"""Google Flow Text-to-Image - Hybrid DOM + request interception."""

import asyncio
import base64
import json

from ..core.browser import (
    ProgressTracker,
    capture_preview,
    close_browser,
    debug_log,
    launch_browser,
    log,
)

FLOW_URL = "https://labs.google/fx/tools/flow"

# Error detection for Flow (policy violations, failures, rate limits)
ERROR_CHECK_JS = """() => {
    const text = document.body.innerText;
    if (text.includes('reached the daily limit') || text.includes('daily limit for')) {
        return { error: 'rate_limit', message: 'Daily generation limit reached' };
    }
    if (text.includes('might violate our') || text.includes('violate our policies')) {
        return { error: 'policy', message: 'Generation might violate policies' };
    }
    if (text.includes('Something went wrong')) {
        return { error: 'failed', message: 'Something went wrong' };
    }
    return null;
}"""


class FlowGenerationError(Exception):
    """Raised when Flow generation fails (policy violation, error) - retriable."""

    pass


class FlowRateLimitError(Exception):
    """Raised when daily limit reached - not retriable."""

    pass


async def _check_errors(page) -> None:
    """Check for errors and raise appropriate exception."""
    result = await page.evaluate(ERROR_CHECK_JS)
    if result:
        if result["error"] == "rate_limit":
            raise FlowRateLimitError(result["message"])
        raise FlowGenerationError(result["message"])


# Model ID → API model name
MODELS = {
    "imagen-4": "IMAGEN_3_5",
    "nano-banana": "GEM_PIX",
    "nano-banana-pro": "GEM_PIX_2",
}

# Aspect ratio ID → API enum
ASPECT_RATIOS = {
    "9:16 (768x1376)": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "16:9 (1376x768)": "IMAGE_ASPECT_RATIO_LANDSCAPE",
}


async def _upsample_via_ui(page, img_index: int) -> tuple[bytes | None, str | None]:
    """Upsample image via Download > 2K menu. Returns (data, error)."""
    from pathlib import Path

    try:
        # Click download button for this image
        download_btn = page.get_by_role("button", name="download Download").nth(img_index)
        await download_btn.click()
        await asyncio.sleep(0.3)

        # Click 2K download option and capture the download
        async with page.expect_download(timeout=60000) as download_info:
            await page.get_by_role("menuitem", name="2K Download 2K").click()

        download = await download_info.value
        download_path = await download.path()

        if download_path:
            data = Path(download_path).read_bytes()
            debug_log(f"Downloaded 2K image: {len(data) // 1024}KB")
            return data, None

        return None, "Download path not available"

    except Exception as e:
        debug_log(f"Upsample error: {e}")
        return None, str(e)


async def imagine_t2i(
    prompt: str,
    model: str = "nano-banana-pro",
    aspect_ratio: str = "16:9 (1376x768)",
    num_outputs: int = 1,
    seed: int = 42,
    upscale: bool = False,
    pbar=None,
    preview: bool = False,
    max_retries: int = 3,
) -> list[bytes]:
    """Text-to-image generation via Google Flow with request interception."""
    import random

    progress = ProgressTracker(pbar, preview)
    api_model = MODELS.get(model, MODELS["nano-banana-pro"])
    api_ratio = ASPECT_RATIOS.get(aspect_ratio, ASPECT_RATIOS["16:9 (1376x768)"])
    num_outputs = max(1, min(4, num_outputs))

    last_error = None
    for attempt in range(1, max_retries + 1):
        # New seed each attempt (unless fixed)
        base_seed = min(seed, 999999) if seed > 0 else random.randint(100000, 999999)

        if attempt > 1:
            log(f"Retry {attempt}/{max_retries} with seed={base_seed}", "↻")
        progress.update(5)

        log(f"Flow T2I: {model} ({api_model}), {aspect_ratio}, {num_outputs} output(s), seed={base_seed}", "●")
        if upscale:
            log("Upscaling to 2K enabled", "↑")
        progress.update(10)

        playwright, context, page, _ = await launch_browser("flow")

        try:
            result = await _t2i_attempt(
                page, prompt, api_model, api_ratio, num_outputs, base_seed, upscale, progress, preview, pbar
            )
            return result
        except FlowRateLimitError:
            await close_browser(playwright, context)
            raise  # Don't retry rate limits
        except FlowGenerationError as e:
            last_error = e
            log(f"Generation failed: {e}", "✕")
        finally:
            await close_browser(playwright, context)

    raise Exception(f"Failed after {max_retries} attempts: {last_error}")


