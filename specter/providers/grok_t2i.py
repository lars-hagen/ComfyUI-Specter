"""Grok Imagine Text-to-Image - DOM-based generation via /imagine tab."""

import asyncio
import base64
import time

from ..core.browser import ProgressTracker, capture_preview, close_browser, launch_browser, log
from .grok_video import SIZES, _check_errors, _log_image_info, _setup_imagine_page


def _calc_viewport(size: str, max_images: int) -> dict:
    """Calculate viewport to fit exactly max_images in single column."""
    _, res = SIZES.get(size, ([1, 1], "960x960"))
    w, h = map(int, res.split("x"))
    vp_width = 500
    scale = (vp_width - 40) / w
    scaled_h = int(h * scale)
    vp_height = (scaled_h * max_images) + 150
    return {"width": vp_width, "height": vp_height}


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
    _, expected_res = SIZES.get(size, ([1, 1], "960x960"))

    playwright, context, page, *_ = await launch_browser("grok")

    try:
        await _setup_imagine_page(page, size, video=False)

        log(f"Settings: {max_images} images, {expected_res}, viewport {viewport['width']}x{viewport['height']}", "○")
        log(f"Prompt: {prompt[:60]}..." if len(prompt) > 60 else f"Prompt: {prompt}", "✎")
        progress.update(30)

        textarea = page.locator(".tiptap")

        # Count existing buttons BEFORE submitting (may have leftover from previous session)
        existing_buttons = await page.evaluate(
            """() => document.querySelectorAll('button[aria-label="Make video"]').length"""
        )
        if existing_buttons > 0:
            log(f"Found {existing_buttons} existing image(s) from previous session", "○")

        await textarea.fill(prompt)
        await page.locator('button[aria-label="Submit"]').click()
        progress.update(40)

        # Wait for NEW "Make video" buttons (count must increase by max_images)
        target_count = existing_buttons + max_images
        await page.wait_for_function(
            """(targetCount) => {
            const buttons = document.querySelectorAll('button[aria-label="Make video"]');
            return buttons.length >= targetCount;
        }""",
            arg=target_count,
            timeout=60000,
        )
        await _check_errors(page)

        # Wait for images to finish loading (base64 loads progressively: preview → full quality)
        # Use stability check: if sizes stop changing, images are done loading
        expected_w, expected_h = map(int, expected_res.split("x"))
        min_base64_size = 180000  # ~135KB decoded (base64 is 4/3 of decoded size)
        wait_start = time.time()
        last_preview = 0
        last_sizes = []
        stable_count = 0
        last_error_check = 0

        while time.time() - wait_start < 40:
            try:
                # Check if images meet quality threshold
                await page.wait_for_function(
                    """({maxImages, minWidth, minHeight, minSrcLength}) => {
                    const buttons = Array.from(document.querySelectorAll('button[aria-label="Make video"]'));
                    const images = buttons.slice(0, maxImages).map(btn => {
                        const container = btn.closest('[class*="group/media-post-masonry-card"]');
                        return container?.querySelector('img[alt="Generated image"]');
                    });
                    return images.every(img =>
                        img?.complete &&
                        img.naturalWidth >= minWidth * 0.9 &&
                        img.naturalHeight >= minHeight * 0.9 &&
                        img.src?.startsWith('data:image') &&
                        img.src.length >= minSrcLength
                    );
                }""",
                    arg={
                        "maxImages": max_images,
                        "minWidth": expected_w,
                        "minHeight": expected_h,
                        "minSrcLength": min_base64_size,
                    },
                    timeout=1000,
                )
                elapsed_total = time.time() - wait_start
                log(f"All {max_images} images loaded in {int(elapsed_total)}s", "✓")
                break
            except Exception:
                # Check stability: if sizes AND dimensions haven't changed for 3 checks, consider loaded
                current_state = await page.evaluate(
                    """({maxImages, minWidth, minHeight}) => {
                    const buttons = Array.from(document.querySelectorAll('button[aria-label="Make video"]'));
                    return buttons.slice(0, maxImages).map(btn => {
                        const container = btn.closest('[class*="group/media-post-masonry-card"]');
                        const img = container?.querySelector('img[alt="Generated image"]');
                        return {
                            size: img?.src?.length || 0,
                            width: img?.naturalWidth || 0,
                            height: img?.naturalHeight || 0,
                            meetsMinDimensions: (img?.naturalWidth || 0) >= minWidth * 0.8 && (img?.naturalHeight || 0) >= minHeight * 0.8
                        };
                    });
                }""",
                    {"maxImages": max_images, "minWidth": expected_w, "minHeight": expected_h},
                )

                # Consider stable if: sizes unchanged, dimensions met, size >100KB
                if (
                    current_state == last_sizes
                    and current_state
                    and all(s["size"] > 100000 and s["meetsMinDimensions"] for s in current_state)
                ):
                    stable_count += 1
                    if stable_count >= 3:
                        elapsed_total = time.time() - wait_start
                        log(f"Images stable (no growth for {stable_count}s) - proceeding", "✓")
                        break
                else:
                    stable_count = 0
                last_sizes = current_state

            elapsed = time.time() - wait_start
            if elapsed - last_error_check >= 3:
                await _check_errors(page)
                last_error_check = elapsed
            if elapsed - last_preview >= 3 and progress.preview:
                preview_img = await capture_preview(page)
                if preview_img:
                    progress.update(progress.current, preview_img)
                last_preview = elapsed
            await asyncio.sleep(1)

        # Check if we timed out
        if time.time() - wait_start >= 40:
            log("Timeout after 40s - proceeding with whatever is loaded", "⚠")

        progress.update(90)

        # Extract images using the "Make video" button as selector
        image_data = await page.evaluate(
            """(maxImages) => {
            const buttons = Array.from(document.querySelectorAll('button[aria-label="Make video"]'));
            return buttons.slice(0, maxImages).map(btn => {
                const container = btn.closest('[class*="group/media-post-masonry-card"]');
                const img = container?.querySelector('img[alt="Generated image"]');
                if (!img?.src) return null;

                // Try to get dimensions
                const width = img.naturalWidth || img.width;
                const height = img.naturalHeight || img.height;
                const srcLength = img.src.length;

                return {
                    src: img.src.startsWith('data:image') ? img.src : null,
                    width,
                    height,
                    srcLength
                };
            }).filter(d => d && d.src);
        }""",
            max_images,
        )

        images = []
        min_quality_size = 100 * 1024

        for idx, img_info in enumerate(image_data):
            b64 = img_info["src"].split(";base64,")[1]
            data = base64.b64decode(b64)
            size_kb = len(data) // 1024
            w, h = img_info["width"], img_info["height"]

            log(f"Image {idx + 1}: {w}x{h}, src={img_info['srcLength']} chars, decoded={size_kb}KB", "○")

            if len(data) >= min_quality_size:
                _log_image_info(data, f"Kept image {idx + 1}")
                images.append(data)
            else:
                log(f"Rejected image {idx + 1}: {size_kb}KB < 100KB threshold", "✕")

        if not images:
            if image_data:
                sizes = [
                    f"{d['width']}x{d['height']} ({len(base64.b64decode(d['src'].split(';base64,')[1])) // 1024}KB)"
                    for d in image_data
                ]
                raise Exception(
                    f"All {len(image_data)} images were too small (likely moderated/failed): {', '.join(sizes)}"
                )
            else:
                raise Exception("No images found - generation may have been moderated or failed")

        log(f"Generation complete ({len(images)} images)", "✓")
        for i, img_data in enumerate(images):
            try:
                _log_image_info(img_data, f"Image {i + 1}")
            except Exception:
                pass
        progress.update(100)
        return images
    finally:
        await close_browser(playwright, context)
