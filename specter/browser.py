import asyncio
import json

from .core.browser import launch_browser, log


class BrowserStream:
    def __init__(self):
        self._playwright = None
        self._context = None
        self.page = None
        self.clients = set()
        self.streaming = False
        self._stream_task = None
        self._login_config = None
        self.current_service = None

    async def start(
        self, url: str, width: int = 600, height: int = 800, login_config: dict | None = None, purpose: str = "login"
    ):
        if self._context:
            await self.stop()

        self._login_config = login_config
        self.current_service = (login_config.get("service") if login_config else None) or "default"

        self._playwright, self._context, self.page, _ = await launch_browser(
            service=self.current_service,
            viewport={"width": width, "height": height},
            headless=True,
            purpose=purpose,
        )

        if login_config and login_config.get("init_scripts"):
            for script in login_config["init_scripts"]:
                await self.page.add_init_script(script)

        log("Browser ready, streaming screenshots to UI...", "▸")
        self.streaming = True
        self._stream_task = asyncio.create_task(self._stream_loop())

        try:
            await self.page.goto(url, timeout=60000, wait_until="commit")
        except Exception as e:
            log(f"Navigation warning: {str(e)[:100]}", "⚠")
            log("Continuing with current page state...", "○")

    async def stop(self):
        log(f"Stopping browser stream for {self.current_service}...", "○")
        self.streaming = False
        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None

        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                log(f"Warning closing context: {e}", "⚠")
            self._context = None
            self.page = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                log(f"Warning stopping playwright: {e}", "⚠")
            self._playwright = None

        log("Browser stream stopped", "✓")

    async def _stream_loop(self):
        screenshot_errors = 0
        max_screenshot_errors = 10
        last_url = None
        frame_count = 0

        while self.streaming and self.page:
            try:
                # URL change logging (every frame is fine, it's cheap)
                current_url = self.page.url
                if current_url != last_url:
                    log(f"Page: {current_url}", "→")
                    last_url = current_url

                # Login check every 15 frames (~0.5s at 30fps) to reduce overhead
                if self._login_config and frame_count % 15 == 0:
                    try:
                        if await self._check_logged_in():
                            log("✓ Login detected! Closing browser to save session...", "★")
                            await self._broadcast_json({"type": "logged_in"})
                    except Exception as e:
                        log(f"Login check error: {e}", "⚠")

                try:
                    # JPEG is ~5x faster to encode and ~3x smaller than PNG
                    screenshot = await self.page.screenshot(type="jpeg", quality=80, timeout=2000)
                    await self._broadcast_bytes(screenshot)
                    screenshot_errors = 0
                    frame_count += 1
                except Exception as e:
                    screenshot_errors += 1

                    if screenshot_errors == 1:
                        log(f"Screenshot error: {str(e).split('Call log')[0].strip()}", "⚠")

                    if screenshot_errors >= max_screenshot_errors:
                        log(
                            f"Stopping screenshots after {max_screenshot_errors} failures, but still checking for login...",
                            "○",
                        )
                        while self.streaming and self.page:
                            if self._login_config:
                                try:
                                    if await self._check_logged_in():
                                        log("✓ Login detected (no screenshots)!", "★")
                                        await self._broadcast_json({"type": "logged_in"})
                                except Exception:
                                    pass
                            await asyncio.sleep(1)
                        break

                    await asyncio.sleep(0.5)
                    continue

                await asyncio.sleep(0.033)  # ~30fps target
            except Exception as e:
                log(f"Stream loop error: {e}", "✕")
                if self._login_config:
                    try:
                        if await self._check_logged_in():
                            log("✓ Login detected on error!", "★")
                            await self._broadcast_json({"type": "logged_in"})
                    except Exception:
                        pass
                break

    async def _check_logged_in(self) -> bool:
        cfg = self._login_config
        if not cfg or not self.page:
            return False
        if cfg.get("detect_login") is False:
            return False
        url = self.page.url

        success_pattern = cfg.get("success_url_contains", "")
        if success_pattern and success_pattern not in url:
            return False
        if cfg.get("success_url_excludes") and cfg["success_url_excludes"] in url:
            return False

        for selector in cfg.get("login_selectors", []):
            if await self.page.locator(selector).count() > 0:
                return False

        if cfg.get("workspace_selector"):
            try:
                await self.page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass
            if await self.page.locator(cfg["workspace_selector"]).count() > 0:
                return False

        try:
            title = await self.page.title()
            if "just a moment" in title.lower():
                return False
        except Exception:
            pass

        return True

    async def _broadcast_bytes(self, data: bytes):
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    async def _broadcast_json(self, data: dict):
        msg = json.dumps(data)
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    async def handle_event(self, event: dict):
        if not self.page:
            return

        t = event.get("type")
        x, y = event.get("x"), event.get("y")
        try:
            if t == "click":
                if x is not None and y is not None:
                    turnstile_info = await self.page.evaluate("""
                        () => {
                            // Look for Turnstile iframe
                            const selectors = [
                                'iframe[src*="challenges.cloudflare.com"]',
                                'iframe[src*="turnstile"]',
                                'iframe[title*="Widget"]',
                                'iframe[title*="cloudflare"]'
                            ];

                            for (const selector of selectors) {
                                const iframe = document.querySelector(selector);
                                if (iframe) {
                                    const rect = iframe.getBoundingClientRect();
                                    return {
                                        found: true,
                                        x: rect.x,
                                        y: rect.y,
                                        width: rect.width,
                                        height: rect.height
                                    };
                                }
                            }
                            return { found: false };
                        }
                    """)

                    if turnstile_info and turnstile_info.get("found"):
                        log(
                            f"Turnstile iframe at ({int(turnstile_info['x'])}, {int(turnstile_info['y'])}), size {int(turnstile_info['width'])}x{int(turnstile_info['height'])}",
                            "◉",
                        )
                        log(f"Clicking at page coordinates ({int(x)}, {int(y)})", "◉")
                    else:
                        log(f"Click at ({int(x)}, {int(y)})", "◉")

                    await self.page.mouse.click(x, y, delay=50)
            elif t == "mousemove":
                if x is not None and y is not None:
                    await self.page.mouse.move(x, y)
            elif t == "mousedown":
                if x is not None and y is not None:
                    await self.page.mouse.move(x, y)
                await self.page.mouse.down()
            elif t == "mouseup":
                if x is not None and y is not None:
                    await self.page.mouse.move(x, y)
                await self.page.mouse.up()
            elif t == "type":
                await self.page.keyboard.type(event["text"])
            elif t == "keydown":
                await self.page.keyboard.press(event["key"])
            elif t == "scroll":
                await self.page.mouse.wheel(event.get("dx", 0), event.get("dy", 0))
        except Exception as e:
            log(f"Event handling error: {e}", "⚠")

    async def get_storage_state(self):
        if self.page:
            return await self.page.context.storage_state()
        return None

    async def is_logged_in(self) -> bool:
        if not self.page or not self._login_config:
            return False
        return await self._check_logged_in()


browser_stream = BrowserStream()
