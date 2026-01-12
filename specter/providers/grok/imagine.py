"""Grok Imagine - API-based image/video generation (edit, t2v, i2v)."""

import asyncio
import json
import re
import time
from io import BytesIO

from PIL import Image

from ...core.browser import (
    BrowserSession,
    ProgressTracker,
    debug_log,
    log,
)
from ...core.session import handle_login_flow
from .chat import AGE_VERIFICATION_INIT_SCRIPT

GROK_LOGIN_EVENT = "specter-grok-login-required"

# Size presets: name -> (aspect_ratio, t2i_resolution)
SIZES = {
    "1:1 Square (960x960)": ([1, 1], "960x960"),
    "2:3 Portrait (784x1168)": ([2, 3], "784x1168"),
    "3:2 Landscape (1168x784)": ([3, 2], "1168x784"),
    "9:16 Vertical (720x1280)": ([9, 16], "720x1280"),
    "16:9 Widescreen (1280x720)": ([16, 9], "1280x720"),
}

# Selector that only appears on the landing page (not logged in)
LANDING_PAGE_SELECTOR = 'text="Introducing the fastest image and video generation experience"'

VIDEO_MODES = {
    "normal": "normal",
    "custom": "custom",
    "fun": "extremely-crazy",
    "spicy": "extremely-spicy-or-crazy",
}

# Error detection (rate limit, moderation) - checks toasts and overlay messages
ERROR_CHECK_JS = """() => {
    // Check toasts
    const toasts = Array.from(document.querySelectorAll('li.toast'));
    for (const toast of toasts) {
        const text = toast.textContent;
        if (text.includes('Rate limit reached')) {
            return { error: 'rate_limit', message: text };
        }
        if (text.includes('Content Moderated')) {
            return { error: 'moderated', message: text };
        }
    }
    // Check for moderation overlay on image/video (appears as text on the preview)
    const pageText = document.body.innerText;
    if (pageText.includes('Content Moderated')) {
        return { error: 'moderated', message: 'Content Moderated. Try a different idea.' };
    }
    return null;
}"""


async def _check_errors(page):
    """Check for errors (rate limit, moderation) and raise if detected."""
    result = await page.evaluate(ERROR_CHECK_JS)
    if result:
        if result["error"] == "rate_limit":
            raise RuntimeError("Grok rate limit reached. Upgrade your account or wait before generating more images.")
        elif result["error"] == "moderated":
            raise RuntimeError(f"Content moderated by Grok: {result['message']}")


async def _setup_imagine_page(
    browser: BrowserSession, size: str, video: bool, mode: str | None = None
) -> tuple:
    """Set up page for Grok Imagine.

    Args:
        browser: BrowserSession instance (handles login flow if needed)

    Returns:
        (unblock_fn, gate_state) - call unblock() right before submitting.
    """
    ar, _ = SIZES.get(size, ([1, 1], "960x960"))
    store = {"state": {"imagineMode": "video" if video else "image", "aspectRatio": ar}}

    ls_mode = "spicy" if mode == "spicy" else "fun"
    video_mode_script = f"localStorage.setItem('grok-video-mode', '\"{ls_mode}\"');" if video else ""

    init_script = f"""
        localStorage.setItem('useImagineModeStore', {json.dumps(json.dumps(store))});
        {video_mode_script}
        {AGE_VERIFICATION_INIT_SCRIPT}
    """

    async def _navigate_and_setup():
        """Navigate and set up page, returns gate_result."""
        page = browser.page
        await page.add_init_script(init_script)

        gate_result = await _setup_request_gate(page, mode=mode if video else None, allow_video=video)

        await page.goto("https://grok.com/imagine", timeout=60000, wait_until="domcontentloaded")

        # Hide text selection highlight (prevents visual artifacts in screenshots)
        await page.evaluate("""() => {
            const style = document.createElement('style');
            style.textContent = '::selection { background: transparent !important; }';
            document.head.appendChild(style);
        }""")

        return gate_result

    gate_result = await _navigate_and_setup()
    page = browser.page

    # Wait for either editor (logged in) or landing page (not logged in)
    editor_selector = ".tiptap.ProseMirror"
    try:
        await page.wait_for_selector(
            f"{editor_selector}, {LANDING_PAGE_SELECTOR}",
            timeout=30000,
        )
    except Exception:
        pass

    # Check if we're on the landing page (not logged in) - trigger login popup
    if await page.locator(LANDING_PAGE_SELECTOR).count() > 0:
        log("Not logged in - opening authentication popup...", "⚠")
        await browser.close()
        await handle_login_flow(None, "grok", GROK_LOGIN_EVENT, [])
        await browser.start()
        gate_result = await _navigate_and_setup()
        page = browser.page

        # Re-check after login
        try:
            await page.wait_for_selector(editor_selector, timeout=30000)
        except Exception:
            raise RuntimeError("Login failed. Please try again via ComfyUI Settings > Specter > Grok.") from None

    # Wait for editor to be fully hydrated
    await page.wait_for_function(
        """() => {
            const editor = document.querySelector('.tiptap');
            return editor?.isContentEditable && editor.classList.contains('ProseMirror');
        }""",
        timeout=10000,
    )

    return gate_result


