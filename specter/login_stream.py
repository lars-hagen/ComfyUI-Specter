"""Embedded browser for login flow."""

import asyncio
import base64
import json
import uuid

from patchright.async_api import async_playwright

from .core.browser import CHROME_ARGS, is_headed, log

# Auth-specific UA (Chrome 144)
AUTH_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"


class BrowserStream:
    """Embedded browser for login flow using Patchright.

    Pure async - no threading, no daemon threads, no locks to clean up.
    """

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._cdp = None

        self.clients = set()
        self.streaming = False
        self.browser_starting = False
        self._stream_task = None

        self._login_config = None
        self.current_service = None
        self.session_id = None

    async def start(
        self, url: str, width: int = 600, height: int = 800, login_config: dict | None = None, purpose: str = "login"
    ):
        """Start browser stream for login flow."""
        # Stop existing session if any
        if self.streaming or self.browser_starting or self.session_id:
            sid = self.session_id[:8] if self.session_id else "unknown"
            log(f"[{sid}] Stopping existing session before starting new one", "○")
            await self.stop()

        self.session_id = str(uuid.uuid4())
        self._login_config = login_config
        self.current_service = (login_config.get("service") if login_config else None) or "default"

        log(f"[{self.session_id[:8]}] Starting browser session for {self.current_service}", "▸")
        self.browser_starting = True

        try:
            self.playwright = await async_playwright().start()

            self.browser = await self.playwright.chromium.launch(
                channel="chrome",
                headless=not is_headed(),
                args=CHROME_ARGS,
            )
            self.context = await self.browser.new_context(
                user_agent=AUTH_USER_AGENT,
                viewport={"width": width, "height": height},
            )

            # CRITICAL: Disable Patchright's route injection that breaks cross-domain navigation
            self.context._impl_obj.route_injecting = True  # type: ignore[attr-defined]

            # Inject saved cookies if any
            session = self._load_session()
            if session and session.get("cookies"):
                await self.context.add_cookies(session["cookies"])

            self.page = await self.context.new_page()

            # Setup WebAuthn virtual authenticator (makes passkey prompts fall back to password)
            try:
                cdp = await self.context.new_cdp_session(self.page)
                await cdp.send("WebAuthn.enable")
                await cdp.send("WebAuthn.addVirtualAuthenticator", {
                    "options": {
                        "protocol": "ctap2",
                        "transport": "usb",
                        "hasResidentKey": False,
                        "hasUserVerification": False,
                    }
                })
            except Exception:
                pass

            # Add init scripts if provided
            if login_config and login_config.get("init_scripts"):
                for script in login_config["init_scripts"]:
                    await self.page.add_init_script(script)

            # Create CDP session for screenshots
            self._cdp = await self.context.new_cdp_session(self.page)

            self.streaming = True
            self.browser_starting = False

            log(f"[{self.session_id[:8]}] Navigating to {url[:50]}...", "▸")
            await self.page.goto(url, timeout=60000, wait_until="domcontentloaded")

            log(f"[{self.session_id[:8]}] Browser ready, streaming...", "▸")
            self._stream_task = asyncio.create_task(self._stream_loop())

        except Exception as e:
            sid = self.session_id[:8] if self.session_id else "unknown"
            log(f"[{sid}] Start failed: {e}", "✕")
            await self.stop()
            raise

    def _load_session(self) -> dict | None:
        if not self.current_service:
            return None
        try:
            from .core.browser import load_session
            return load_session(self.current_service)
        except:
            return None

    async def stop(self):
        """Stop browser stream - clean async shutdown."""
        sid = self.session_id[:8] if self.session_id else "unknown"

        # Cancel stream task
        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None

        self.streaming = False
        self.browser_starting = False

        # Close CDP session
        if self._cdp:
            try:
                await self._cdp.detach()
            except Exception:
                pass
            self._cdp = None

        # Close context
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None

        # Close browser
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None

        # Stop playwright
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

        self.page = None

        if self.session_id:
            log(f"[{sid}] Browser stopped", "✓")
        self.session_id = None

    async def _save_login_and_broadcast(self):
        from .core.browser import save_session

        if not self.current_service or not self.page:
            return
        try:
            storage = await self.page.context.storage_state()
            save_session(self.current_service, dict(storage))
            log(f"Session saved for {self.current_service.title()} ({len(storage.get('cookies', []))} cookies)", "✓")
            await self._broadcast_json({"type": "logged_in"})
        except Exception as e:
            log(f"Failed to save session: {e}", "⚠")

    async def _auto_close(self):
        """Auto-close browser after login detection."""
        await asyncio.sleep(0.1)
        await self.stop()

    async def _stream_loop(self):
        """Main streaming loop."""
        last_url = None
        frame_count = 0
        login_broadcasted = False
        workspace_modal_seen = False
        networkidle_waited = False

        while self.streaming and self.page and self._cdp:
            try:
                if self.page.is_closed():
                    log("Browser closed externally", "○")
                    break

                current_url = self.page.url
                if current_url != last_url:
                    log(f"Page: {current_url}", "→")
                    last_url = current_url
                    workspace_modal_seen = False
                    networkidle_waited = False

                # Check login status every 15 frames
                detect_login = self._login_config.get("detect_login", True) if self._login_config else True
                if self._login_config and detect_login and not login_broadcasted and frame_count % 15 == 0:
                    try:
                        logged_in, networkidle_waited = await self._check_logged_in(networkidle_waited)

                        ws_selector = self._login_config.get("workspace_selector")
                        if ws_selector and not logged_in:
                            modal_count = await self.page.locator(ws_selector).count()
                            if modal_count > 0 and not workspace_modal_seen:
                                log("Workspace modal open - waiting for user to close it", "○")
                                workspace_modal_seen = True
                            elif modal_count == 0 and workspace_modal_seen:
                                log("Workspace modal closed", "✓")
                                workspace_modal_seen = False
                                logged_in = True

                        if logged_in:
                            log("Login detected! Closing browser to save session...", "★")
                            await self._save_login_and_broadcast()
                            login_broadcasted = True
                            asyncio.create_task(self._auto_close())
                            break
                    except Exception as e:
                        log(f"Login check error: {e}", "⚠")

                # CDP screenshot
                try:
                    result = await self._cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 95})
                    screenshot = base64.b64decode(result["data"])
                    await self._broadcast_bytes(screenshot)
                    frame_count += 1
                except Exception as e:
                    error_msg = str(e).lower()
                    if "detached" in error_msg or "not attached" in error_msg or "closed" in error_msg:
                        await asyncio.sleep(0.1)
                        continue
                    frame_count += 1

                await asyncio.sleep(0.033)  # ~30fps

            except Exception as e:
                sid = self.session_id[:8] if self.session_id else "unknown"
                error_msg = str(e).lower()

                if "closed" in error_msg or "detached" in error_msg:
                    log(f"[{sid}] Browser closed externally", "○")
                else:
                    log(f"[{sid}] Stream error: {type(e).__name__}", "✕")
                break

    async def _check_logged_in(self, networkidle_waited: bool = False) -> tuple[bool, bool]:
        """Check if user is logged in."""
        if not self._login_config or not self.page:
            return (False, networkidle_waited)

        try:
            url = self.page.url
            success_pattern = self._login_config.get("success_url_contains", "")
            success_excludes = self._login_config.get("success_url_excludes", "")

            if not success_pattern or success_pattern not in url:
                return (False, networkidle_waited)
            if success_excludes and success_excludes in url:
                return (False, networkidle_waited)

            verify_excludes = self._login_config.get("verify_excludes", [])
            if verify_excludes:
                try:
                    page_text = (await self.page.evaluate("() => document.body?.innerText || ''")).lower()
                    if any(phrase.lower() in page_text for phrase in verify_excludes):
                        return (False, networkidle_waited)
                except Exception:
                    pass

            ws_selector = self._login_config.get("workspace_selector")
            if ws_selector and not networkidle_waited:
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=2000)
                    networkidle_waited = True
                except Exception:
                    pass

            if ws_selector:
                modal_count = await self.page.locator(ws_selector).count()
                if modal_count > 0:
                    return (False, networkidle_waited)

            return (True, networkidle_waited)
        except Exception:
            return (False, networkidle_waited)

    async def _broadcast_bytes(self, data: bytes):
        """Broadcast bytes to all clients."""
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    async def _broadcast_json(self, data: dict):
        """Broadcast JSON to all clients."""
        msg = json.dumps(data)
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    async def handle_event(self, event: dict):
        """Handle input event from client."""
        if not self.page:
            return

        t = event.get("type")
        x, y = event.get("x"), event.get("y")
        try:
            if t == "click":
                if x is not None and y is not None:
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
            log(f"Event error: {e}", "⚠")

    async def get_storage_state(self):
        """Get current storage state."""
        if self.context:
            return await self.context.storage_state()
        return None

    async def is_logged_in(self) -> bool:
        """Check if currently logged in."""
        if not self.page or not self._login_config:
            return False
        logged_in, _ = await self._check_logged_in()
        return logged_in


browser_stream = BrowserStream()
