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
)
from ..core.session import handle_login_flow, is_logged_in


@dataclass
class ServiceConfig:
    service_name: str
    base_url: str
    selectors: dict  # textarea, send_button, response
    login_selectors: list
    login_event: str  # WebSocket event name for login popup
    image_url_patterns: list = field(default_factory=list)  # URL patterns for captured images
    image_min_size: int = 50000  # Minimum image size in bytes
    response_timeout: int = 90  # Seconds to wait for response
    init_scripts: list = field(default_factory=list)  # Scripts to inject before page load (localStorage setup)
    cookies: list = field(default_factory=list)  # Cookies to add before navigation
    image_ready_selector: str | None = None  # Selector that must exist before accepting captured images


class ChatService(ABC):
    """Abstract base class for chat providers (ChatGPT, Grok, etc.)

    Implements the template method pattern for browser-based chat automation.
    Subclasses override hook methods for service-specific behavior.
    """

    config: ServiceConfig

    def __init__(self):
        self.page: Any = None
        self.context: Any = None
        self.playwright: Any = None
        self.progress: Any = None
        self.captured_images: list[bytes] = []
        self.response_count = 0
        self.listening = False
        self.api_completion: dict | None = None
        self.last_api_body: dict | None = None
        self.api_error: dict | None = None
        self.upload_complete: dict | None = None

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
        Retries once if no results returned but no error occurred.
        """
        max_retries = 3
        last_result = ("", None)
        for attempt in range(1, max_retries + 1):
            try:
                result = await self._chat_impl(prompt, model, image_path, system_message, pbar, preview, **kwargs)
                last_result = result
                # If we got something back, return it
                if result[0] or result[1]:
                    return result
                # Got nothing but no error - retry if attempts remain
                if attempt < max_retries:
                    log(f"No results captured - retrying (attempt {attempt + 1}/{max_retries})", "⟳")
                    await asyncio.sleep(1)
                    continue
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:
                # Retry on 429 (heavy load)
                if "API error 429" in str(e) and attempt < max_retries:
                    log(f"Rate limited (429) - retrying in 2s (attempt {attempt + 1}/{max_retries})", "⟳")
                    await asyncio.sleep(2)
                    continue
                raise
        return last_result

    async def _chat_impl(
        self,
        prompt: str,
        model: str,
        image_path: str | None = None,
        system_message: str | None = None,
        pbar=None,
        preview: bool = False,
        **kwargs,
    ) -> tuple[str, bytes | None]:
        """Internal chat implementation."""
        lock = get_service_lock(self.config.service_name)
        await lock.acquire()

        try:
            # Initialize state AFTER acquiring lock to prevent race conditions
            self.progress = ProgressTracker(pbar, preview)
            self.captured_images = []
            self.response_count = 0
            self.listening = False
            self.api_completion = None
            self.last_api_body = None
            self.api_error = None
            self.upload_complete = None
            self._expect_image = kwargs.get("_expect_image", False)
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
            log(f"Error: {str(e).split(chr(10))[0]}", "✕")
            await handle_browser_error(self.page, e, self.config.service_name)
            raise
        finally:
            await self._cleanup()
            lock.release()

    async def _launch_browser(self):
        self.playwright, self.context, self.page, _ = await launch_browser(self.config.service_name)
        self.page.on("response", self._handle_response)

        for script in self.config.init_scripts:
            await self.page.add_init_script(script)

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
            except Exception:
                pass
            await route.continue_()

        await self.page.route(self._get_intercept_pattern(), intercept)

    async def _navigate_and_login(self):
        if not self.page:
            raise RuntimeError("Browser page not initialized")
        await self.page.goto(self.config.base_url, timeout=120000, wait_until="domcontentloaded")

        if not await is_logged_in(self.page, self.config.login_selectors):
            await self._handle_login()

    async def _handle_login(self):
        await close_browser(self.playwright, self.context)
        self.playwright = None
        self.context = None
        self.page = None

        new_session = await handle_login_flow(
            None, self.config.service_name, self.config.login_event, self.config.login_selectors
        )

        await self._launch_browser()

        if "cookies" in new_session:
            await self.page.context.add_cookies(new_session["cookies"])

        await self.page.goto(self.config.base_url, timeout=120000, wait_until="domcontentloaded")

    async def _wait_for_interface(self):
        await self.page.wait_for_selector(self.config.selectors["textarea"], timeout=30000)

    async def _upload_image(self, image_path: str):
        """Upload image and wait for API completion."""
        log("Attaching input image...", "↑")
        self.upload_complete = None

        try:
            file_input = self.page.locator('input[type="file"]').first
            await file_input.set_input_files(image_path)

            upload_start = time.time()
            while time.time() - upload_start < 30:
                if self.upload_complete:
                    log(f"Upload complete: {self.upload_complete['file_id']}", "✓")
                    return
                await asyncio.sleep(0.1)
            log("Upload timeout - proceeding anyway", "⚠")
        except Exception:
            log("Image upload not available", "⚠")

    async def _send_prompt(self, prompt: str):
        """Send prompt - fill textarea and press Enter."""
        prompt_preview = prompt[:60] + "..." if len(prompt) > 60 else prompt
        log(f'Typing prompt: "{prompt_preview}"', "✎")

        textarea = self.page.locator(self.config.selectors["textarea"]).first
        await textarea.fill(prompt)
        self.progress.update(35)

        self.listening = True
        await self._click_send()
        log("Prompt sent - awaiting response...", "→")
        await asyncio.sleep(0.5)
        await self._update_preview()

    async def _click_send(self):
        """Default: press Enter. Override if provider needs button click."""
        await self.page.keyboard.press("Enter")

    async def _wait_for_response(self) -> tuple[str, list[bytes]]:
        """Wait for response to complete and extract content."""
        # Wait for response container
        await self.page.wait_for_selector(self.config.selectors["response"], timeout=60000)
        self.progress.update(50)

        wait_start = time.time()
        last_preview = 0
        last_image_count = 0
        response_text = ""
        INACTIVITY_TIMEOUT = 50 if self._expect_image else 40
        RETRY_AFTER_NO_RESPONSE = 10  # Retry if no API response after 10s
        retry_count = 0
        max_retries = 2

        while True:
            await asyncio.sleep(0.5)
            elapsed = time.time() - wait_start

            # Check for API error first
            if self.api_error:
                break

            # Check for hard rate limit (quota exceeded - don't retry)
            if await self.page.locator("text=You've reached your current limit").count() > 0:
                from ..core.exceptions import RateLimitException

                log("Rate limit reached - quota exceeded", "✕")
                raise RateLimitException(self.config.service_name)

            # API completion signal (set by _handle_response)
            if self.api_completion:
                response_text = self.api_completion.get("text", "")
                await asyncio.sleep(0.5)  # Brief wait for final images
                break

            # Retry if no API responses after 10s (request may not have been processed)
            if self.response_count == 0 and elapsed >= RETRY_AFTER_NO_RESPONSE and retry_count < max_retries:
                retry_count += 1
                log(f"No response after {RETRY_AFTER_NO_RESPONSE}s - retrying ({retry_count}/{max_retries})", "⟳")
                await self._click_send()
                wait_start = time.time()  # Reset timer
                continue

            # Check max timeout (safety net)
            if elapsed >= self.config.response_timeout:
                log(f"Max timeout reached ({self.config.response_timeout}s)", "⚠")
                break

            # Check for image ready signal (download button visible = image fully rendered)
            if self._expect_image and self.config.image_ready_selector:
                if await self.page.locator(self.config.image_ready_selector).count() > 0:
                    log("Image ready (download button visible)", "✓")
                    response_text = "Image generated"
                    break

            # Track new images and extend timeout (+10s per new image)
            if len(self.captured_images) > last_image_count:
                last_image_count = len(self.captured_images)

            # Check inactivity timeout (extended by image captures)
            effective_timeout = INACTIVITY_TIMEOUT + (last_image_count * 10)
            if elapsed >= effective_timeout:
                if self._expect_image and len(self.captured_images) > 0:
                    log(f"Timeout ({effective_timeout:.0f}s) - completing with captured images", "✓")
                    response_text = "Image generated"
                    break
                log(f"No API response for {INACTIVITY_TIMEOUT}s", "⚠")
                break

            # Update preview every 3 seconds
            if elapsed - last_preview >= 3:
                await self._update_preview()
                last_preview = elapsed

            # Update progress bar
            if int(elapsed) % 10 == 0:
                self.progress.update(min(50 + int(elapsed / 6), 85))

        self.progress.update(95)

        # If API didn't provide text, check for errors
        if not response_text:
            if self.api_error:
                status = self.api_error["status"]
                body = self.api_error["body"]
                error_msg = body.get("error", {}).get("message", f"HTTP {status} error")
                raise Exception(f"API error {status}: {error_msg}")

            # Extract from DOM (normal for ChatGPT SSE streams)
            response_text = await self.extract_response_text()
            # If we have images but no text, that's OK for image generation
            if not response_text and self.captured_images and self._expect_image:
                response_text = "Image generated"
            elif not response_text:
                raise Exception("No response text or images")

        # Try DOM fallback for images (only if expecting images)
        if self._expect_image and not self.captured_images:
            log("No images from network capture, trying DOM fallback...", "○")
            self.captured_images = await self._extract_images_from_dom()
            if self.captured_images:
                log(f"DOM fallback found {len(self.captured_images)} image(s)", "✓")
            else:
                log("DOM fallback found no images", "⚠")

        self.progress.update(95)
        await self._update_preview()

        return response_text, self.captured_images

    async def _update_preview(self):
        if not self.progress.preview:
            return
        from ..core.browser import BrowserSession

        session = BrowserSession(self.config.service_name)
        session.page = self.page
        await session.update_preview_if_enabled(self.progress)

    async def _handle_response(self, response):
        url = response.url

        # Track uploads (regardless of listening state - uploads happen before prompt)
        if self._is_api_response(url):
            try:
                import json

                text = await response.text()

                # Check for upload completion (always check, even before listening)
                if response.status >= 200 and response.status < 300:
                    for line in text.split("\n"):
                        if line.strip():
                            try:
                                body = json.loads(line)
                                upload_signal = await self.handle_upload_response_body(body)
                                if upload_signal and upload_signal.get("complete"):
                                    self.upload_complete = upload_signal
                                    # Don't return - continue to check for text completion below
                                    break
                            except json.JSONDecodeError:
                                pass
            except Exception:
                pass

        # Only track text responses if we're listening (after prompt sent)
        if not self.listening:
            return

        # Track API responses for progress
        if self._is_api_response(url):
            self.response_count += 1
            self.progress.update(min(40 + self.response_count * 3, 50))

            # Parse API response bodies
            try:
                import json

                text = await response.text()

                # Check for error responses (4xx, 5xx)
                if response.status >= 400:
                    try:
                        error_body = json.loads(text)
                        self.api_error = {
                            "status": response.status,
                            "body": error_body,
                            "url": url,
                        }
                        error_msg = error_body.get("error", {}).get("message", f"HTTP {response.status}")
                        log(f"API error {response.status}: {error_msg}", "✕")
                    except json.JSONDecodeError:
                        self.api_error = {
                            "status": response.status,
                            "body": text,
                            "url": url,
                        }
                        log(f"API error {response.status}", "✕")
                    return

                # Parse success responses (200-299)
                if response.status >= 200 and response.status < 300:
                    # Quick completion detection (ChatGPT SSE format)
                    # Skip for image gen - wait for download button instead
                    if "message_stream_complete" in text and not self._expect_image:
                        log("Stream complete - extracting from DOM", "✓")
                        self.api_completion = {"complete": True, "text": ""}
                    # Parse JSON lines for Grok completion detection
                    for line in text.splitlines():
                        line = line.strip()
                        if line.startswith("data:"):
                            line = line[5:].lstrip()
                        if not line or line.startswith("event:") or line == "[DONE]":
                            continue
                        try:
                            body = json.loads(line)
                            signal = await self.handle_api_response_body(body)
                            if signal and signal.get("complete"):
                                self.api_completion = signal
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass

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
                except Exception:
                    pass

    async def _cleanup(self):
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

    async def handle_api_response_body(self, body: dict) -> dict | None:
        """Parse API response body for completion signals.

        Returns dict with:
        - "complete": bool - whether response is finished
        - "text": str - extracted text (if available)
        - "metadata": dict - any metadata

        Returns None if not applicable (base implementation).
        """
        return None

    async def handle_upload_response_body(self, body: dict) -> dict | None:
        """Parse upload completion signals from API responses.

        Returns dict with:
        - "complete": bool - upload finished
        - "file_id": str - uploaded file ID
        - "metadata": dict - provider metadata

        Returns None if not applicable (base implementation).
        """
        return None

    def _needs_interception(self, model: str, system_message: str | None, **kwargs) -> bool:
        return bool(model or system_message)

    def _get_intercept_pattern(self) -> str:
        return "**/*"

    def _is_api_response(self, url: str) -> bool:
        return False

    def _matches_image_pattern(self, url: str) -> bool:
        return any(pattern in url for pattern in self.config.image_url_patterns)

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
        except Exception:
            pass
        return images
