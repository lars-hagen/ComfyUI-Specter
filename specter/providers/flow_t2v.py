"""Google Flow Text-to-Video - Hybrid DOM + request interception."""

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


# Model display names
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


async def generate_t2v(
    prompt: str,
    model: str = "veo-3.1-fast",
    aspect_ratio: str = "16:9 (Landscape)",
    seed: int = 42,
    upscale: bool = False,
    pbar=None,
    preview: bool = False,
) -> bytes | None:
    """Text-to-video generation via Google Flow with request interception."""
    import random

    progress = ProgressTracker(pbar, preview)
    progress.update(5)

    is_portrait = "Portrait" in aspect_ratio
    api_model = MODEL_KEYS.get((model, is_portrait), MODEL_KEYS[("veo-3.1-fast", is_portrait)])
    api_ratio = RATIO_ENUMS.get(aspect_ratio, "VIDEO_ASPECT_RATIO_LANDSCAPE")
    base_seed = min(seed, 999999) if seed > 0 else random.randint(100000, 999999)

    log(f"Flow T2V: {model} ({api_model}), {aspect_ratio}, seed={base_seed}", "●")
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

        # Text to Video is the default mode, no need to change it

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

        # Download the video - click button to open menu, then click download option
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
