"""Shared browser utilities for Specter nodes."""

import asyncio
import contextvars
import os
import subprocess
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import Image
from playwright.async_api import async_playwright

from .debug import cleanup_old_dumps, is_debug_dumps_enabled, is_debug_logging_enabled, is_headed_mode, is_trace_enabled
from .session import load_session

# Track if we've already checked/installed Firefox this session
_firefox_installed = False

# Context variable for current operation (e.g., "GrokT2I", "GrokChat")
_log_context: contextvars.ContextVar[str | None] = contextvars.ContextVar("log_context", default=None)


@contextmanager
def log_context(name: str):
    """Set logging context for a block of code. All logs will include this context."""
    token = _log_context.set(name)
    try:
        yield
    finally:
        _log_context.reset(token)


# Cached browser health status
_browser_ready: bool | None = None
_browser_error: str | None = None


def ensure_firefox_installed():
    """Install Playwright Firefox if not already installed."""
    global _firefox_installed
    if _firefox_installed:
        return

    import re
    import sys

    # Get install location from dry-run
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "firefox", "--dry-run"],
        capture_output=True,
        text=True,
    )

    # Parse install location from output
    match = re.search(r"Install location:\s+(\S+)", result.stdout)
    if match:
        install_path = match.group(1)
        if os.path.isdir(install_path):
            _firefox_installed = True
            return

    # Install Firefox
    log("Installing Playwright Firefox (first run)...", "◈")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "firefox"], check=True)
        log("Firefox installed successfully", "✓")
    except Exception as e:
        log(f"Failed to install Firefox: {e}", "✕")
        raise RuntimeError(f"Failed to install Playwright Firefox: {e}") from e

    _firefox_installed = True


def log(msg, symbol="▸"):
    """Log with timestamp, context, and symbol."""
    ts = datetime.now().strftime("%H:%M:%S")
    ctx = _log_context.get()
    ctx_str = f" {ctx}:" if ctx else ""
    print(f"[Specter {ts}]{ctx_str} {symbol} {msg}")


def debug_log(msg, symbol="⌘"):
    """Log only when SPECTER_DEBUG=1 is set."""
    if is_debug_logging_enabled():
        log(msg, symbol)


