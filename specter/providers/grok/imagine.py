"""Grok Imagine - Direct image/video generation via grok.com/imagine."""

import asyncio
import json
import re
import time
from io import BytesIO

from PIL import Image

from ...core.browser import (
    ProgressTracker,
    close_browser,
    get_service_lock,
    handle_browser_error,
    launch_browser,
    log,
    update_preview,
)
from ...core.session import handle_login_flow, is_logged_in
from .chat import AGE_VERIFICATION_INIT_SCRIPT

# Size presets: name -> (aspect_ratio, t2i_resolution)
# Edit resolution varies based on input image
SIZES = {
    "1:1 Square (960x960)": ([1, 1], "960x960"),
    "2:3 Portrait (784x1168)": ([2, 3], "784x1168"),
    "3:2 Landscape (1168x784)": ([3, 2], "1168x784"),
    "9:16 Vertical (720x1280)": ([9, 16], "720x1280"),
    "16:9 Widescreen (1280x720)": ([16, 9], "1280x720"),
}

LOGIN_SELECTORS = [
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'a[href*="/sign-in"]',
]


def _calc_viewport(size: str, max_images: int) -> dict:
    """Calculate viewport to fit exactly max_images in single column."""
    _, res = SIZES.get(size, ([1, 1], "960x960"))
    w, h = map(int, res.split("x"))

    # Force single column with narrow width (below grid breakpoint)
    vp_width = 500

    # Height = N images (scaled to fit width) + header
    # Images scale to fit viewport width, so calculate scaled height
    scale = (vp_width - 40) / w  # 40px for padding
    scaled_h = int(h * scale)
    vp_height = (scaled_h * max_images) + 150  # 150 for header/input

    return {"width": vp_width, "height": vp_height}


async def _setup_browser(size: str, video: bool, mode: str | None = None, viewport: dict | None = None):
    """Launch browser with localStorage preset and request gate.

    Returns (playwright, context, page, lock, unblock) - call unblock() before submitting.
    """
    lock = get_service_lock("grok")
    await lock.acquire()

    ar, _ = SIZES.get(size, ([1, 1], "960x960"))
    store = {"state": {"imagineMode": "video" if video else "image", "aspectRatio": ar}}

    # Video mode localStorage (only relevant for video)
    ls_mode = "spicy" if mode == "spicy" else "fun"
    video_mode_script = f"localStorage.setItem('grok-video-mode', '\"{ls_mode}\"');" if video else ""

    init_script = f"""
        localStorage.setItem('useImagineModeStore', {json.dumps(json.dumps(store))});
        {video_mode_script}
        {AGE_VERIFICATION_INIT_SCRIPT}
    """

    vp = viewport or {"width": 767, "height": 800}
    playwright, context, page, _ = await launch_browser("grok", viewport=vp, headless=None)
    await page.add_init_script(init_script)

    # Set up request gate BEFORE navigation to catch any auto-fired requests
    unblock = await _setup_request_gate(page, mode=mode if video else None)

    await page.goto("https://grok.com/imagine", timeout=60000)

    if not await is_logged_in(page, LOGIN_SELECTORS):
        session = await handle_login_flow(page, "grok", "specter-grok-login-required", LOGIN_SELECTORS)
        if "cookies" in session:
            await page.context.add_cookies(session["cookies"])
        await page.goto("https://grok.com/imagine", timeout=60000)

    await page.wait_for_selector('div[contenteditable="true"]', timeout=30000)
    return playwright, context, page, lock, unblock


def _log_image_dimensions(images: list[bytes], expected: str | None = None) -> None:
    """Log dimensions and size of captured images."""
    for i, img_data in enumerate(images):
        try:
            img = Image.open(BytesIO(img_data))
            size_kb = len(img_data) // 1024
            actual = f"{img.width}x{img.height}"
            if expected:
                log(f"Image {i + 1}: {actual}, {size_kb}KB (requested: {expected})", "○")
            else:
                log(f"Image {i + 1}: {actual}, {size_kb}KB", "○")
        except Exception:
            pass


