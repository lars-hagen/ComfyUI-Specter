"""Google Flow Image-to-Video (Frames to Video)."""

import asyncio
import json
from pathlib import Path

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


async def _check_errors(page) -> None:
    """Check for errors and raise if detected."""
    result = await page.evaluate(ERROR_CHECK_JS)
    if result:
        raise RuntimeError(f"Flow error: {result['message']}")


# Model display names (same as T2V)
MODELS = ["veo-3.1-fast", "veo-3.1-quality", "veo-2-fast", "veo-2-quality"]

# Aspect ratios
ASPECT_RATIOS = ["16:9 (Landscape)", "9:16 (Portrait)"]

# Model key lookup: (model, is_portrait) → API model key
MODEL_KEYS = {
    # Veo 3.1 (current, has audio)
    ("veo-3.1-fast", False): "veo_3_1_t2v_fast",
    ("veo-3.1-fast", True): "veo_3_1_t2v_fast_portrait",
    ("veo-3.1-quality", False): "veo_3_1_t2v",
    ("veo-3.1-quality", True): "veo_3_1_t2v_portrait",
    # Veo 2 (no audio)
    ("veo-2-fast", False): "veo_2_1_fast_d_15_t2v",
    ("veo-2-fast", True): "veo_2_1_fast_d_15_t2v",
    ("veo-2-quality", False): "veo_2_0_t2v",
    ("veo-2-quality", True): "veo_2_0_t2v",
}

RATIO_ENUMS = {
    "16:9 (Landscape)": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "9:16 (Portrait)": "VIDEO_ASPECT_RATIO_PORTRAIT",
}


async def _upload_frame(page, image_path: str, is_first: bool, aspect_ratio: str) -> None:
    """Upload a frame image (first or last)."""
    # Click the appropriate add button
    add_btn = page.get_by_role("button", name="add")
    if is_first:
        await add_btn.first.click()
    else:
        await add_btn.click()
    await asyncio.sleep(0.5)

    # Click upload button to open upload dialog
    upload_btn = page.get_by_role("button", name="upload Upload .png, .jpg, .")
    if await upload_btn.is_visible():
        await upload_btn.click()
        await asyncio.sleep(0.3)

    # Handle "I agree" button if it appears (usually for last frame)
    agree_btn = page.get_by_role("button", name="I agree")
    if await agree_btn.is_visible():
        await agree_btn.click()
        await asyncio.sleep(0.3)

    # Find and use the file input (hidden input element)
    file_input = page.locator('input[type="file"]')
    await file_input.set_input_files(image_path)

    frame_type = "First" if is_first else "Last"
    log(f"{frame_type} frame uploaded", "↑")

    # Wait for crop dialog
    await asyncio.sleep(1)

    # Select aspect ratio if portrait
    is_portrait = "Portrait" in aspect_ratio
    if is_portrait:
        crop_dropdown = page.get_by_text("crop_16_9arrow_drop_down")
        if await crop_dropdown.is_visible():
            await crop_dropdown.click()
            await asyncio.sleep(0.2)
            await page.get_by_role("option", name="Portrait").click()
            await asyncio.sleep(0.3)

    # Click Crop and Save
    crop_save_btn = page.get_by_role("button", name="crop Crop and Save")
    await crop_save_btn.wait_for(timeout=10000)
    await crop_save_btn.click()
    log(f"{frame_type} frame cropped ({aspect_ratio})", "✂")
    await asyncio.sleep(0.5)


