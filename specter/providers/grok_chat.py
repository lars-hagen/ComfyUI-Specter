"""Grok chat - standalone, no inheritance."""

import asyncio
import json

from ..core.browser import (
    ProgressTracker,
    capture_preview,
    close_browser,
    launch_browser,
    load_session,
    log,
)

LOGIN_SELECTORS = [
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'a[href*="/login"]',
]

AGE_VERIFICATION_SCRIPT = """localStorage.setItem('age-verif', '{"state":{"stage":"pass"},"version":3}');"""
DISMISS_NOTIFICATIONS_SCRIPT = """localStorage.setItem('notifications-toast-dismiss-count', '999');"""


async def chat_with_grok(
    prompt: str,
    model: str = "grok-3",
    image_path: str | None = None,
    system_message: str | None = None,
    pbar=None,
    preview: bool = False,
    image_count: int | None = None,
    _expect_image: bool = False,
    disable_tools: bool = False,
) -> tuple[str, bytes | None]:
    """Send message to Grok and return (text, image_bytes)."""
    progress = ProgressTracker(pbar, preview)
    progress.update(5)

    pw, context, page, _ = await launch_browser("grok")
    progress.update(10)

    await page.add_init_script(AGE_VERIFICATION_SCRIPT)
    await page.add_init_script(DISMISS_NOTIFICATIONS_SCRIPT)

    try:
        await page.goto("https://grok.com", wait_until="domcontentloaded")

        if not await _is_logged_in(page):
            await close_browser(pw, context)
            await _handle_login()
            pw, context, page, _ = await launch_browser("grok")
            await page.add_init_script(AGE_VERIFICATION_SCRIPT)
            await page.add_init_script(DISMISS_NOTIFICATIONS_SCRIPT)
            await page.goto("https://grok.com", wait_until="domcontentloaded")

        await page.wait_for_selector('textarea[aria-label="Ask Grok anything"], div[contenteditable="true"]', timeout=30000)
        log("Connected to Grok", "●")
        progress.update(20)

        # Capture state for response tracking
        response_state = {"text": "", "complete": False}
        captured_images: list[bytes] = []

        # Request interception
        async def intercept_request(route):
            if route.request.method != "POST":
                await route.continue_()
                return
            try:
                body = json.loads(route.request.post_data or "{}")
                modified = False

                # Disable side-by-side feedback
                if body.get("enableSideBySide"):
                    body["enableSideBySide"] = False
                    modified = True

                # Disable text follow-ups
                if not body.get("disableTextFollowUps"):
                    body["disableTextFollowUps"] = True
                    modified = True

                # Disable self-harm short circuit
                if not body.get("disableSelfHarmShortCircuit"):
                    body["disableSelfHarmShortCircuit"] = True
                    modified = True

                # Inject model
                if model and "modelName" in body and body.get("modelName") != model:
                    log(f"Model: {model}", "⟳")
                    body["modelName"] = model
                    modified = True

                # Inject system message
                if system_message:
                    log(f"System: {system_message[:40]}...", "⟳")
                    body["customPersonality"] = system_message
                    modified = True

                # Image count
                if image_count is not None and "imageGenerationCount" in body:
                    body["imageGenerationCount"] = image_count
                    modified = True

                # Disable tools
                if disable_tools:
                    body["disableSearch"] = True
                    body["enableImageGeneration"] = False
                    modified = True

                if modified:
                    await route.continue_(post_data=json.dumps(body))
                else:
                    await route.continue_()
            except:
                await route.continue_()

        # Response tracking
        async def track_response(response):
            url = response.url
            # Track images
            if "assets.grok.com" in url and "/generated/" in url:
                try:
                    data = await response.body()
                    if len(data) > 80000:  # 80KB min
                        captured_images.append(data)
                        log(f"Captured image ({len(data)//1024}KB)", "◆")
                except:
                    pass
                return

            # Track API response for completion
            if "/rest/app-chat" in url and response.status == 200:
                try:
                    text = await response.text()
                    for line in text.split("\n"):
                        if not line.strip():
                            continue
                        try:
                            body = json.loads(line)
                            result = body.get("result", {}).get("response", {})
                            model_response = result.get("modelResponse", {})
                            msg = model_response.get("message", "")
                            if msg:
                                response_state["text"] = msg
                                response_state["complete"] = True
                        except:
                            pass
                except:
                    pass

        await page.route("**/rest/app-chat/**", intercept_request)
        page.on("response", track_response)

        # Upload image if provided
        if image_path:
            log("Attaching image...", "↑")
            file_input = page.locator('input[type="file"]').first
            await file_input.set_input_files(image_path)
            await asyncio.sleep(2)
            progress.update(30)

        # Send prompt
        prompt_preview = prompt[:60] + "..." if len(prompt) > 60 else prompt
        log(f'Sending: "{prompt_preview}"', "✎")
        textarea = page.locator('textarea[aria-label="Ask Grok anything"], div[contenteditable="true"]').first
        await textarea.fill(prompt)
        await page.keyboard.press("Enter")
        progress.update(40)

        # Wait for response
        result_text = ""
        result_image = None

        if _expect_image:
            result_text, result_image = await _wait_for_image(page, captured_images, progress, preview)
        else:
            result_text = await _wait_for_text(page, response_state, progress, preview)

        if result_text or result_image:
            log(f"Success: {len(result_text)} chars" + (" + image" if result_image else ""), "★")
        else:
            log("No response captured", "⚠")

        return result_text, result_image

    except asyncio.CancelledError:
        log("Interrupted", "✕")
        raise
    except Exception as e:
        log(f"Error: {str(e).split(chr(10))[0]}", "✕")
        raise
    finally:
        await close_browser(pw, context)