async def _extract_images_from_dom(page, min_width: int = 500, min_size: int = 80000) -> tuple[list[bytes], str]:
    """Extract images from DOM - both base64 and assets.grok.com URLs. Returns (images, stats_str)."""
    import base64

    # Get both base64 data URLs and assets.grok.com generated image URLs
    img_data = await page.evaluate("""
        () => {
            const results = {base64: [], urls: []};
            document.querySelectorAll('img').forEach(img => {
                const src = img.src;
                if (src.startsWith('data:image') && src.length > 1000) {
                    results.base64.push(src);
                } else if (src.includes('assets.grok.com') && src.includes('/generated/')) {
                    results.urls.push(src);
                }
            });
            return results;
        }
    """)

    images = []
    seen = set()
    all_sizes = []

    # Process base64 images (t2i)
    for url in img_data.get("base64", []):
        if ";base64," not in url:
            continue
        b64 = url.split(";base64,")[1]
        data = base64.b64decode(b64)
        size_kb = len(data) // 1024
        try:
            img = Image.open(BytesIO(data))
            all_sizes.append(f"{img.width}x{img.height}:{size_kb}KB")
            if len(data) < min_size or img.width < min_width:
                continue
        except Exception:
            continue
        h = hash(data)
        if h not in seen:
            seen.add(h)
            images.append(data)

    # Process URL images (edit) - fetch using browser context for auth
    for url in img_data.get("urls", []):
        try:
            resp = await page.request.get(url, timeout=10000)
            if resp.status != 200:
                continue
            data = await resp.body()
            size_kb = len(data) // 1024
            img = Image.open(BytesIO(data))
            all_sizes.append(f"{img.width}x{img.height}:{size_kb}KB(url)")
            if len(data) < min_size or img.width < min_width:
                continue
            h = hash(data)
            if h not in seen:
                seen.add(h)
                images.append(data)
        except Exception:
            continue

    total = len(img_data.get("base64", [])) + len(img_data.get("urls", []))
    stats = f"{total} imgs [{', '.join(all_sizes[:6])}] valid: {len(images)}"
    return images, stats


async def _wait_for_images(page, progress: ProgressTracker, max_images: int = 1, timeout: int = 30) -> list[bytes]:
    """Wait for images to appear in DOM, return up to max_images."""
    start = time.time()
    last_log = 0
    last_preview = 0.0

    while time.time() - start < timeout:
        images, stats = await _extract_images_from_dom(page)
        if len(images) >= max_images:
            await asyncio.sleep(2)  # Let remaining images load
            final, _ = await _extract_images_from_dom(page)
            return final[:max_images]
        await asyncio.sleep(0.5)
        elapsed = time.time() - start

        # Preview every 3 seconds
        if progress.preview and elapsed - last_preview >= 3:
            preview_img = await update_preview(page, height=800)
            if preview_img:
                progress.update(progress.current, preview_img)
            last_preview = elapsed

        # Log every 5 seconds
        if int(elapsed) >= last_log + 5:
            log(f"{int(elapsed)}s | {stats}", "◌")
            last_log = int(elapsed)

    images, _ = await _extract_images_from_dom(page)
    return images[:max_images]


