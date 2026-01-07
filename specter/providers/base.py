"""Abstract base class for chat service providers."""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..core.browser import (
    ProgressTracker,
    close_browser,
    get_service_lock,
    handle_browser_error,
    launch_browser,
    log,
    log_media_capture,
    update_preview,
)
from ..core.session import handle_login_flow, is_logged_in


def _clean_playwright_error(error: str) -> str:
    """Clean verbose Playwright error messages for user display."""
    if "install-deps" in error:
        return "Missing system dependencies. Run: sudo playwright install-deps"
    if "launch_persistent_context" in error or "BrowserType" in error:
        # Extract just the key message from Playwright's verbose output
        lines = error.split("\n")
        for line in lines:
            line = line.strip()
            if line and not line.startswith("╔") and not line.startswith("║") and not line.startswith("╚"):
                if "Host system" in line or "missing" in line.lower():
                    return line
        return "Browser failed to launch. Check system dependencies."
    return error.split("\n")[0]


@dataclass
class ServiceConfig:
    """Configuration for a chat service."""

    service_name: str
    base_url: str
    selectors: dict  # textarea, send_button, response
    login_selectors: list
    login_event: str  # WebSocket event name for login popup
    image_url_patterns: list = field(default_factory=list)  # URL patterns for captured images
    image_min_size: int = 50000  # Minimum image size in bytes
    completion_selectors: list = field(default_factory=list)  # Selectors indicating completion
    response_timeout: int = 90  # Seconds to wait for response
    init_scripts: list = field(default_factory=list)  # Scripts to inject before page load (localStorage setup)
    cookies: list = field(default_factory=list)  # Cookies to add before navigation