async def _setup_request_gate(page, mode: str | None = None, allow_video: bool = True):
    """Block all generation requests until unblock() is called.

    Args:
        mode: Video mode to inject (normal/custom/fun/spicy)
        allow_video: If False, always block videoGen requests (for image edit)

    Returns (unblock_fn, state) - call unblock() right before submitting.
    """
    state = {"ready": False, "allowed": False}
    api_mode = VIDEO_MODES.get(mode, "custom") if mode else None

    async def on_route(route):
        request = route.request
        if request.method != "POST":
            await route.continue_()
            return

        try:
            body = json.loads(request.post_data)
            is_video_request = body.get("toolOverrides", {}).get("videoGen")

            # Block ALL requests until ready
            if not state["ready"]:
                debug_log(f"Blocked request (not ready): {json.dumps(body, indent=2)}")
                log("Blocked premature request", "✕")
                await route.abort()
                return

            # Always block videoGen if not allowed (image edit mode)
            if is_video_request and not allow_video:
                debug_log(f"Blocked videoGen request: {json.dumps(body, indent=2)}")
                log("Blocked videoGen request", "✕")
                await route.abort()
                return

            # Only allow ONE request - block any subsequent ones
            if state["allowed"]:
                debug_log(f"Blocked duplicate request: {json.dumps(body, indent=2)}")
                log("Blocked duplicate request", "✕")
                await route.abort()
                return

            # Allow and intercept
            state["allowed"] = True
            body["enableSideBySide"] = False

            if api_mode:
                original_msg = body.get("message", "")
                msg = re.sub(r"\s*--mode=\S+", "", original_msg)
                body["message"] = f"{msg.rstrip()}  --mode={api_mode}"

            debug_log(f"Allowed request: {json.dumps(body, indent=2)}")
            log("Allowed request", "✓")
            await route.continue_(post_data=json.dumps(body))
        except Exception:
            await route.continue_()

    await page.route("**/rest/app-chat/conversations/new", on_route)
    log("Request gate installed", "○")

    def unblock():
        state["ready"] = True

    return unblock, state


async def _upload_image(page, image_path: str, upload_state: dict, file_selector: str = 'input[type="file"]') -> None:
    """Upload image and wait for API completion."""
    file_input = page.locator(file_selector).first
    await file_input.wait_for(state="attached", timeout=10000)
    await file_input.set_input_files(image_path)
    log("File attached, waiting for upload...", "↑")

    upload_start = time.time()
    while time.time() - upload_start < 30:
        if upload_state["complete"]:
            upload_state["complete"] = None
            log("Image uploaded", "✓")
            return
        await asyncio.sleep(0.1)

    log("Upload timeout - proceeding anyway", "⚠")


async def _verify_request_sent(gate_state: dict, error_msg: str, timeout: int = 3) -> None:
    """Verify request was sent within timeout."""
    for _ in range(timeout * 10):
        if gate_state["allowed"]:
            return
        await asyncio.sleep(0.1)
    log(f"No request sent - {error_msg}", "✕")
    raise Exception(f"No request sent ({error_msg})")


def _log_image_info(data: bytes, prefix: str = "Extracted") -> None:
    """Log image dimensions and size."""
    img = Image.open(BytesIO(data))
    size_kb = len(data) // 1024
    log(f"{prefix}: {img.width}x{img.height}, {size_kb}KB", "○")