async def _wait_for_video(page, progress: ProgressTracker, captured: list, timeout: int = 120) -> bytes | None:
    """Wait for video to be captured via network interception."""
    start = time.time()
    last_log = 0
    last_preview = 0.0

    while time.time() - start < timeout:
        if captured:
            return captured[-1]
        await asyncio.sleep(1)
        elapsed = time.time() - start

        if progress.preview and elapsed - last_preview >= 3:
            preview_img = await update_preview(page, height=800)
            if preview_img:
                progress.update(progress.current, preview_img)
            last_preview = elapsed

        if int(elapsed) >= last_log + 10:
            log(f"{int(elapsed)}s | waiting for video...", "◌")
            last_log = int(elapsed)

        progress.update(min(40 + int(elapsed // 2), 85))

    return captured[-1] if captured else None


def _setup_video_capture(page) -> list[bytes]:
    """Set up video capture handler. Returns list that will be populated with captured videos."""
    captured: list[bytes] = []

    async def on_response(response):
        if "assets.grok.com" in response.url and ".mp4" in response.url:
            try:
                body = await response.body()
                if len(body) > 10000:
                    captured.append(body)
                    log(f"Captured video ({len(body) // 1024}KB)", "✓")
            except Exception:
                pass

    page.on("response", on_response)
    return captured


VIDEO_MODES = {
    "normal": "normal",
    "custom": "custom",
    "fun": "extremely-crazy",
    "spicy": "extremely-spicy-or-crazy",
}


async def _setup_request_gate(page, mode: str | None = None):
    """Block conversations/new until ready. Only allows ONE request after unlock.

    Returns unblock() function to call before submitting.
    """
    state = {"ready": False, "allowed": False}
    api_mode = VIDEO_MODES.get(mode, "custom") if mode else None

    async def on_route(route):
        request = route.request

        # Block until ready, or if we already allowed one request
        if not state["ready"] or state["allowed"]:
            log(f"Blocked: {request.url[-50:]}", "✕")
            await route.abort()
            return

        # Allow exactly one request
        state["allowed"] = True
        log(f"Allowed: {request.url[-50:]}", "→")

        # Non-POST requests pass through
        if request.method != "POST":
            await route.continue_()
            return

        # Modify request body
        try:
            body = json.loads(request.post_data)

            # Disable side-by-side feedback dialog
            body["enableSideBySide"] = False

            # Video mode injection
            if api_mode:
                original_msg = body.get("message", "")
                msg = re.sub(r"\s*--mode=\S+", "", original_msg)
                msg = f"{msg.rstrip()}  --mode={api_mode}"
                body["message"] = msg
                log(f"Injected mode: {api_mode}", "→")

            await route.continue_(post_data=json.dumps(body))
        except Exception:
            await route.continue_()

    await page.route("**/rest/app-chat/conversations/new", on_route)
    log("Request gate installed", "○")

    def unblock():
        state["ready"] = True
        log("Request gate unlocked", "✓")

    return unblock


def _log_prompt(prompt: str) -> None:
    """Log prompt with truncation."""
    log(f"Prompt: {prompt[:60]}..." if len(prompt) > 60 else f"Prompt: {prompt}", "✎")


async def _upload_image(
    page, image_path: str, file_selector: str = 'input[type="file"]', wait_for_url: str | None = None
) -> None:
    """Upload image and wait for completion.

    Args:
        page: Playwright page
        image_path: Path to image file
        file_selector: CSS selector for file input
        wait_for_url: Optional URL pattern to wait for after upload (e.g. "**/imagine/post/*")
    """
    file_input = page.locator(file_selector).first
    await file_input.wait_for(state="attached", timeout=10000)
    await file_input.set_input_files(image_path)
    log("File attached, waiting for upload...", "↑")

    if wait_for_url:
        await page.wait_for_url(wait_for_url, timeout=30000)

    log("Image uploaded", "✓")


async def imagine_t2i(
    prompt: str,
    size: str = "Square (960x960)",
    max_images: int = 1,
    pbar=None,
    preview: bool = False,
) -> list[bytes]:
    """Text-to-image generation."""
    progress = ProgressTracker(pbar, preview)
    progress.update(10)

    viewport = _calc_viewport(size, max_images)
    playwright, context, page, lock, unblock = await _setup_browser(size, video=False, viewport=viewport)

    try:
        _, expected_res = SIZES.get(size, ([1, 1], "960x960"))
        log(f"Settings: {max_images} images, {expected_res}, viewport {viewport['width']}x{viewport['height']}", "○")
        _log_prompt(prompt)
        progress.update(30)

        await page.keyboard.insert_text(prompt)
        unblock()
        await page.keyboard.press("Enter")
        progress.update(40)

        images = await _wait_for_images(page, progress, max_images=max_images)
        if images:
            log(f"Generation complete ({len(images)} images)", "✓")
        else:
            log("Timeout waiting for images", "⚠")
            await handle_browser_error(page, Exception("Timeout"), "grok-imagine-t2i-timeout")

        _log_image_dimensions(images, expected_res)
        progress.update(100)
        return images

    except Exception as e:
        log(f"Error: {e}", "✕")
        await handle_browser_error(page, e, "grok-imagine-t2i")
        raise
    finally:
        await close_browser(playwright, context)
        lock.release()


async def imagine_edit(
    prompt: str,
    image_path: str,
    max_images: int = 1,
    pbar=None,
    preview: bool = False,
) -> list[bytes]:
    """Image-to-image editing. Output size follows input image dimensions."""
    progress = ProgressTracker(pbar, preview)
    progress.update(10)

    # Edit doesn't support size - output follows input dimensions
    playwright, context, page, lock, unblock = await _setup_browser("1:1 Square (960x960)", video=False)

    try:
        log(f"Settings: {max_images} images", "○")
        log(f"Uploading: {image_path}", "↑")
        progress.update(20)

        await _upload_image(page, image_path, file_selector='input[type="file"][accept*="image"]')

        # Wait for Edit image button
        edit_btn = page.locator('button:has-text("Edit image")').first
        await edit_btn.wait_for(state="visible", timeout=30000)
        await edit_btn.click()
        log("Edit image clicked", "✓")
        progress.update(30)

        edit_prompt = prompt or "Edit this image"
        _log_prompt(edit_prompt)
        textarea = page.locator('textarea[placeholder*="edit image" i]')
        await textarea.fill(edit_prompt)
        unblock()
        await textarea.press("Enter")
        progress.update(40)

        images = await _wait_for_images(page, progress, max_images=max_images)
        if images:
            log(f"Edit complete ({len(images)} images)", "✓")
        else:
            log("Timeout waiting for images", "⚠")
            await handle_browser_error(page, Exception("Timeout"), "grok-imagine-edit-timeout")

        _log_image_dimensions(images)
        progress.update(100)
        return images

    except Exception as e:
        log(f"Error: {e}", "✕")
        await handle_browser_error(page, e, "grok-imagine-edit")
        raise
    finally:
        await close_browser(playwright, context)
        lock.release()


async def imagine_t2v(
    prompt: str,
    size: str = "1:1 Square (960x960)",
    mode: str = "custom",
    pbar=None,
    preview: bool = False,
) -> bytes | None:
    """Text-to-video generation."""
    progress = ProgressTracker(pbar, preview)
    progress.update(10)

    playwright, context, page, lock, unblock = await _setup_browser(size, video=True, mode=mode)

    try:
        captured = _setup_video_capture(page)

        _, expected_res = SIZES.get(size, ([1, 1], "960x960"))
        log(f"Settings: {expected_res}, mode={mode}", "○")
        _log_prompt(prompt)
        progress.update(30)

        await page.keyboard.insert_text(prompt)
        unblock()
        await page.keyboard.press("Enter")
        progress.update(40)

        video = await _wait_for_video(page, progress, captured)
        if video:
            log(f"Video complete ({len(video) // 1024}KB, requested: {expected_res})", "✓")
            progress.update(100)
            return video

        log("Timeout waiting for video", "⚠")
        await handle_browser_error(page, Exception("Timeout"), "grok-imagine-t2v-timeout")
        return None

    except Exception as e:
        log(f"Error: {e}", "✕")
        await handle_browser_error(page, e, "grok-imagine-t2v")
        raise
    finally:
        await close_browser(playwright, context)
        lock.release()


async def imagine_i2v(
    image_path: str,
    prompt: str = "",
    mode: str = "custom",
    pbar=None,
    preview: bool = False,
) -> bytes | None:
    """Image-to-video generation. Output size follows input image."""
    progress = ProgressTracker(pbar, preview)
    progress.update(10)

    # i2v doesn't use size - output follows input image dimensions
    playwright, context, page, lock, unblock = await _setup_browser("1:1 Square (960x960)", video=True, mode=mode)

    try:
        captured = _setup_video_capture(page)

        log(f"Settings: mode={mode} (size follows input image)", "○")
        log(f"Uploading: {image_path}", "↑")
        progress.update(20)

        await _upload_image(page, image_path, wait_for_url="**/imagine/post/*")
        progress.update(30)

        textarea = page.locator('textarea[placeholder*="customize video"]')
        await textarea.wait_for(state="visible", timeout=10000)

        if prompt:
            await textarea.fill(prompt)
            _log_prompt(prompt)
        unblock()
        await textarea.press("Enter")
        log("Video generation started", "✓")
        progress.update(40)

        video = await _wait_for_video(page, progress, captured)
        if video:
            log(f"Video complete ({len(video) // 1024}KB)", "✓")
            progress.update(100)
            return video

        log("Timeout waiting for video", "⚠")
        await handle_browser_error(page, Exception("Timeout"), "grok-imagine-i2v-timeout")
        return None

    except Exception as e:
        log(f"Error: {e}", "✕")
        await handle_browser_error(page, e, "grok-imagine-i2v")
        raise
    finally:
        await close_browser(playwright, context)
        lock.release()
