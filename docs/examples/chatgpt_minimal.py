#!/usr/bin/env python3
"""Minimal ChatGPT image gen - DOM-based, no network capture."""

import asyncio
import json
import sys
from pathlib import Path

from patchright.async_api import async_playwright


async def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else "a cool toaster"
    headed = "--headed" in sys.argv

    session_file = Path(__file__).parent / "user_data/sessions/chatgpt_session.json"
    cookies = json.loads(session_file.read_text()).get("cookies", []) if session_file.exists() else []

    async with async_playwright() as pw:
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
        if cookies:
            await context.add_cookies(cookies)

        page = await context.new_page()
        #await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        await page.goto("https://chatgpt.com/")
        await page.fill("#prompt-textarea", f"Use image_gen to create: {prompt}")
        await page.keyboard.press("Enter")

        # Wait for download button first (signals image is ready), then grab the image
        await page.wait_for_selector('button[aria-label="Download this image"]', timeout=120000)

        # Dump HTML around the image for debugging
        container = page.locator('div[aria-label="Generated image"]').first
        html = await container.evaluate("el => el.outerHTML")
        Path("debug_html.html").write_text(html)
        print("Dumped HTML to debug_html.html")

        img = page.locator('div.z-2 > img.z-1[alt="Generated image"]').first
        await img.wait_for(timeout=5000)

        # Get src and fetch
        src = await img.get_attribute("src")
        print(f"Found image: {src[:80]}...")

        response = await page.request.get(src)
        data = await response.body()
        Path("chatgpt_output.png").write_bytes(data)
        print(f"Saved {len(data)//1024}KB")

if __name__ == "__main__":
    asyncio.run(main())