async def _is_logged_in(page) -> bool:
    try:
        await page.wait_for_load_state("domcontentloaded")
        for selector in LOGIN_SELECTORS:
            if await page.locator(selector).count() > 0:
                return False
        return True
    except:
        return False


async def _handle_login() -> dict:
    from server import PromptServer

    log("Not logged in - opening authentication popup...", "⚠")
    PromptServer.instance.send_sync("specter-grok-login-required", {})

    log("Waiting for login to complete...", "◌")
    timeout, elapsed = 300, 0

    while elapsed < timeout:
        await asyncio.sleep(2)
        elapsed += 2
        session = load_session("grok")
        if session:
            log("Login detected!", "●")
            return session

    raise Exception("Login timed out after 5 minutes")


async def _wait_for_image(page, captured: list, progress: ProgressTracker, preview: bool) -> tuple[str, bytes | None]:
    """Wait for image generation."""
    try:
        start, last_preview = asyncio.get_event_loop().time(), 0

        while asyncio.get_event_loop().time() - start < 60:
            if captured:
                if preview:
                    progress.update(95, await capture_preview(page))
                else:
                    progress.update(95)
                return "Image generated", captured[-1]

            elapsed = asyncio.get_event_loop().time() - start
            if elapsed - last_preview >= 3:
                if preview:
                    progress.update_async(int(40 + elapsed), page)
                else:
                    progress.update(int(40 + elapsed))
                last_preview = elapsed

            await asyncio.sleep(0.5)

        return "", None
    except Exception as e:
        log(f"Image capture failed: {e}", "⚠")
        return "", None


async def _wait_for_text(page, state: dict, progress: ProgressTracker, preview: bool) -> str:
    """Wait for text response."""
    try:
        start, last_preview = asyncio.get_event_loop().time(), 0

        while asyncio.get_event_loop().time() - start < 120:
            if state["complete"]:
                if preview:
                    progress.update(95, await capture_preview(page))
                else:
                    progress.update(95)
                return state["text"]

            elapsed = asyncio.get_event_loop().time() - start
            if elapsed - last_preview >= 3:
                if preview:
                    progress.update_async(int(40 + elapsed / 2), page)
                else:
                    progress.update(int(40 + elapsed / 2))
                last_preview = elapsed

            await asyncio.sleep(0.5)

        # Fallback to DOM extraction
        log("API timeout, extracting from DOM...", "⚠")
        try:
            response_el = page.locator(".response-content-markdown").last
            if await response_el.count() > 0:
                return await response_el.inner_text(timeout=2000)
        except:
            pass

        return ""
    except Exception as e:
        log(f"Text extraction failed: {e}", "⚠")
        return ""
