"""Shared browser utilities for Specter nodes."""

import asyncio
import os
import subprocess
from datetime import datetime
from io import BytesIO

from PIL import Image
from playwright.async_api import async_playwright

from .debug import cleanup_old_dumps, is_debug_dumps_enabled, is_headed_mode
from .session import load_session

# Track if we've already checked/installed Firefox this session
_firefox_installed = False


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
    """Log with timestamp and symbol."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[Specter {ts}] {symbol} {msg}")


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
}
DEFAULT_VIEWPORT = {"width": 767, "height": 1020}  # Below tablet breakpoint, ultrawide height minus taskbar

# Stealth script to remove webdriver flag
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
        )
        page = context.pages[0] if context.pages else await context.new_page()
        browser = None
    else:
        browser = await playwright.firefox.launch(
            headless=headless,
            firefox_user_prefs=FIREFOX_PREFS,
        )
        context = await browser.new_context(viewport=vp, color_scheme="dark")  # type: ignore[arg-type]
        page = await context.new_page()

    await page.add_init_script(STEALTH_SCRIPT)
    return playwright, browser, context, page


async def launch_browser(
    service: str, viewport: dict | None = None, headless: bool | None = None, inject_session: bool = False
):
    """Launch Firefox with persistent profile and restore session.

    Returns (playwright, context, page, session).
    Caller must close context and stop playwright when done.

    Args:
        headless: Override headless mode. None = use settings.
        inject_session: Manually inject cookies/localStorage from saved session.
                       Defaults to False (rely on persistent profile).
                       Can be overridden globally via SPECTER_INJECT_SESSION env var.
    """
    profile_dir = os.path.join(PROFILES_DIR, f"firefox-{service}")
    cleanup_old_dumps()

    headless = headless if headless is not None else not is_headed_mode()
    vp = viewport or DEFAULT_VIEWPORT
    headed_str = ", headed" if not headless else ""
    log(f"Launching Firefox for {service.title()} ({vp['width']}x{vp['height']}{headed_str})...", "◈")

    session = load_session(service)
    log("Found saved session" if session else "No saved session", "○")

    playwright, _, context, page = await create_browser(
        headless=headless,
        viewport=viewport,
        profile_dir=profile_dir,
    )

    # Check if manual injection requested (via param or env var for quick testing)
    inject = inject_session or os.getenv("SPECTER_INJECT_SESSION", "").lower() in ("1", "true", "yes")

    # Inject saved cookies if requested
    if inject and session and session.get("cookies"):
        log("Restoring session cookies...", "○")
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
                # Save trace if SPECTER_TRACE env var is set
                if os.getenv("SPECTER_TRACE", "").lower() in ("1", "true", "yes"):
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
