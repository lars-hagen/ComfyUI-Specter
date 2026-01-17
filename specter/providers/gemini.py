"""Gemini provider - text chat with multimodal support."""

import asyncio

from ..core.browser import (
    ProgressTracker,
    capture_preview,
    close_browser,
    launch_browser,
    log,
)

# Map model IDs to UI data-test-id values
MODEL_TO_UI = {
    "gemini-1.5-flash": "fast",
    "gemini-3.0-flash": "thinking",
    "gemini-3.0-pro": "pro",
}


async def _upload_files(page, file_paths: list[str], prompt_input) -> None:
    """Upload files via the file input dialog."""
    # Click on prompt area first (activates upload button)
    await prompt_input.click()
    await asyncio.sleep(0.2)

    # Click the + button to open upload menu (first button in uploader, label is localized)
    await page.locator('uploader button').first.click()
    await asyncio.sleep(0.3)

    # Click "Upload file" option
    await page.locator('[data-test-id="local-images-files-uploader-button"]').click()
    await asyncio.sleep(0.3)

    # Set files on the actual file input element
    await page.locator('input[type="file"][name="Filedata"]').set_input_files(file_paths)
    await asyncio.sleep(1.0)


async def chat_with_gemini(
    prompt: str,
    model: str = "gemini-1.5-flash",
    image_paths: list[str] | None = None,
    audio_path: str | None = None,
    video_path: str | None = None,
    file_paths: list[str] | None = None,
    system_prompt: str | None = None,
    pbar=None,
    preview: bool = False,
) -> str:
    """Send message to Gemini and return response text."""
    progress = ProgressTracker(pbar, preview)
    progress.update(5)

    pw, context, page, _ = await launch_browser("gemini")
    progress.update(10)

    try:
        await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")

        # Wait for prompt input
        prompt_input = page.locator("rich-textarea .ql-editor[contenteditable='true']")
        try:
            await prompt_input.wait_for(timeout=10000)
        except Exception:
            raise RuntimeError(
                "Gemini not ready. Please open gemini.google.com in your browser, "
                "accept any terms/dialogs, then try again."
            ) from None

        log("Connected to Gemini", "●")
        progress.update(20)

        # Set up request interception to inject system prompt
        if system_prompt:
            import json
            from urllib.parse import parse_qs, urlencode

            async def inject_system_prompt(route):
                request = route.request
                body = request.post_data or ""

                try:
                    parsed = parse_qs(body)
                    if "f.req" in parsed:
                        freq = parsed["f.req"][0]
                        outer = json.loads(freq)
                        if outer and len(outer) > 1 and outer[1]:
                            inner = json.loads(outer[1])
                            if inner and len(inner) > 0 and inner[0] and len(inner[0]) > 0:
                                original_msg = inner[0][0]
                                inner[0][0] = f"<system_instructions>\n{system_prompt}\n</system_instructions>\n\n{original_msg}"
                                outer[1] = json.dumps(inner)
                                parsed["f.req"] = [json.dumps(outer)]
                                body = urlencode(parsed, doseq=True)
                except Exception:
                    pass

                await route.continue_(post_data=body)

            await page.route("**/StreamGenerate*", inject_system_prompt)

        # Select model via UI dropdown
        ui_model = MODEL_TO_UI.get(model)
        if ui_model:
            mode_btn = page.locator("bard-mode-switcher button.input-area-switch")
            current = (await mode_btn.inner_text()).strip().lower()

            if current != ui_model:
                log(f"Switching model: {current} → {ui_model}", "◆")
                await mode_btn.click()

                option = page.locator(f'[data-test-id="bard-mode-option-{ui_model}"]')
                await option.wait_for(state="visible", timeout=5000)
                await option.click()
                await asyncio.sleep(0.3)

        # Collect all files to upload
        files_to_upload = []
        if image_paths:
            files_to_upload.extend(image_paths)
        if audio_path:
            files_to_upload.append(audio_path)
        if video_path:
            files_to_upload.append(video_path)
        if file_paths:
            files_to_upload.extend(file_paths)

        if files_to_upload:
            log(f"Uploading {len(files_to_upload)} file(s)...", "↑")
            await _upload_files(page, files_to_upload, prompt_input)

            # Check for consent dialog
            upload_consent = page.locator('[data-test-id="upload-image-agree-button"]')
            if await upload_consent.is_visible():
                raise RuntimeError(
                    "Gemini file upload consent required. Please upload a file manually "
                    "at gemini.google.com, accept the terms, then try again."
                )

            # Brief wait for file processing
            await asyncio.sleep(2)
            progress.update(30)

        # Send prompt
        prompt_preview = prompt[:60] + "..." if len(prompt) > 60 else prompt
        log(f'Sending: "{prompt_preview}"', "✎")
        await prompt_input.fill(prompt)
        await page.keyboard.press("Enter")
        progress.update(40)

        # Wait for response (scale timeout with file count)
        file_count = len(files_to_upload)
        result = await _wait_for_response(page, progress, preview, file_count)

        if result:
            log(f"Success: {len(result)} chars", "★")
        else:
            log("No response captured", "⚠")

        return result

    except asyncio.CancelledError:
        log("Interrupted", "✕")
        raise
    except Exception as e:
        log(f"Error: {str(e).split(chr(10))[0]}", "✕")
        raise
    finally:
        await close_browser(pw, context)


async def _wait_for_response(page, progress: ProgressTracker, preview: bool, file_count: int = 0) -> str:
    """Wait for Gemini response to complete."""
    # Scale timeout: 30s base + 40s per file
    timeout = 30000 + file_count * 40000
    try:
        await page.wait_for_selector('message-content [aria-busy]', timeout=timeout)
        progress.update(50)

        start = asyncio.get_event_loop().time()
        last_preview = 0
        done_selector = 'model-response message-content [aria-busy="false"]'

        while True:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > 120:
                break

            progress.update(50 + min(int(elapsed / 3), 40))

            if preview and elapsed - last_preview >= 3:
                progress.update(50 + min(int(elapsed / 3), 40), await capture_preview(page))
                last_preview = elapsed

            done = page.locator(done_selector).last
            if await done.count() > 0:
                text = await done.inner_text()
                progress.update(95)
                if preview:
                    progress.update(95, await capture_preview(page))
                return text

            await asyncio.sleep(0.5)

        # Timeout fallback
        responses = page.locator('model-response message-content')
        if await responses.count() > 0:
            return await responses.last.inner_text()
        return ""

    except Exception as e:
        log(f"Response extraction failed: {e}", "⚠")
        return ""
