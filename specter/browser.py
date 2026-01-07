"""Browser streaming for embedded login UI."""

import asyncio
import json

from .core.browser import launch_browser, log


class BrowserStream:
    """Streams browser screenshots and handles user input with login detection."""

    def __init__(self):
        self._playwright = None
        self._context = None
        self.page = None
        self.clients = set()
        self.streaming = False
        self._stream_task = None
        self._login_config = None
        self.current_service = None
        self._did_post_login_redirect = False

    async def start(
        self, url: str, width: int = 600, height: int = 800, login_config: dict | None = None, purpose: str = "login"
    ):
        """Launch browser and navigate to URL."""
        if self._context:
            await self.stop()

        self._login_config = login_config
        self.current_service = login_config.get("service") if login_config else None
        self._did_post_login_redirect = False

        # Use launch_browser for persistent profiles and session handling
        self._playwright, self._context, self.page, _ = await launch_browser(
            service=self.current_service or "default",
            viewport={"width": width, "height": height},
            headless=True,
            purpose=purpose,
        )

        # Apply service-specific init scripts from config
        if login_config and login_config.get("init_scripts"):
            for script in login_config["init_scripts"]:
                await self.page.add_init_script(script)

        # Start streaming BEFORE navigation so you can see loading/challenges
        log("Browser ready, streaming screenshots to UI...", "▸")
        self.streaming = True
        self._stream_task = asyncio.create_task(self._stream_loop())

        try:
            # Use longer timeout and wait for any state (allows Cloudflare challenges to load)
            await self.page.goto(url, timeout=60000, wait_until="commit")
        except Exception as e:
            # If navigation times out but page loaded something, continue anyway
            log(f"Navigation warning: {str(e)[:100]}", "⚠")
            log("Continuing with current page state...", "○")

    async def stop(self):
        """Close browser and stop streaming."""
        log(f"Stopping browser stream for {self.current_service or 'unknown'}...", "○")
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
        """Capture and broadcast screenshots, detect login."""
        screenshot_errors = 0
        max_screenshot_errors = 10  # More tolerance for screenshot failures
        last_url = None

        while self.streaming and self.page:
            try:
                # Log URL changes for debugging
                current_url = self.page.url
                if current_url != last_url:
                    log(f"Page: {current_url}", "→")
                    last_url = current_url

                # ALWAYS check for login - even if screenshots are failing
                # This is critical - screenshot failures shouldn't block login detection
                if self._login_config:
                    try:
                        if await self._check_logged_in():
                            log("✓ Login detected! Closing browser to save session...", "★")
                            await self._broadcast_json({"type": "logged_in"})
                    except Exception as e:
                        log(f"Login check error: {e}", "⚠")

                # Try to capture screenshot (but don't let failures stop login checking)
                try:
                    screenshot = await self.page.screenshot(type="png", timeout=5000)
                    await self._broadcast_bytes(screenshot)
                    screenshot_errors = 0  # Reset error counter on success
                except Exception as e:
                    screenshot_errors += 1

                    # Log first error with detail
                    if screenshot_errors == 1:
                        log(f"Screenshot error: {str(e).split('Call log')[0].strip()}", "⚠")

                    # After many failures, stop trying screenshots but KEEP checking login
                    if screenshot_errors >= max_screenshot_errors:
                        log(
                            f"Stopping screenshots after {max_screenshot_errors} failures, but still checking for login...",
                            "○",
                        )
                        # Keep loop running but skip screenshot attempts
                        while self.streaming and self.page:
                            if self._login_config:
                                try:
                                    if await self._check_logged_in():
                                        log("✓ Login detected (no screenshots)!", "★")
                                        await self._broadcast_json({"type": "logged_in"})
                                except:
                                    pass
                            await asyncio.sleep(1)
                        break

                    await asyncio.sleep(0.5)
                    continue

                await asyncio.sleep(0.1)
            except Exception as e:
                log(f"Stream loop error: {e}", "✕")
                # Last attempt to check login before dying
                if self._login_config:
                    try:
                        if await self._check_logged_in():
                            log("✓ Login detected on error!", "★")
                            await self._broadcast_json({"type": "logged_in"})
                    except:
                        pass
                break

    async def _check_logged_in(self) -> bool:
        """Check if user has successfully logged in."""
        cfg = self._login_config
        if not cfg or not self.page:
            return False
        # Skip login detection if explicitly disabled
        if cfg.get("detect_login") is False:
            return False
        url = self.page.url

        # Check URL conditions
        success_pattern = cfg.get("success_url_contains", "")
        if success_pattern and success_pattern not in url:
            return False
        if cfg.get("success_url_excludes") and cfg["success_url_excludes"] in url:
            return False

        # Check login buttons are gone
        for selector in cfg.get("login_selectors", []):
            if await self.page.locator(selector).count() > 0:
                return False

        # Check workspace modal is gone (wait for network idle first)
        if cfg.get("workspace_selector"):
            try:
                await self.page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass
            if await self.page.locator(cfg["workspace_selector"]).count() > 0:
                return False

        # Navigate to post_login_url if specified (e.g., grok.com after X login)
        # Only do this once per session
        post_login_url = cfg.get("post_login_url")
        if post_login_url and not self._did_post_login_redirect:
            self._did_post_login_redirect = True
            log(f"Navigating to {post_login_url} to complete login...", "○")
            try:
                await self.page.goto(post_login_url, timeout=30000)
                await asyncio.sleep(1)  # Let page start loading

                # Check if Cloudflare challenge is present and warn user
                try:
                    title = await self.page.title()
                    if "just a moment" in title.lower():
                        log("⚠ Cloudflare challenge detected - please click the checkbox if shown", "!")
                        # Don't return True yet - wait for challenge to clear
                        return False
                except Exception:
                    pass
            except Exception:
                pass

        # Check if still on Cloudflare challenge page
        try:
            title = await self.page.title()
            if "just a moment" in title.lower():
                return False  # Not logged in yet, still on challenge
        except Exception:
            pass

        return True

    async def _broadcast_bytes(self, data: bytes):
        """Send binary data to all connected clients."""
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    async def _broadcast_json(self, data: dict):
        """Send JSON message to all connected clients."""
        msg = json.dumps(data)
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    async def handle_event(self, event: dict):
        """Replay user input events on the page."""
        if not self.page:
            return

        t = event.get("type")
        x, y = event.get("x"), event.get("y")
        try:
            if t == "click":
                # Atomic click with realistic delay - better for iframes like Cloudflare Turnstile
                if x is not None and y is not None:
                    # Detect Cloudflare Turnstile iframe and calculate offset
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
                        # If clicking inside the iframe area, don't add offset
                        # The user is clicking on the canvas which shows the page screenshot
                        # So their click IS already page-relative
                        log(
                            f"Turnstile iframe at ({int(turnstile_info['x'])}, {int(turnstile_info['y'])}), size {int(turnstile_info['width'])}x{int(turnstile_info['height'])}",
                            "◉",
                        )
                        log(f"Clicking at page coordinates ({int(x)}, {int(y)})", "◉")
                    else:
                        log(f"Click at ({int(x)}, {int(y)})", "◉")

                    await self.page.mouse.click(x, y, delay=50)  # 50ms delay between mousedown/mouseup
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
        """Get browser storage state for session persistence."""
        if self.page:
            return await self.page.context.storage_state()
        return None

    async def is_logged_in(self) -> bool:
        """Check current login state."""
        if not self.page or not self._login_config:
            return False
        return await self._check_logged_in()


browser_stream = BrowserStream()
