#!/usr/bin/env python3
"""Hybrid ChatGPT - efficient DOM-based. Supports image and text."""

import asyncio
import json
import time
from pathlib import Path

from patchright.async_api import async_playwright

SESSION_FILE = Path(__file__).parent / "user_data/sessions/chatgpt_session.json"


async def generate(
    prompt: str,
    mode: str = "image",
    model: str = "gpt-4o",
    system_message: str | None = None,
    headed: bool = False,
) -> bytes | str | None:
    """Generate image or text with ChatGPT."""
    start = time.time()
    log = lambda msg: print(f"[{time.time()-start:.2f}s] {msg}")  # noqa: E731

    cookies = []
    if SESSION_FILE.exists():
        cookies = json.loads(SESSION_FILE.read_text()).get("cookies", [])

    pw = await async_playwright().start()
    log("Playwright started")

    # Minimal Chrome flags - more flags can break Cloudflare Turnstile autosolve
    browser = await pw.chromium.launch(
        channel="chrome",
        headless=not headed,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--mute-audio",
        ],
    )
    # Mobile-ish viewport to avoid horizontal scroll issues
    context = await browser.new_context(
        viewport={"width": 767, "height": 1020},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        service_workers="block",
        bypass_csp=True,
    )
    # CRITICAL: Disable Patchright's route injection to prevent cross-domain navigation errors
    context._impl_obj.route_injecting = True
    log("Browser launched")

    if cookies:
        await context.add_cookies(cookies)

    page = await context.new_page()

    await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
    log("Navigation done")

    await page.wait_for_selector("#prompt-textarea", timeout=30000)
    log("Textarea ready")

    # Request interception for model/system message injection
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

    full_prompt = f"Use image_gen to create: {prompt}" if mode == "image" else prompt
    await page.fill("#prompt-textarea", full_prompt)
    await page.keyboard.press("Enter")
    log(f"Sent: {prompt[:50]}{'...' if len(prompt) > 50 else ''}")

    result = None

    if mode == "image":
        try:
            await page.wait_for_selector('text="Image created"', timeout=120000)
            img = page.locator('div[style*="height: 100%"] > img[alt^="Generated image"]').first
            await img.wait_for(timeout=5000)
            src = await img.get_attribute("src")
            result = await (await page.request.get(src)).body()
            log(f"Captured {len(result)//1024}KB")
        except Exception as e:
            log(f"Failed: {e}")
    else:
        try:
            await page.wait_for_selector('[data-testid="stop-button"]', timeout=30000)
            await page.wait_for_selector('[data-testid="stop-button"]', state="hidden", timeout=120000)
            msg = page.locator('[data-message-author-role="assistant"] .markdown.prose').last
            result = await msg.inner_text()
            log(f"Got {len(result)} chars")
        except Exception as e:
            log(f"Failed: {e}")

    await context.close()
    await browser.close()
    await pw.stop()
    return result


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    flags = [a for a in args if a.startswith("--")]
    prompt = next((a for a in args if not a.startswith("--")), "a cool toaster")
    headed = "--headed" in flags
    mode = "text" if "--text" in flags else "image"

    print(f"Mode: {mode.upper()} | Headed: {headed}\nPrompt: {prompt}\n")

    async def main():
        result = await generate(prompt, mode=mode, headed=headed)
        if result:
            if mode == "image":
                Path("chatgpt_output.png").write_bytes(result)
                print(f"Saved chatgpt_output.png ({len(result)//1024}KB)")
            else:
                print(f"--- RESPONSE ---\n{result}")
        else:
            print("Failed")

    asyncio.run(main())