def _setup_response_tracking(page, mode: str, max_images: int = 1):
    """Set up unified response tracking for uploads, API responses, and video downloads.

    NOTE: Playwright's page.on("response") fires ONCE when response completes, not progressively.
    All API progress chunks arrive together at the end.

    Args:
        mode: "video", "image", or "both"
        max_images: For image mode - number of images to track

    Returns:
        (captured_videos, video_complete, image_complete, upload_state)
    """
    captured_videos: list[bytes] = []
    upload_state = {"complete": None}
    video_complete = {"done": False}
    image_complete = {"done": False, "urls": []}

    async def on_response(response):
        url = response.url

        # Track uploads
        if "/rest/app-chat/upload-file" in url:
            try:
                body = await response.json()
                if "fileMetadataId" in body:
                    upload_state["complete"] = body
                    log(f"Upload complete: {body['fileMetadataId']}", "✓")
            except:
                pass
            return

        # Track video downloads
        if mode in ("video", "both") and "assets.grok.com" in url and ".mp4" in url:
            try:
                body = await response.body()
                if len(body) > 10000:
                    captured_videos.append(body)
                    log(f"Captured video ({len(body) // 1024}KB)", "✓")
            except:
                pass
            return

        # Parse API response for completion
        if "/rest/app-chat/conversations/new" in url and response.status == 200:
            try:
                text = await response.text()
                for line in text.split("\n"):
                    if not line.strip():
                        continue
                    try:
                        body = json.loads(line)
                        resp = body.get("result", {}).get("response", {})

                        # Check video completion
                        if mode in ("video", "both"):
                            video_gen = resp.get("streamingVideoGenerationResponse", {})
                            if video_gen and video_gen.get("progress") == 100:
                                video_complete["done"] = True

                        # Check image completion
                        if mode == "image":
                            img_gen = resp.get("streamingImageGenerationResponse", {})
                            if img_gen and img_gen.get("progress") == 100:
                                url = img_gen.get("imageUrl", "")
                                if url and url not in image_complete["urls"]:
                                    image_complete["urls"].append(url)
                                if len(image_complete["urls"]) >= max_images:
                                    image_complete["done"] = True
                    except json.JSONDecodeError:
                        pass
            except:
                pass

    page.on("response", on_response)
    return captured_videos, video_complete, image_complete, upload_state


async def _wait_for_video(captured: list[bytes], browser, progress: ProgressTracker, timeout: int = 60) -> bytes:
    """Wait for video to be captured via network interception (timeout in seconds)."""
    start = time.time()
    last_preview = 0
    last_error_check = 0

    while time.time() - start < timeout:
        if captured:
            return captured[-1]

        elapsed = time.time() - start

        # Check for errors (moderation, rate limit) every 3 seconds
        if elapsed - last_error_check >= 3:
            await _check_errors(browser.page)
            last_error_check = elapsed

        if elapsed - last_preview >= 3:
            await browser.update_preview_if_enabled(progress)
            last_preview = elapsed

        await asyncio.sleep(1)

    raise Exception("Timeout waiting for video")


