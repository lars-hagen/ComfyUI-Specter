"""ChatGPT provider - standalone, no inheritance."""

import asyncio
import json

from ..core.browser import (
    ProgressTracker,
    capture_preview,
    close_browser,
    handle_login,
    is_logged_in,
    launch_browser,
    log,
)

LOGIN_SELECTORS = ['button:has-text("Log in")', 'a:has-text("Log in")', 'button:has-text("Sign up")']


async def chat_with_gpt(
    prompt: str,
    model: str,
    image_path: str | None = None,
    system_message: str | None = None,
    pbar=None,
    preview: bool = False,
    _expect_image: bool = False,
    disable_tools: bool = False,
) -> tuple[str, bytes | None]:
    """Send message to ChatGPT and return (text, image_bytes)."""
    progress = ProgressTracker(pbar, preview)
    progress.update(5)

    pw, context, page, _ = await launch_browser("chatgpt")
    progress.update(10)

    try:
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")

        if not await is_logged_in(page, LOGIN_SELECTORS):
            await close_browser(pw, context)
            session = await handle_login("chatgpt", "specter-login-required", LOGIN_SELECTORS)
            pw, context, page, _ = await launch_browser("chatgpt")
            if session.get("cookies"):
                await context.add_cookies(session["cookies"])
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")

        await page.wait_for_selector("#prompt-textarea", timeout=30000)
        log("Connected to ChatGPT", "●")
        progress.update(20)

        # Request interception for model/system message
        if system_message or (model and model != "gpt-4o"):
            async def intercept(route):
                if "backend-api" in route.request.url and "conversation" in route.request.url:
                    try:
                        body = json.loads(route.request.post_data or "{}")
                        if model and "model" in body:
                            body["model"] = model
                        if system_message and "messages" in body and body["messages"]:
                            body["messages"].insert(0, {
                                "author": {"role": "system"},
                                "content": {"content_type": "text", "parts": [system_message]},
                            })
                        await route.continue_(post_data=json.dumps(body))
                        return
                    except:
                        pass
                await route.continue_()
            await page.route("**/backend-api/**/conversation", intercept)

        # Upload image if provided
        if image_path:
            log("Attaching image...", "↑")
            file_input = page.locator('input[type="file"]').first
            await file_input.set_input_files(image_path)
            await asyncio.sleep(2)  # Wait for upload
            progress.update(30)

        # Send prompt
        prompt_preview = prompt[:60] + "..." if len(prompt) > 60 else prompt
        log(f'Sending: "{prompt_preview}"', "✎")
        await page.fill("#prompt-textarea", prompt)
        await page.keyboard.press("Enter")
        progress.update(40)

        # Wait for response
        result_text = ""
        result_image = None

        if _expect_image:
            result_text, result_image = await _wait_for_image(page, progress, preview)
        else:
            result_text = await _wait_for_text(page, progress, preview)

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


async def _wait_for_image(page, progress: ProgressTracker, preview: bool) -> tuple[str, bytes | None]:
    """Wait for image generation to complete."""
    try:
        await page.wait_for_selector('text="Image created"', timeout=120000)
        progress.update(80)

        img = page.locator('div[style*="height: 100%"] > img[alt^="Generated image"]').first
        await img.wait_for(timeout=5000)

        src = await img.get_attribute("src")
        data = await (await page.request.get(src)).body()
        log(f"Captured {len(data)//1024}KB image", "◆")
        progress.update(95)

        if preview:
            progress.update(95, await capture_preview(page))

        return "Image generated", data
    except Exception as e:
        log(f"Image capture failed: {e}", "⚠")
        return "", None


async def _wait_for_text(page, progress: ProgressTracker, preview: bool) -> str:
    """Wait for text response to complete."""
    try:
        # Wait for stop button to appear (streaming started)
        await page.wait_for_selector('[data-testid="stop-button"]', timeout=30000)
        progress.update(60)

        # Wait for stop button to disappear (streaming done)
        await page.wait_for_selector('[data-testid="stop-button"]', state="hidden", timeout=120000)
        progress.update(90)

        # Get last assistant message
        msg = page.locator('[data-message-author-role="assistant"] .markdown.prose').last
        text = await msg.inner_text()
        progress.update(95)

        if preview:
            progress.update(95, await capture_preview(page))

        return text
    except Exception as e:
        log(f"Text extraction failed: {e}", "⚠")
        return ""
