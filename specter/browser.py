"""Browser streaming for embedded login UI."""

import asyncio
import json

from .core.browser import launch_browser


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

    async def start(self, url: str, width: int = 600, height: int = 800, login_config: dict | None = None):
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
        )

        # Apply service-specific init scripts from config
        if login_config and login_config.get("init_scripts"):
            for script in login_config["init_scripts"]:
                await self.page.add_init_script(script)

        await self.page.goto(url)
        self.streaming = True
        self._stream_task = asyncio.create_task(self._stream_loop())

    async def stop(self):
        """Close browser and stop streaming."""
        self.streaming = False
        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

        if self._context:
            try:
                await self._context.close()
            except:
                pass
            self._context = None
            self.page = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass
            self._playwright = None

    async def _stream_loop(self):
        """Capture and broadcast screenshots, detect login."""
        while self.streaming and self.page:
            try:
                # Check for login success
                if self._login_config and await self._check_logged_in():
                    await self._broadcast_json({"type": "logged_in"})

                screenshot = await self.page.screenshot(type="png")
                await self._broadcast_bytes(screenshot)
                await asyncio.sleep(0.1)
            except Exception:
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
            try:
                await self.page.goto(post_login_url, timeout=30000)
                # Wait for JS to initialize and populate localStorage
                await asyncio.sleep(5)
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
            if t == "mousedown":
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
        except Exception:
            pass

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