async def _wait_for_images(image_state: dict, browser, progress: ProgressTracker, timeout: int = 40) -> list[str]:
    """Wait for images via API response (timeout in seconds)."""
    start = time.time()
    last_preview = 0
    last_error_check = 0

    while time.time() - start < timeout:
        if image_state["done"]:
            return image_state["urls"]

        elapsed = time.time() - start

        # Check for errors (moderation, rate limit) every 3 seconds
        if elapsed - last_error_check >= 3:
            await _check_errors(browser.page)
            last_error_check = elapsed

        if elapsed - last_preview >= 3:
            await browser.update_preview_if_enabled(progress)
            last_preview = elapsed

        await asyncio.sleep(1)

    raise Exception("Timeout waiting for images")


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

    async with BrowserSession("grok", error_context="grok-imagine-edit") as browser:
        unblock, gate_state = await _setup_imagine_page(browser, "1:1 Square (960x960)", video=False)
        _, _, image_state, upload_state = _setup_response_tracking(browser.page, mode="image", max_images=max_images)

        log(f"Settings: {max_images} images", "○")
        log(f"Uploading: {image_path}", "↑")
        progress.update(20)

        await _upload_image(browser.page, image_path, upload_state, file_selector='input[type="file"][accept*="image"]')

        edit_btn = browser.page.locator('button:has-text("Edit image")').first
        await edit_btn.wait_for(state="visible", timeout=30000)
        await edit_btn.click()
        log("Edit image clicked", "✓")
        progress.update(30)

        edit_prompt = prompt or "Edit this image"
        log(f"Prompt: {edit_prompt[:60]}..." if len(edit_prompt) > 60 else f"Prompt: {edit_prompt}", "✎")
        textarea = browser.page.locator('textarea[placeholder*="edit image" i]')
        # Fill prompt BEFORE unblock to prevent premature request
        await textarea.fill(edit_prompt)
        unblock()
        await textarea.press("Enter")

        await _verify_request_sent(gate_state, "textarea not found or Enter failed")
        log("Edit request sent", "✓")
        progress.update(40)

        await _check_errors(browser.page)

        # Wait for images from API
        image_urls = await _wait_for_images(image_state, browser, progress, timeout=40)
        progress.update(90)

        # Download images from URLs (respect max_images limit)
        images = []
        urls_to_download = image_urls[:max_images]
        for idx, url in enumerate(urls_to_download):
            # Prepend base URL if relative
            full_url = f"https://assets.grok.com/{url}" if not url.startswith("http") else url
            try:
                resp = await browser.page.request.get(full_url, timeout=10000)
                if resp.status == 200:
                    img_data = await resp.body()
                    _log_image_info(img_data, f"Image {idx + 1}")
                    images.append(img_data)
            except Exception as e:
                log(f"Failed to download image {idx + 1}: {e}", "✕")
                continue

        if not images:
            raise Exception("Failed to download images from API URLs")

        log(f"Edit complete ({len(images)} images)", "✓")
        for i, img_data in enumerate(images):
            try:
                _log_image_info(img_data, f"Image {i + 1}")
            except Exception:
                pass
        progress.update(100)
        return images


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

    async with BrowserSession("grok", error_context="grok-imagine-t2v") as browser:
        unblock, gate_state = await _setup_imagine_page(browser, size, video=True, mode=mode)
        captured, _, _, _ = _setup_response_tracking(browser.page, mode="video")

        _, expected_res = SIZES.get(size, ([1, 1], "960x960"))
        log(f"Settings: {expected_res}, mode={mode}", "○")
        log(f"Prompt: {prompt[:60]}..." if len(prompt) > 60 else f"Prompt: {prompt}", "✎")
        progress.update(30)

        # Type prompt BEFORE unblock to prevent premature request
        await browser.page.keyboard.insert_text(prompt)
        unblock()
        await browser.page.keyboard.press("Enter")

        await _verify_request_sent(gate_state, "textarea not found or Enter failed")
        log("Video generation started", "✓")
        progress.update(40)

        video = await _wait_for_video(captured, browser, progress)
        size_kb = len(video) // 1024
        log(f"Video complete ({size_kb}KB, requested: {expected_res})", "✓")
        progress.update(100)
        return video


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

    async with BrowserSession("grok", error_context="grok-imagine-i2v") as browser:
        unblock, gate_state = await _setup_imagine_page(
            browser, "1:1 Square (960x960)", video=True, mode=mode
        )
        captured, _, _, upload_state = _setup_response_tracking(browser.page, mode="both")

        log(f"Settings: mode={mode} (size follows input image)", "○")
        log(f"Uploading: {image_path}", "↑")
        progress.update(20)

        await _upload_image(browser.page, image_path, upload_state)
        progress.update(30)

        textarea = browser.page.locator('textarea[placeholder*="customize video"]')
        await textarea.wait_for(state="visible", timeout=10000)

        # Fill prompt BEFORE unblock to prevent Grok auto-firing without our prompt
        if prompt:
            await textarea.fill(prompt)
            log(f"Prompt: {prompt[:60]}..." if len(prompt) > 60 else f"Prompt: {prompt}", "✎")
        unblock()
        await textarea.press("Enter")

        await _verify_request_sent(gate_state, "textarea not found or Enter failed")
        log("Video generation started", "✓")
        progress.update(40)

        video = await _wait_for_video(captured, browser, progress)
        size_kb = len(video) // 1024
        log(f"Video complete ({size_kb}KB)", "✓")
        progress.update(100)
        return video