class ChatService(ABC):
    """Abstract base class for chat providers (ChatGPT, Grok, etc.)

    Implements the template method pattern for browser-based chat automation.
    Subclasses override hook methods for service-specific behavior.
    """

    config: ServiceConfig

    def __init__(self):
        self.page: Any = None  # Set in _launch_browser
        self.context: Any = None
        self.playwright: Any = None
        self.progress: Any = None  # Set in chat()
        self.captured_images: list[bytes] = []
        self.response_count = 0
        self.listening = False
        self._session: dict | None = None
        self.last_activity = 0.0

    async def chat(
        self,
        prompt: str,
        model: str,
        image_path: str | None = None,
        system_message: str | None = None,
        pbar=None,
        preview: bool = False,
        **kwargs,
    ) -> tuple[str, bytes | None]:
        """Send message and return response text + captured image.

        This is the template method that orchestrates the chat flow.
        """
        self.progress = ProgressTracker(pbar, preview)
        self.captured_images = []
        self.response_count = 0
        self.listening = False

        lock = get_service_lock(self.config.service_name)
        await lock.acquire()

        try:
            self.progress.update(5)
            await self._launch_browser()
            self.progress.update(10)

            await self._setup_interceptors(model, system_message, **kwargs)
            await self._navigate_and_login()
            self.progress.update(20)

            log(f"Connected to {self.config.service_name.title()}", "●")

            await self._wait_for_interface()
            self.progress.update(25)

            if image_path:
                await self._upload_image(image_path)
                self.progress.update(30)

            await self._send_prompt(prompt)
            self.progress.update(40)

            text, images = await self._wait_for_response()
            self.progress.update(100)

            # Dump debug if no response at all
            if not text and not images:
                log("No response captured", "⚠")
                await handle_browser_error(self.page, Exception("No response text or images"), self.config.service_name)
                return "", None

            # Dump debug if image was expected but got text-only
            if text and not images and kwargs.get("_expect_image"):
                log("Expected image but got text-only response", "⚠")
                await handle_browser_error(
                    self.page, Exception("Image expected but not captured"), self.config.service_name
                )

            if images:
                log(f"Success: {len(text)} chars + image captured", "★")
            else:
                log(f"Success: {len(text)} chars", "★")

            return text, self._select_best_image(images)

        except asyncio.CancelledError:
            log("Interrupted by user", "✕")
            raise
        except Exception as e:
            clean_msg = _clean_playwright_error(str(e))
            log(f"Error: {clean_msg}", "✕")
            await handle_browser_error(self.page, e, self.config.service_name)
            raise Exception(clean_msg) from e
        finally:
            await self._cleanup()
            lock.release()

    async def _launch_browser(self):
        """Launch browser with persistent profile."""
        self.playwright, self.context, self.page, session = await launch_browser(self.config.service_name)
        self.page.on("response", self._handle_response)
        self._session = session

        # Apply service-specific init scripts (localStorage setup, etc.)
        for script in self.config.init_scripts:
            await self.page.add_init_script(script)

        # Apply service-specific cookies
        if self.config.cookies:
            await self.context.add_cookies(self.config.cookies)

    async def _setup_interceptors(self, model: str, system_message: str | None, **kwargs):
        """Set up request interceptors for model/system message injection."""
        if not self._needs_interception(model, system_message, **kwargs):
            return
        if not self.page:
            return

        async def intercept(route):
            try:
                post_data = route.request.post_data
                if post_data:
                    import json

                    body = json.loads(post_data)
                    modified = self.modify_request_body(body, model, system_message, **kwargs)
                    if modified:
                        await route.continue_(post_data=json.dumps(body))
                        return
            except:
                pass
            await route.continue_()

        await self.page.route(self._get_intercept_pattern(), intercept)

    async def _navigate_and_login(self):
        """Navigate to service and handle login if needed."""
        if not self.page:
            raise RuntimeError("Browser page not initialized")
        await self.page.goto(self.config.base_url, timeout=120000, wait_until="domcontentloaded")

        if not await is_logged_in(self.page, self.config.login_selectors):
            await self._handle_login()

    async def _handle_login(self):
        """Handle login flow with popup and polling."""
        new_session = await handle_login_flow(
            self.page, self.config.service_name, self.config.login_event, self.config.login_selectors
        )

        # Inject cookies first
        if "cookies" in new_session:
            await self.page.context.add_cookies(new_session["cookies"])

        # Navigate - Firefox profile handles localStorage
        await self.page.goto(self.config.base_url, timeout=120000, wait_until="domcontentloaded")

    async def _wait_for_interface(self):
        """Wait for chat interface to be ready."""
        await self.page.wait_for_selector(self.config.selectors["textarea"], timeout=30000)

    async def _upload_image(self, image_path: str):
        """Upload an image if provided."""
        log("Attaching input image...", "↑")
        try:
            file_input = self.page.locator('input[type="file"]').first
            await file_input.set_input_files(image_path)
            await asyncio.sleep(1)
        except:
            log("Image upload not available", "⚠")

    async def _send_prompt(self, prompt: str):
        """Type and send the prompt."""
        prompt_preview = prompt[:60] + "..." if len(prompt) > 60 else prompt
        log(f'Typing prompt: "{prompt_preview}"', "✎")

        await self.page.keyboard.insert_text(prompt)
        self.progress.update(35)

        send_btn = self.page.locator(self.config.selectors["send_button"])
        await send_btn.wait_for(state="visible", timeout=10000)

        # Wait for button to be enabled
        for _ in range(50):
            if await send_btn.is_enabled():
                break
            await asyncio.sleep(0.1)

        self.listening = True
        await self._click_send(send_btn)
        log("Prompt sent - awaiting response...", "→")
        await asyncio.sleep(0.5)
        await self._update_preview()

    async def _click_send(self, send_btn):
        """Click the send button. Override for different behavior."""
        await send_btn.click()

    async def _wait_for_response(self) -> tuple[str, list[bytes]]:
        """Wait for response to complete and extract content."""
        # Wait for response container
        await self.page.wait_for_selector(self.config.selectors["response"], timeout=60000)
        self.progress.update(50)

        # Activity-based timeout
        wait_start = time.time()
        last_activity = time.time()
        last_preview = 0
        last_image_count = 0
        last_text_length = 0

        INACTIVITY_TIMEOUT = 30  # Exit if no activity for 30s

        while True:
            await asyncio.sleep(0.5)
            elapsed = time.time() - wait_start
            inactive = time.time() - last_activity

            # Check max timeout (safety net)
            if elapsed >= self.config.response_timeout:
                log(f"Max timeout reached ({self.config.response_timeout}s)", "⚠")
                break

            # Check inactivity timeout
            if inactive >= INACTIVITY_TIMEOUT:
                log(f"No activity for {INACTIVITY_TIMEOUT}s - assuming complete", "✓")
                break

            try:
                # Check for error state
                if await self._check_error_state():
                    raise Exception(f"{self.config.service_name.title()} failed. Please try again.")

                # Check for completion
                completion_selector = await self._check_completion()
                if completion_selector:
                    log(f"Completion detected: {completion_selector}", "✓")
                    await asyncio.sleep(2)  # Wait for network capture
                    break

                # Track activity: new images
                if len(self.captured_images) > last_image_count:
                    last_activity = time.time()
                    last_image_count = len(self.captured_images)

                # Track activity: text changed
                try:
                    current_text = await self.extract_response_text()
                    if len(current_text) != last_text_length:
                        last_activity = time.time()
                        last_text_length = len(current_text)
                except:
                    pass

            except Exception as e:
                if "failed" in str(e).lower():
                    raise
                pass

            # Update preview every 3 seconds
            if elapsed - last_preview >= 3:
                await self._update_preview()
                last_preview = elapsed

            # Update progress bar
            if int(elapsed) % 10 == 0:
                self.progress.update(min(50 + int(elapsed / 6), 85))

        self.progress.update(95)

        # Try DOM fallback for images
        if not self.captured_images:
            log("No images from network capture, trying DOM fallback...", "○")
            self.captured_images = await self._extract_images_from_dom()
            if self.captured_images:
                log(f"DOM fallback found {len(self.captured_images)} image(s)", "✓")
            else:
                log("DOM fallback found no images", "⚠")

        # Extract text
        response_text = await self.extract_response_text()

        self.progress.update(95)
        await self._update_preview()

        return response_text, self.captured_images

    async def _update_preview(self):
        """Update the progress bar with a preview screenshot."""
        if not self.progress.preview:
            return
        preview_img = await update_preview(self.page)
        if preview_img:
            self.progress.update(self.progress.current, preview_img)

    async def _handle_response(self, response):
        """Handle network responses to capture images."""
        if not self.listening:
            return

        url = response.url

        # Track API responses for progress
        if self._is_api_response(url):
            self.response_count += 1
            self.last_activity = time.time()
            self.progress.update(min(40 + self.response_count * 3, 50))

        # Capture images
        content_type = response.headers.get("content-type", "")
        if "image" in content_type and response.status == 200:
            if self._matches_image_pattern(url):
                try:
                    data = await response.body()
                    if len(data) > self.config.image_min_size:
                        self.captured_images.append(data)
                        log_media_capture(data, "image")
                        self.progress.update(min(50 + len(self.captured_images) * 15, 95))
                except:
                    pass

    async def _cleanup(self):
        """Clean up browser resources."""
        await close_browser(self.playwright, self.context)

    # --- Abstract methods (must be implemented by subclasses) ---

    @abstractmethod
    def modify_request_body(self, body: dict, model: str, system_message: str | None, **kwargs) -> bool:
        """Modify request body for model/system message injection.

        Returns True if body was modified.
        """
        pass

    @abstractmethod
    async def extract_response_text(self) -> str:
        """Extract response text from the page."""
        pass

    # --- Hook methods (can be overridden) ---

    def _needs_interception(self, model: str, system_message: str | None, **kwargs) -> bool:
        """Check if request interception is needed."""
        return bool(model or system_message)

    def _get_intercept_pattern(self) -> str:
        """Get the URL pattern for request interception."""
        return "**/*"

    def _is_api_response(self, url: str) -> bool:
        """Check if URL is an API response for progress tracking."""
        return False

    def _matches_image_pattern(self, url: str) -> bool:
        """Check if URL matches image capture patterns."""
        return any(pattern in url for pattern in self.config.image_url_patterns)

    async def _check_error_state(self) -> bool:
        """Check if the page is in an error state."""
        return False

    async def _check_completion(self) -> str | None:
        """Check if response is complete. Returns matched selector or None."""
        for selector in self.config.completion_selectors:
            if await self.page.locator(selector).count() > 0:
                return selector
        return None

    def _select_best_image(self, images: list[bytes]) -> bytes | None:
        """Select the best image - last one that meets quality criteria.

        Progressive renders and streaming mean the last captured image
        is typically the final/complete version.
        """
        if not images:
            return None
        # Return last image (most recent = final render)
        return images[-1]

    async def _extract_images_from_dom(self) -> list[bytes]:
        """Fallback: extract images from DOM."""
        images = []
        try:
            imgs = self.page.locator(f"{self.config.selectors['response']} img")
            for i in range(await imgs.count()):
                src = await imgs.nth(i).get_attribute("src")
                if src and src.startswith("http"):
                    if self._matches_image_pattern(src):
                        resp = await self.page.request.get(src)
                        if resp.ok:
                            data = await resp.body()
                            if len(data) > self.config.image_min_size:
                                images.append(data)
        except:
            pass
        return images