def _run_browser_check() -> tuple[bool, str | None]:
    """Actually run the browser check (runs in thread)."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            browser.close()
        return True, None
    except Exception as e:
        error_str = str(e)
        if "install-deps" in error_str:
            return False, "Missing system dependencies. Run: sudo playwright install-deps"
        return False, error_str.split("\n")[0]


def check_browser_health() -> tuple[bool, str | None]:
    """Check if browser can launch. Caches result after first call."""
    global _browser_ready, _browser_error
    if _browser_ready is not None:
        return _browser_ready, _browser_error

    # Run in thread to avoid asyncio loop conflict
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(_run_browser_check)
        _browser_ready, _browser_error = future.result(timeout=30)

    return _browser_ready, _browser_error


def is_image_complete(data: bytes) -> bool:
    """Check if image data is complete (not truncated)."""
    if len(data) < 100:
        return False

    # Check JPEG end marker (FFD9)
    if data[:2] == b"\xff\xd8":  # JPEG magic
        return data[-2:] == b"\xff\xd9"

    # Check PNG end chunk (IEND)
    if data[:8] == b"\x89PNG\r\n\x1a\n":  # PNG magic
        return b"IEND" in data[-12:]

    # For other formats, try to decode
    try:
        img = Image.open(BytesIO(data))
        img.load()  # Force full decode
        return True
    except:
        return False


def log_media_capture(data: bytes, media_type: str = "image", source: str = ""):
    """Log captured media with size and dimensions if possible."""
    size_kb = len(data) // 1024
    source_str = f" from {source}" if source else ""

    # Try to get dimensions for images
    if media_type == "image":
        try:
            img = Image.open(BytesIO(data))
            complete = is_image_complete(data)
            status = "" if complete else " [INCOMPLETE]"
            log(f"Captured {media_type}: {img.width}x{img.height} ({size_kb}KB){source_str}{status}", "◆")
            return
        except:
            pass

    # Fallback without dimensions
    log(f"Captured {media_type} ({size_kb}KB){source_str}", "◆")


# Service locks to prevent multiple browser instances on same profile
_service_locks: dict[str, asyncio.Lock] = {}


def get_service_lock(service: str) -> asyncio.Lock:
    """Get or create a lock for the given service."""
    if service not in _service_locks:
        _service_locks[service] = asyncio.Lock()
    return _service_locks[service]


class ProgressTracker:
    """Track progress with optional preview updates.

    Replaces the closure-based pattern used in chat functions.
    """

    def __init__(self, pbar=None, preview: bool = False):
        self.pbar = pbar
        self.preview = preview
        self.current = 0
        self.preview_image = None

    def update(self, step: int, preview_image=None):
        """Update progress if step is higher than current."""
        if not self.pbar:
            return

        # Update stored preview image if provided
        if preview_image:
            self.preview_image = preview_image

        # Track if step actually changed
        step_changed = step > self.current
        if step_changed:
            self.current = step

        # Send update to progress bar
        # Always update if: step changed OR preview image was provided
        if step_changed or preview_image:
            if self.preview and self.preview_image:
                self.pbar.update_absolute(self.current, 100, ("JPEG", self.preview_image, None))
            else:
                self.pbar.update_absolute(self.current, 100)


async def handle_browser_error(page, error: Exception, service: str):
    """Handle browser errors with debug dump."""
    if page:
        try:
            from .debug import dump_debug_info

            await dump_debug_info(page, error, service)
        except:
            pass


async def update_preview(page, width: int = 767, height: int = 1200, target_height: int = 500) -> Image.Image | None:
    """Capture a preview screenshot from the page.

    Args:
        page: Playwright page object
        width: Clip width
        height: Clip height
        target_height: Resize to this height (maintains aspect ratio)

    Returns:
        PIL Image or None on error
    """
    try:
        data = await page.screenshot(type="jpeg", quality=70, clip={"x": 0, "y": 0, "width": width, "height": height})
        img = Image.open(BytesIO(data))
        scale = target_height / img.height
        return img.resize((int(img.width * scale), target_height), Image.Resampling.BILINEAR)
    except Exception as e:
        log(f"Preview screenshot failed: {e}", "⚠")
        return None


# Shared Firefox preferences
FIREFOX_PREFS: dict[str, str | float | bool | int] = {
    "ui.systemUsesDarkTheme": 1,
    # Limit cache to 50MB to prevent profile bloat
    "browser.cache.disk.capacity": 51200,  # 50MB in KB
    "browser.cache.disk.smart_size.enabled": False,  # Disable auto-sizing
    # Disable iframe security restrictions to allow clicking Cloudflare Turnstile
    "security.fileuri.strict_origin_policy": False,
    "privacy.file_unique_origin": False,
    "dom.security.https_first": False,
    # Disable COOP to allow iframe interaction
    "browser.tabs.remote.useCrossOriginOpenerPolicy": False,
    "browser.tabs.remote.useCrossOriginEmbedderPolicy": False,
}
DEFAULT_VIEWPORT = {"width": 767, "height": 1020}  # Below tablet breakpoint, ultrawide height minus taskbar

# Stealth script to remove webdriver flag (minimal to avoid triggering more challenges)
STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""

# Project root is 3 levels up from specter/core/browser.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
USER_DATA_DIR = os.path.join(PROJECT_ROOT, "user_data")
PROFILES_DIR = os.path.join(USER_DATA_DIR, "profiles")
os.makedirs(PROFILES_DIR, exist_ok=True)


async def create_browser(headless: bool = True, viewport: dict | None = None, profile_dir: str | None = None):
    """Create a Firefox browser with standard config.

    Args:
        headless: Run browser headless (default True)
        viewport: Browser viewport size
        profile_dir: Optional path to persistent profile directory

    Returns:
        (playwright, browser, context, page) - Note: browser is None when using profile_dir
    """
    ensure_firefox_installed()
    playwright = await async_playwright().start()
    vp = viewport or DEFAULT_VIEWPORT

    if profile_dir:
        os.makedirs(profile_dir, exist_ok=True)
        context = await playwright.firefox.launch_persistent_context(
            profile_dir,
            headless=headless,
            viewport=vp,  # type: ignore[arg-type]
            firefox_user_prefs=FIREFOX_PREFS,
            color_scheme="dark",
            timeout=10000,  # 10s timeout for browser launch
        )
        page = context.pages[0] if context.pages else await context.new_page()
        browser = None
    else:
        browser = await playwright.firefox.launch(
            headless=headless,
            firefox_user_prefs=FIREFOX_PREFS,
            timeout=10000,  # 10s timeout for browser launch
        )
        context = await browser.new_context(viewport=vp, color_scheme="dark")  # type: ignore[arg-type]
        page = await context.new_page()

    await page.add_init_script(STEALTH_SCRIPT)
    return playwright, browser, context, page


async def launch_browser(
    service: str, viewport: dict | None = None, headless: bool | None = None, purpose: str | None = None
):
    """Launch Firefox with persistent profile and restore session.

    Returns (playwright, context, page, session).
    Caller must close context and stop playwright when done.

    Args:
        headless: Override headless mode. None = use settings.
        purpose: What the browser is for (e.g., "login", "settings", "node").

    Session cookies are always injected from saved session to refresh
    Cloudflare tokens and ensure reliable authentication.
    """
    profile_dir = os.path.join(PROFILES_DIR, f"firefox-{service}")
    cleanup_old_dumps()

    headless = headless if headless is not None else not is_headed_mode()
    vp = viewport or DEFAULT_VIEWPORT
    headed_str = ", headed" if not headless else ""
    purpose_str = f" [{purpose}]" if purpose else ""
    log(f"Launching Firefox for {service.title()}{purpose_str} ({vp['width']}x{vp['height']}{headed_str})...", "◈")

    session = load_session(service)
    log("Found saved session" if session else "No saved session", "○")

    playwright, _, context, page = await create_browser(
        headless=headless,
        viewport=viewport,
        profile_dir=profile_dir,
    )

    # Always inject saved cookies to refresh Cloudflare tokens
    # Persistent profile + fresh cookies from session = best reliability
    if session and session.get("cookies"):
        log("Refreshing session cookies...", "○")
        await context.add_cookies(session["cookies"])

    # Start tracing for debug dumps (if enabled)
    if is_debug_dumps_enabled():
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)

    # Set up debug capture for console errors and network failures
    page._specter_console = []  # type: ignore[attr-defined]
    page._specter_network_errors = []  # type: ignore[attr-defined]

    def on_console(msg):
        if msg.type in ["error", "warning"]:
            page._specter_console.append({"type": msg.type, "text": msg.text})  # type: ignore[attr-defined]

    def on_response(response):
        if response.status >= 400:
            page._specter_network_errors.append(  # type: ignore[attr-defined]
                {"url": response.url, "status": response.status, "method": response.request.method}
            )

    page.on("console", on_console)
    page.on("response", on_response)

    return playwright, context, page, session


async def close_browser(playwright, context, browser=None):
    """Clean up browser resources."""
    if context:
        try:
            # Only handle tracing if debug dumps are enabled
            if is_debug_dumps_enabled():
                if is_trace_enabled():
                    from .debug import DEBUG_DIR

                    now = datetime.now()
                    date_dir = DEBUG_DIR / now.strftime("%Y-%m-%d")
                    date_dir.mkdir(parents=True, exist_ok=True)
                    trace_path = date_dir / f"trace_{now.strftime('%H%M%S')}.zip"
                    await context.tracing.stop(path=str(trace_path))
                    log(f"Trace saved: {trace_path}", "◆")
                    log(f"View: npx playwright show-trace {trace_path}", "◆")
                else:
                    await context.tracing.stop()
        except:
            pass

        try:
            # Force Firefox to flush state to disk before closing
            await context.storage_state()
        except Exception as e:
            print(f"[Specter] Warning: Failed to save state: {e}")

        try:
            await context.close()
        except Exception as e:
            print(f"[Specter] Warning: Failed to close context: {e}")

    if browser:
        try:
            await browser.close()
        except:
            pass

    if playwright:
        try:
            await playwright.stop()
        except:
            pass


class BrowserSession:
    """Manages browser lifecycle with automatic cleanup and debug dumps.

    Use as async context manager for automatic resource management:

        async with BrowserSession("grok", error_context="grok-imagine") as browser:
            await browser.page.goto("https://grok.com")
            # ... work with browser.page ...
            # On any exception, debug info is dumped automatically

    Or manually for more control:

        browser = BrowserSession("grok")
        await browser.start()
        try:
            # ... work ...
        except Exception as e:
            await browser.dump_error(e)
            raise
        finally:
            await browser.close()
    """

    def __init__(
        self,
        service: str,
        viewport: dict | None = None,
        error_context: str | None = None,
        headless: bool | None = None,
        purpose: str = "node",
    ):
        self.service = service
        self.viewport = viewport
        self.error_context = error_context or service
        self.headless = headless
        self.purpose = purpose

        self._lock = get_service_lock(service)
        self.playwright: Any = None
        self.context: Any = None
        self.page: Any = None
        self.session: dict | None = None

    async def start(self) -> "BrowserSession":
        """Start browser session. Acquires service lock with crash recovery."""
        await self._lock.acquire()
        try:
            self.playwright, self.context, self.page, self.session = await launch_browser(
                self.service, viewport=self.viewport, headless=self.headless, purpose=self.purpose
            )
        except Exception as e:
            # Try recovery: clear profile lock and retry once
            error_msg = str(e).lower()
            if "lock" in error_msg or "running" in error_msg or "timeout" in error_msg:
                log("Browser launch failed, attempting recovery...", "⟳")
                await self._clear_profile_lock()
                await asyncio.sleep(1)
                try:
                    self.playwright, self.context, self.page, self.session = await launch_browser(
                        self.service, viewport=self.viewport, headless=self.headless, purpose=self.purpose
                    )
                    log("Recovery successful", "✓")
                    return self
                except Exception:
                    pass  # Fall through to release lock and raise original error
            self._lock.release()
            raise
        return self

    async def _clear_profile_lock(self):
        """Clear Firefox profile lock files after a crash."""
        profile_dir = os.path.join(PROFILES_DIR, f"firefox-{self.service}")
        lock_files = [".parentlock", "lock", "parent.lock"]
        for lock_file in lock_files:
            lock_path = os.path.join(profile_dir, lock_file)
            try:
                if os.path.exists(lock_path):
                    os.remove(lock_path)
                    log(f"Removed stale lock: {lock_file}", "○")
            except Exception:
                pass

    async def dump_error(self, error: Exception):
        """Dump debug info for an error."""
        if self.page:
            await handle_browser_error(self.page, error, self.error_context)

    async def close(self):
        """Close browser and release lock."""
        if self.playwright:
            await close_browser(self.playwright, self.context)
            self.playwright = None
            self.context = None
            self.page = None
        if self._lock.locked():
            self._lock.release()

    async def __aenter__(self) -> "BrowserSession":
        return await self.start()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_val:
            await self.dump_error(exc_val)
        await self.close()
        return False  # Don't suppress exceptions

    async def update_preview_if_enabled(self, progress: ProgressTracker):
        """Update preview if enabled. Call in polling loops."""
        if not progress.preview:
            return
        preview_img = await update_preview(self.page)
        if preview_img:
            progress.update(progress.current, preview_img)

    async def setup_response_handler(self, handler):
        """Setup response event handler."""
        self.page.on("response", handler)

    async def wait_for_login(self, base_url: str, login_selectors: list[str], login_event: str):
        """Navigate, check login, handle if needed."""
        from ..core.session import handle_login_flow, is_logged_in

        await self.page.goto(base_url, timeout=120000, wait_until="domcontentloaded")
        if not await is_logged_in(self.page, login_selectors):
            await self.close()
            new_session = await handle_login_flow(None, self.service, login_event, login_selectors)
            await self.start()
            if new_session and "cookies" in new_session:
                await self.context.add_cookies(new_session["cookies"])
            await self.page.goto(base_url, timeout=120000, wait_until="domcontentloaded")