async def _t2i_attempt(
    page, prompt: str, api_model: str, api_ratio: str, num_outputs: int, base_seed: int, upscale: bool, progress, preview: bool, pbar
) -> list[bytes]:
    """Single T2I generation attempt."""

    # Set up request interception BEFORE navigating
    async def intercept_request(route):
        request = route.request
        url = request.url

        # Intercept the image generation API
        if "flowMedia:batchGenerateImages" in url:
            try:
                body = json.loads(request.post_data or "{}")
                requests_list = body.get("requests", [])

                debug_log(f"Intercepted batchGenerateImages with {len(requests_list)} requests")

                # Modify each request in the batch
                modified_requests = []
                for i, req in enumerate(requests_list):
                    if i >= num_outputs:
                        break  # Limit to num_outputs
                    req["imageModelName"] = api_model
                    req["imageAspectRatio"] = api_ratio
                    req["seed"] = base_seed + i  # Sequential seeds
                    modified_requests.append(req)

                # If we need more outputs than provided, duplicate the first
                while len(modified_requests) < num_outputs and requests_list:
                    new_req = json.loads(json.dumps(requests_list[0]))
                    new_req["seed"] = base_seed + len(modified_requests)
                    new_req["imageModelName"] = api_model
                    new_req["imageAspectRatio"] = api_ratio
                    modified_requests.append(new_req)

                body["requests"] = modified_requests

                seeds = [r.get("seed") for r in modified_requests]
                log(f"Modified request: {api_model}, {api_ratio}, {len(modified_requests)} images, seeds={seeds}", "→")
                debug_log(f"Full modified requests: {json.dumps(modified_requests, indent=2)}")
                await route.continue_(post_data=json.dumps(body))
                return
            except Exception as e:
                debug_log(f"Interception error: {e}")

        await route.continue_()

    await page.route("**/aisandbox-pa.googleapis.com/**", intercept_request)

    # Navigate to Flow
    await page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=60000)
    progress.update(20)

    # Wait for app to load
    new_project_btn = page.get_by_role("button", name="add_2 New project")
    create_btn = page.get_by_role("button", name="Create with Flow")
    await new_project_btn.or_(create_btn).wait_for(timeout=30000)

    # Handle landing page
    if await create_btn.is_visible():
        await create_btn.click()
        await new_project_btn.wait_for(timeout=30000)

    # Start new project
    await new_project_btn.click()
    progress.update(30)

    # Select "Create Image" mode
    mode_dropdown = page.get_by_text("Text to Videoarrow_drop_down")
    await mode_dropdown.wait_for(timeout=10000)
    await mode_dropdown.click()
    await page.get_by_role("option", name="Create Image").click()
    await asyncio.sleep(0.5)
    progress.update(40)

    # Fill prompt (no need to set other options - interception handles it)
    prompt_input = page.get_by_role("textbox", name="Generate an image from text")
    await prompt_input.wait_for(timeout=10000)
    await prompt_input.click()
    await prompt_input.fill(prompt)
    log(f"Prompt: {prompt[:60]}..." if len(prompt) > 60 else f"Prompt: {prompt}", "✎")
    progress.update(50)

    # Click Create
    await page.get_by_role("button", name="arrow_forward Create").click()
    log("Generating...", "◐")

    # Wait for result images with preview during generation
    result_selector = 'img[alt^="Flow Image:"]'
    last_preview_time = 0
    last_error_check = 0
    images_found = False

    for elapsed in range(1, 61):
        await asyncio.sleep(1)

        # Check for errors (policy violation, failures) every 3 seconds
        if elapsed - last_error_check >= 3:
            await _check_errors(page)
            last_error_check = elapsed

        # Preview every 3s during generation
        if preview and elapsed - last_preview_time >= 3:
            preview_img = await capture_preview(page)
            if preview_img:
                pct = 50 + min(elapsed // 2, 40) if not images_found else 70 + min((elapsed - 60) // 2, 20)
                progress.update(pct, preview_img)
            last_preview_time = elapsed

        img_count = await page.locator(result_selector).count()
        if img_count > 0 and not images_found:
            images_found = True
            progress.update(70)

        if img_count >= num_outputs:
            all_loaded = await page.evaluate(
                """(selector) => {
                const imgs = document.querySelectorAll(selector);
                return Array.from(imgs).every(img =>
                    img.complete && img.naturalWidth > 100 &&
                    (img.src.startsWith('data:') || img.src.startsWith('blob:') || img.src.startsWith('http'))
                );
            }""",
                result_selector,
            )
            if all_loaded:
                log(f"All {img_count} images loaded", "✓")
                break

    if not images_found:
        raise TimeoutError("No images generated within timeout")

    progress.update(85 if upscale else 90)

    # Extract images (original resolution)
    images = []
    img_elements = page.locator(result_selector)
    count = await img_elements.count()

    for i in range(min(count, num_outputs)):
        img = img_elements.nth(i)
        src = await img.get_attribute("src")

        if not src:
            log(f"Image {i + 1}: no src attribute", "!")
            continue

        if src.startswith("data:image"):
            b64 = src.split(";base64,")[1]
            data = base64.b64decode(b64)
        elif src.startswith("blob:") or src.startswith("http"):
            response = await page.request.get(src)
            data = await response.body()
        else:
            log(f"Unknown image src format: {src[:50]}", "!")
            continue

        log(f"Image {i + 1}: {len(data) // 1024}KB", "○")
        images.append(data)

    if not images:
        raise Exception("No images captured - generation may have failed")

    # Upscale images if requested (via UI click)
    if upscale:
        log(f"Upscaling {len(images)} images to 2K via UI...", "↑")
        upscaled_images = []

        for i in range(len(images)):
            upscaled, error = await _upsample_via_ui(page, i)
            if upscaled:
                log(f"Image {i + 1} upscaled: {len(upscaled) // 1024}KB", "↑")
                upscaled_images.append(upscaled)
            else:
                log(f"Image {i + 1} upscale failed, using original", "!")
                debug_log(f"Upscale error for image {i + 1}: {error}")
                upscaled_images.append(images[i])

        images = upscaled_images

    log(f"Complete: {len(images)} image(s)", "✓")
    progress.update(100)
    return images