async def generate_i2v(
    prompt: str,
    first_frame_path: str | None = None,
    last_frame_path: str | None = None,
    model: str = "veo-3.1-fast",
    aspect_ratio: str = "16:9 (Landscape)",
    seed: int = 42,
    upscale: bool = False,
    pbar=None,
    preview: bool = False,
) -> bytes | None:
    """Image-to-video generation via Google Flow (Frames to Video)."""
    import random

    if not first_frame_path and not last_frame_path:
        raise ValueError("At least one frame (first or last) is required")

    progress = ProgressTracker(pbar, preview)
    progress.update(5)

    is_portrait = "Portrait" in aspect_ratio
    api_model = MODEL_KEYS.get((model, is_portrait), MODEL_KEYS[("veo-3.1-fast", is_portrait)])
    api_ratio = RATIO_ENUMS.get(aspect_ratio, "VIDEO_ASPECT_RATIO_LANDSCAPE")
    base_seed = min(seed, 999999) if seed > 0 else random.randint(100000, 999999)

    frames_desc = []
    if first_frame_path:
        frames_desc.append("first")
    if last_frame_path:
        frames_desc.append("last")
    log(f"Flow I2V: {model} ({api_model}), {aspect_ratio}, {'+'.join(frames_desc)} frame(s), seed={base_seed}", "●")
    if upscale:
        log("Upscaling to 1080p enabled", "↑")
    progress.update(10)

    playwright, context, page, _ = await launch_browser("flow")

    try:
        # Set up request interception BEFORE navigating
        async def intercept_request(route):
            request = route.request
            url = request.url

            # Intercept the video generation API
            if "video:batchAsyncGenerateVideoText" in url:
                try:
                    body = json.loads(request.post_data or "{}")
                    requests_list = body.get("requests", [])

                    debug_log(f"Intercepted batchAsyncGenerateVideoText with {len(requests_list)} requests")

                    # Limit to 1 video and modify the request
                    if requests_list:
                        req = requests_list[0]
                        req["videoModelKey"] = api_model
                        req["aspectRatio"] = api_ratio
                        req["seed"] = base_seed
                        body["requests"] = [req]

                    seeds = [r.get("seed") for r in body.get("requests", [])]
                    log(f"Modified request: {api_model}, {api_ratio}, seeds={seeds}", "→")
                    debug_log(f"Full modified requests: {json.dumps(body.get('requests', []), indent=2)}")
                    await route.continue_(post_data=json.dumps(body))
                    return
                except Exception as e:
                    debug_log(f"Interception error: {e}")

            await route.continue_()

        await page.route("**/aisandbox-pa.googleapis.com/**", intercept_request)

        # Navigate to Flow
        await page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=60000)
        progress.update(15)

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
        progress.update(20)

        # Select "Frames to Video" mode
        await page.get_by_text("Text to Videoarrow_drop_down").click()
        await page.get_by_role("option", name="Frames to Video").click()
        await asyncio.sleep(0.5)
        progress.update(25)

        # Upload first frame if provided
        if first_frame_path:
            await _upload_frame(page, first_frame_path, is_first=True, aspect_ratio=aspect_ratio)
            progress.update(35)

        # Upload last frame if provided
        if last_frame_path:
            await _upload_frame(page, last_frame_path, is_first=False, aspect_ratio=aspect_ratio)
            progress.update(45)

        # Fill prompt
        prompt_input = page.get_by_role("textbox", name="Generate a video with text")
        await prompt_input.wait_for(timeout=10000)
        await prompt_input.click()
        await prompt_input.fill(prompt)
        log(f"Prompt: {prompt[:60]}..." if len(prompt) > 60 else f"Prompt: {prompt}", "✎")
        progress.update(50)

        # Click Create
        await page.get_by_role("button", name="arrow_forward Create").click()
        log("Generating video...", "◐")

        # Wait for video with preview during generation
        download_btn = page.get_by_role("button", name="download Download").first
        last_preview_time = 0
        last_error_check = 0
        video_ready = False

        for elapsed in range(1, 151):  # Up to 2.5 minutes for video
            await asyncio.sleep(1)

            # Check for errors (policy violation, failures) every 3 seconds
            if elapsed - last_error_check >= 3:
                await _check_errors(page)
                last_error_check = elapsed

            # Preview every 3s during generation
            if preview and elapsed - last_preview_time >= 3:
                preview_img = await capture_preview(page)
                if preview_img:
                    pct = 50 + min(elapsed // 6, 40)  # Slower progress for video
                    progress.update(pct, preview_img)
                last_preview_time = elapsed

            # Check if download button is visible
            if await download_btn.is_visible():
                video_ready = True
                log("Video ready", "✓")
                break

        if not video_ready:
            raise TimeoutError("Video generation timed out")

        progress.update(90)

        # Download the video
        resolution = "1080p" if upscale else "720p"
        log(f"Downloading video ({resolution})...", "↓")
        await download_btn.click(force=True)
        await asyncio.sleep(0.3)

        async with page.expect_download(timeout=120000) as download_info:
            if upscale:
                await page.get_by_role("menuitem", name="high_res Upscaled (1080p)").click()
            else:
                await page.get_by_role("menuitem", name="capture Original size (720p)").click()

        download = await download_info.value
        download_path = await download.path()

        if download_path:
            data = Path(download_path).read_bytes()
            log(f"Video downloaded: {len(data) // 1024}KB", "✓")
            progress.update(100)
            return data

        raise Exception("Download failed - no file path")

    finally:
        await close_browser(playwright, context)
