#!/usr/bin/env python3
"""Specter CLI - Unified command-line interface for ComfyUI-Specter.

Usage:
    specter onboard <url> [options]    - Onboard new AI provider
    specter test <service> <type>      - Test generation capabilities
    specter diagnose <service>         - Browser compatibility testing
    specter watch <service>            - Monitor API traffic
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add parent to path for imports when running directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def cmd_onboard(args):
    """Run provider onboarding."""
    from .onboard import main as onboard_main

    # Re-inject args into sys.argv for onboard's argparse
    sys.argv = ["specter onboard", args.url] if args.url else ["specter onboard"]
    if args.output:
        sys.argv.extend(["-o", args.output])
    if args.cookies:
        sys.argv.extend(["-c", args.cookies])
    if args.localstorage:
        sys.argv.extend(["-l", args.localstorage])
    if args.profile:
        sys.argv.extend(["-p", args.profile])
    if args.mode:
        sys.argv.extend(["-m", args.mode])
    if args.width:
        sys.argv.extend(["-w", str(args.width)])
    if args.no_login:
        sys.argv.append("--no-login")
    if args.browse:
        sys.argv.append("--browse")
    if args.replay:
        sys.argv.extend(["--replay", args.replay])
    onboard_main()


async def cmd_test(args):
    """Run generation tests."""
    from specter.core.browser import log

    service = args.service
    test_type = args.type
    image_path = args.image
    prompt = args.prompt or ""

    # Validate image path for types that need it
    if test_type in ("i2v", "i2i") and not image_path:
        print(f"Error: {test_type} requires --image <path>")
        sys.exit(1)

    if image_path and not Path(image_path).exists():
        print(f"Error: Image not found: {image_path}")
        sys.exit(1)

    # Map aspect ratio string to size name
    size_map = {
        "1:1": "Square (960x960)",
        "2:3": "Portrait (640x960)",
        "3:2": "Landscape (960x640)",
        "9:16": "Vertical (540x960)",
        "16:9": "Widescreen (960x540)",
    }
    size = size_map.get(args.aspect_ratio, "Portrait (640x960)")

    if service == "grok":
        if test_type == "i2v":
            from specter.providers.grok import imagine_i2v

            log("Testing Grok image-to-video", "●")
            log(f"Image: {image_path}", "○")
            log(f"Prompt: {prompt or '(none)'}", "○")

            video_bytes = await imagine_i2v(image_path=image_path, prompt=prompt)

            if video_bytes:
                output = Path(args.output or "test_i2v_output.mp4")
                output.write_bytes(video_bytes)
                log(f"Saved to {output} ({len(video_bytes) // 1024}KB)", "✓")
            else:
                log("No video returned", "✗")
                sys.exit(1)

        elif test_type == "i2i":
            from specter.providers.grok import imagine_edit

            log("Testing Grok image-to-image", "●")
            log(f"Image: {image_path}", "○")
            log(f"Prompt: {prompt or '(none)'}", "○")

            media_list = await imagine_edit(prompt=prompt, image_path=image_path)

            if media_list:
                output = Path(args.output or "test_i2i_output.png")
                output.write_bytes(media_list[0])
                log(f"Saved {len(media_list)} image(s), best to {output} ({len(media_list[0]) // 1024}KB)", "✓")
            else:
                log("No image returned", "✗")
                sys.exit(1)

        elif test_type == "t2i":
            from specter.providers.grok import imagine_t2i

            if not prompt:
                print("Error: t2i requires --prompt")
                sys.exit(1)
            log("Testing Grok text-to-image", "●")
            log(f"Prompt: {prompt}", "○")

            media_list = await imagine_t2i(prompt=prompt, size=size)

            if media_list:
                output = Path(args.output or "test_t2i_output.png")
                output.write_bytes(media_list[0])
                log(f"Saved {len(media_list)} image(s), best to {output} ({len(media_list[0]) // 1024}KB)", "✓")
            else:
                log("No image returned", "✗")
                sys.exit(1)

        elif test_type == "t2v":
            from specter.providers.grok import imagine_t2v

            if not prompt:
                print("Error: t2v requires --prompt")
                sys.exit(1)
            log("Testing Grok text-to-video", "●")
            log(f"Prompt: {prompt}", "○")

            video_bytes = await imagine_t2v(prompt=prompt, size=size)

            if video_bytes:
                output = Path(args.output or "test_t2v_output.mp4")
                output.write_bytes(video_bytes)
                log(f"Saved to {output} ({len(video_bytes) // 1024}KB)", "✓")
            else:
                log("No video returned", "✗")
                sys.exit(1)

        elif test_type == "chat":
            from specter.providers.grok import chat_with_grok

            if not prompt:
                print("Error: chat requires --prompt")
                sys.exit(1)
            log("Testing Grok chat", "●")
            log(f"Prompt: {prompt}", "○")

            response, image_bytes = await chat_with_grok(
                prompt=prompt,
                model=args.model or "grok-3",
                image_path=image_path,
                preview=True,
            )

            log(f"Response: {response[:200]}..." if len(response) > 200 else f"Response: {response}", "✓")
            if image_bytes:
                output = Path(args.output or "test_chat_image.png")
                output.write_bytes(image_bytes)
                log(f"Image saved to {output}", "✓")

        else:
            print(f"Error: Unknown test type '{test_type}' for grok")
            print("Available: i2v, i2i, t2i, t2v, chat")
            sys.exit(1)

    elif service == "chatgpt":
        from specter.providers.chatgpt import chat_with_gpt

        if not prompt:
            print("Error: chatgpt tests require --prompt")
            sys.exit(1)

        log("Testing ChatGPT chat", "●")
        log(f"Prompt: {prompt}", "○")

        response, image_bytes = await chat_with_gpt(
            prompt=prompt,
            model=args.model or "gpt-4o",
            image_path=image_path,
            preview=True,
        )

        log(f"Response: {response[:200]}..." if len(response) > 200 else f"Response: {response}", "✓")
        if image_bytes:
            output = Path(args.output or "test_chatgpt_image.png")
            output.write_bytes(image_bytes)
            log(f"Image saved to {output}", "✓")

    else:
        print(f"Error: Unknown service '{service}'")
        print("Available: grok, chatgpt")
        sys.exit(1)


async def cmd_diagnose(args):
    """Run browser diagnostics."""
    from specter.core.browser import close_browser, launch_browser

    service = args.service
    sites = {
        "grok": "https://grok.com",
        "chatgpt": "https://chatgpt.com",
    }
    url = sites.get(service, sites["grok"])

    print("\n" + "=" * 60)
    print(f"BROWSER DIAGNOSTICS: {service.upper()}")
    print(f"  URL: {url}")
    print(f"  Persistent: {not args.fresh}")
    print(f"  Clear cookies: {args.clear_cookies}")
    print("=" * 60)

    if args.fresh:
        # Fresh context, no persistence
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        browser = await pw.firefox.launch(
            headless=False,
            firefox_user_prefs={"ui.systemUsesDarkTheme": 1},
        )
        ctx = await browser.new_context(
            viewport={"width": 767, "height": 1800},
        )
        page = await ctx.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
    else:
        # Use actual browser_utils
        pw, ctx, page, _ = await launch_browser(service)

        if args.clear_cookies:
            print("[*] Clearing cookies...")
            await ctx.clear_cookies()

    print(f"[*] Navigating to {url}...")
    await page.goto(url, timeout=60000)

    print("[*] Waiting 3s for page to load...")
    await asyncio.sleep(3)

    # Take screenshot
    screenshot_path = f"/tmp/specter_diagnose_{service}.png"
    await page.screenshot(path=screenshot_path)
    print(f"[*] Screenshot saved: {screenshot_path}")

    # Check localStorage
    try:
        local_keys = await page.evaluate("Object.keys(localStorage)")
        print(f"\n[*] localStorage ({len(local_keys)} keys):")
        for key in local_keys[:10]:
            print(f"    - {key}")
        if len(local_keys) > 10:
            print(f"    ... and {len(local_keys) - 10} more")
    except Exception as e:
        print(f"[!] Could not check localStorage: {e}")

    print("\n>>> CHECK BROWSER WINDOW <<<")
    print(">>> Press ENTER when done...")
    input()

    if args.fresh:
        await ctx.close()
        await pw.stop()
    else:
        await close_browser(pw, ctx)
    print("[*] Done")


async def cmd_watch(args):
    """Watch API traffic."""
    import json

    from specter.core.browser import close_browser, launch_browser, log

    service = args.service
    sites = {
        "grok": "https://grok.com/imagine",
        "chatgpt": "https://chatgpt.com",
    }
    url = sites.get(service, sites["grok"])

    # Endpoints to watch
    watch_endpoints = ["upload-file", "create", "new", "content", "generated_video", "preview_image", "conversation"]
    ignore_patterns = ["track", "monitoring", "log_metric", "events", "like"]

    captured_requests = []
    captured_responses = []

    def should_capture(url: str) -> bool:
        for pattern in ignore_patterns:
            if pattern in url:
                return False
        for endpoint in watch_endpoints:
            if endpoint in url:
                return True
        if f"{service}.com/rest/" in url or "backend-api" in url:
            return True
        return False

    async def on_request(request):
        url = request.url
        if not should_capture(url):
            return
        endpoint = url.split("/")[-1].split("?")[0]
        log(f"REQUEST: {request.method} {endpoint}", "→")
        try:
            post_data = request.post_data
            if post_data:
                try:
                    data = json.loads(post_data)
                    print(f"    Body: {json.dumps(data, indent=2)[:500]}")
                except:
                    print(f"    Body: {post_data[:200]}...")
        except:
            pass
        captured_requests.append({"url": url, "method": request.method, "endpoint": endpoint})

    async def on_response(response):
        url = response.url
        if not should_capture(url):
            return
        endpoint = url.split("/")[-1].split("?")[0]
        content_type = response.headers.get("content-type", "")
        log(f"RESPONSE: {response.status} {endpoint} ({content_type[:30]})", "←")
        if "json" in content_type or "text" in content_type:
            try:
                body = await response.text()
                if len(body) < 2000:
                    try:
                        data = json.loads(body)
                        print(f"    Body: {json.dumps(data, indent=2)}")
                    except:
                        print(f"    Body: {body[:500]}")
                else:
                    print(f"    Body: {body[:500]}...")
            except:
                pass
        captured_responses.append({"url": url, "status": response.status, "endpoint": endpoint})

    log(f"Starting {service.upper()} API watcher...", "●")

    playwright, context, page, _ = await launch_browser(service, viewport={"width": 800, "height": 600}, headless=False)
    page.on("request", on_request)
    page.on("response", on_response)

    await page.goto(url, timeout=60000)

    log(f"Browser ready - interact with {service} to watch API flow", "✓")
    log("Press Ctrl+C to stop and see summary", "○")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log("\nStopping...", "○")

    print("\n" + "=" * 60)
    print(f"CAPTURED {len(captured_requests)} REQUESTS, {len(captured_responses)} RESPONSES")
    print("=" * 60)
    for i, req in enumerate(captured_requests[:20], 1):
        print(f"{i}. {req['method']} {req['endpoint']}")

    await close_browser(playwright, context)


async def cmd_step(args):
    """Debug img2img flow - logs all API requests."""
    import asyncio
    from datetime import datetime

    from specter.core.browser import launch_browser

    image_path = args.image
    prompt = args.prompt or "turn into cat"

    if not image_path:
        print("Error: --image required")
        return

    # Log to file
    log_file = open("debug_requests.log", "w")

    def flog(msg):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        print(line)
        log_file.write(line + "\n")
        log_file.flush()

    flog("=== IMG2IMG DEBUG SESSION ===\n")

    playwright, context, page, _ = await launch_browser("grok", viewport={"width": 767, "height": 1300}, headless=False)

    # Combined route handler
    import json

    async def handle_request(route):
        url = route.request.url

        # Handle conversations/new specially
        if "conversations/new" in url:
            body = route.request.post_data or ""
            flog("\n" + "=" * 60)
            flog("CONVERSATIONS/NEW REQUEST")
            flog("=" * 60)
            try:
                data = json.loads(body)
                flog(f"  modelName:            {data.get('modelName')}")
                flog(f"  message:              {str(data.get('message', ''))[:80]}")
                flog(f"  toolOverrides:        {data.get('toolOverrides')}")
                flog(f"  imageGenerationCount: {data.get('imageGenerationCount')}")
                flog(f"  enableImageGeneration:{data.get('enableImageGeneration')}")
                flog(f"  temporary:            {data.get('temporary')}")
            except:
                flog(f"  RAW: {body[:300]}")

            if "videoGen" in body:
                flog("  >>> BLOCKING (has videoGen)")
                await route.abort()
                return
            if "imageGenerationCount" in body:
                data = json.loads(body)
                data["imageGenerationCount"] = 1
                flog("  >>> ALLOWING (set imageGenerationCount=1)")
                await route.continue_(post_data=json.dumps(data))
                return
            flog("  >>> ALLOWING (passthrough)")

        await route.continue_()

    await page.route("**/*", handle_request)

    # Monitor WebSocket
    def on_websocket(ws):
        flog(f"\n[WS] OPENED: {ws.url}")
        ws.on("framereceived", lambda payload: flog(f"[WS] RECV: {str(payload)[:200]}"))
        ws.on("framesent", lambda payload: flog(f"[WS] SENT: {str(payload)[:200]}"))
        ws.on("close", lambda: flog("[WS] CLOSED"))

    page.on("websocket", on_websocket)

    # Log image responses
    async def log_response(response):
        url = response.url
        ct = response.headers.get("content-type", "")

        # Log SSE/streaming responses
        if "text/event-stream" in ct or "stream" in ct:
            flog(f"\n[SSE] {url[:100]}")

        # Log generated images - show full path after /generated/
        if "image" in ct and response.status == 200 and "generated" in url:
            try:
                data = await response.body()
                if len(data) > 50000:
                    # Extract generation ID from URL
                    gen_part = url.split("/generated/")[-1].split("?")[0] if "/generated/" in url else url[-60:]
                    flog(f"\n[IMG] {len(data) // 1024}KB - /generated/{gen_part}")
            except:
                pass

    page.on("response", log_response)

    await page.goto("https://grok.com/imagine", timeout=60000)
    await page.wait_for_selector('div[contenteditable="true"]', timeout=30000)
    flog("\n>>> PAGE READY")

    # Upload image
    flog(f"\n>>> UPLOADING: {image_path}")
    file_input = page.locator('input[type="file"]').first
    await file_input.set_input_files(image_path)
    await asyncio.sleep(1)

    # Click Edit image
    edit_button = page.locator('button:has-text("Edit image")').first
    await edit_button.wait_for(state="visible", timeout=30000)
    flog("\n>>> CLICKING 'Edit image'")
    await edit_button.click()
    await asyncio.sleep(0.5)

    # Type prompt
    flog(f"\n>>> TYPING: {prompt}")
    await page.keyboard.insert_text(prompt)

    # Submit
    flog("\n>>> PRESSING ENTER")
    await page.keyboard.press("Enter")

    flog("\n>>> WAITING 20s FOR GENERATION...")
    await asyncio.sleep(20)

    flog("\n>>> DONE - check debug_requests.log")
    log_file.close()
    await context.close()
    await playwright.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Specter CLI - Browser automation for AI providers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  specter onboard https://newprovider.com
  specter onboard https://grok.com -m video --no-login
  specter test grok i2v --image photo.png --prompt "make it dance"
  specter test grok t2i --prompt "a cat in space"
  specter test chatgpt chat --prompt "hello"
  specter diagnose grok
  specter watch grok
  specter step --image photo.png --prompt "turn into dog"
""",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Onboard command
    onboard_parser = subparsers.add_parser("onboard", help="Onboard new AI provider")
    onboard_parser.add_argument("url", nargs="?", help="URL of the AI provider")
    onboard_parser.add_argument("-o", "--output", help="Output directory")
    onboard_parser.add_argument("-c", "--cookies", metavar="FILE", help="Cookie file")
    onboard_parser.add_argument("-l", "--localstorage", metavar="FILE", help="localStorage JSON file")
    onboard_parser.add_argument("-p", "--profile", metavar="DIR", help="Browser profile directory")
    onboard_parser.add_argument("-m", "--mode", choices=["text", "image", "video"], default="text")
    onboard_parser.add_argument("-w", "--width", type=int, default=767)
    onboard_parser.add_argument("--no-login", action="store_true", help="Skip login step")
    onboard_parser.add_argument("--browse", action="store_true", help="Browse mode only")
    onboard_parser.add_argument("--replay", metavar="HAR_FILE", help="Replay HAR file")

    # Test command
    test_parser = subparsers.add_parser("test", help="Test generation capabilities")
    test_parser.add_argument("service", choices=["grok", "chatgpt"], help="Service to test")
    test_parser.add_argument("type", choices=["i2v", "i2i", "t2i", "t2v", "chat"], help="Test type")
    test_parser.add_argument("-i", "--image", metavar="PATH", help="Input image path")
    test_parser.add_argument("-p", "--prompt", help="Prompt text")
    test_parser.add_argument("-o", "--output", help="Output file path")
    test_parser.add_argument("-m", "--model", help="Model to use")
    test_parser.add_argument("-a", "--aspect-ratio", default="2:3", help="Aspect ratio (default: 2:3)")

    # Diagnose command
    diagnose_parser = subparsers.add_parser("diagnose", help="Browser compatibility testing")
    diagnose_parser.add_argument("service", choices=["grok", "chatgpt"], help="Service to diagnose")
    diagnose_parser.add_argument("--fresh", action="store_true", help="Use fresh browser context")
    diagnose_parser.add_argument("--clear-cookies", action="store_true", help="Clear saved cookies")

    # Watch command
    watch_parser = subparsers.add_parser("watch", help="Monitor API traffic")
    watch_parser.add_argument("service", choices=["grok", "chatgpt"], help="Service to watch")

    # Step command (debug img2img)
    step_parser = subparsers.add_parser("step", help="Step through img2img flow (debug)")
    step_parser.add_argument("-i", "--image", metavar="PATH", required=True, help="Input image")
    step_parser.add_argument("-p", "--prompt", default="Edit this image", help="Edit prompt")

    args = parser.parse_args()

    if args.command == "onboard":
        cmd_onboard(args)
    elif args.command == "test":
        asyncio.run(cmd_test(args))
    elif args.command == "diagnose":
        asyncio.run(cmd_diagnose(args))
    elif args.command == "watch":
        asyncio.run(cmd_watch(args))
    elif args.command == "step":
        asyncio.run(cmd_step(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
