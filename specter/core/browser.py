"""Minimal browser utilities for Specter."""

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from io import BytesIO
from pathlib import Path

from patchright.async_api import ViewportSize, async_playwright
from PIL import Image

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
USER_DATA_DIR = PROJECT_ROOT / "user_data"
SESSION_DIR = USER_DATA_DIR / "sessions"
TRACE_DIR = USER_DATA_DIR / "traces"
SETTINGS_PATH = PROJECT_ROOT / "specter" / "settings.json"
SESSION_DIR.mkdir(parents=True, exist_ok=True)


def is_trace_enabled() -> bool:
    return os.environ.get("SPECTER_TRACE", "").lower() in ("1", "true", "yes")


def is_debug_enabled() -> bool:
    return os.environ.get("SPECTER_DEBUG", "").lower() in ("1", "true", "yes")


# Log context
_log_context: ContextVar[str | None] = ContextVar("log_context", default=None)


@contextmanager
def log_context(name: str):
    token = _log_context.set(name)
    try:
        yield
    finally:
        _log_context.reset(token)


def log(msg: str, symbol: str = "▸"):
    ts = datetime.now().strftime("%H:%M:%S")
    ctx = _log_context.get()
    ctx_str = f" {ctx}:" if ctx else ""
    print(f"[Specter {ts}]{ctx_str} {symbol} {msg}")


def debug_log(msg: str):
    """Print debug message if SPECTER_DEBUG is enabled."""
    if is_debug_enabled():
        log(msg, "⌘")


# Chrome args for speed and stealth
# Note: Some flags commented out as they may interfere with Cloudflare Turnstile autosolve
CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--disable-software-rasterizer",
    # "--disable-background-timer-throttling",
    # "--disable-backgrounding-occluded-windows",
    # "--disable-renderer-backgrounding",
    # "--disable-ipc-flooding-protection",
    # "--disable-hang-monitor",
    # "--disable-features=CalculateNativeWinOcclusion,Translate,MediaRouter,OptimizationHints",
    # "--disable-background-networking",
    # "--disable-breakpad",
    # "--disable-component-update",
    # "--disable-domain-reliability",
    # "--disable-client-side-phishing-detection",
    # "--disable-sync",
    # "--disable-extensions",
    # "--metrics-recording-only",
    # "--no-first-run",
    # "--no-default-browser-check",
    # "--deny-permission-prompts",
    # "--disable-notifications",
    # "--noerrdialogs",
    "--mute-audio"
]

VIEWPORT: ViewportSize = {"width": 767, "height": 1020}
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
DARK_THEME_SCRIPT = "localStorage.setItem('theme', 'dark'); localStorage.setItem('oai/apps/theme', 'dark');"


def load_session(service: str) -> dict | None:
    path = SESSION_DIR / f"{service}_session.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            return None
    return None


def save_session(service: str, data: dict):
    (SESSION_DIR / f"{service}_session.json").write_text(json.dumps(data))


def delete_session(service: str) -> bool:
    """Delete session for service. Returns True if deleted."""
    session_path = SESSION_DIR / f"{service}_session.json"
    if session_path.exists():
        try:
            session_path.unlink()
            log(f"Deleted session for {service}", "✓")
            return True
        except Exception as e:
            log(f"Failed to delete session for {service}: {e}", "✕")
    return False


def parse_cookies(content: str) -> list[dict]:
    content = content.strip()
    if content.startswith("["):
        cookies = json.loads(content)
        return [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
                "expires": c.get("expirationDate", -1),
                "sameSite": {"no_restriction": "None", "lax": "Lax", "strict": "Strict"}.get(c.get("sameSite", "lax"), "Lax"),
            }
            for c in cookies
        ]
    # Netscape TXT format
    cookies = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies.append({
                "name": parts[5], "value": parts[6], "domain": parts[0], "path": parts[2],
                "secure": parts[3].upper() == "TRUE", "httpOnly": False,
                "expires": int(parts[4]) if parts[4] != "0" else -1, "sameSite": "Lax",
            })
    return cookies


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except:
            pass
    return {}


def save_settings(settings: dict):
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


def is_headed() -> bool:
    return load_settings().get("headed_browser", False)


async def launch_browser(
    service: str,
    headed: bool | None = None,
    viewport: ViewportSize | None = None,
    enable_tracing: bool | None = None,
):
    """Launch browser. Returns: (playwright, context, page, cookies)"""
    if headed is None:
        headed = is_headed()
    if enable_tracing is None:
        enable_tracing = is_trace_enabled()

    log(f"Launching browser for {service} ({'headed' if headed else 'headless'})...", "◈")

    session = load_session(service)
    cookies = session.get("cookies", []) if session else []
    if session:
        log(f"Loaded {len(cookies)} cookies", "○")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel="chrome", headless=not headed, args=CHROME_ARGS)

    # Use storage_state to restore cookies + localStorage (CF tokens)
    context = await browser.new_context(
        viewport=viewport or VIEWPORT,
        user_agent=USER_AGENT,
        storage_state=session if session else None,
    )
    # CRITICAL: Disable Patchright's route injection to prevent cross-domain navigation errors
    context._impl_obj.route_injecting = True

    # Start trace if enabled
    if enable_tracing:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)
        context._specter_trace_service = service  # type: ignore[attr-defined]
        log("Tracing enabled", "◆")

    page = await context.new_page()

    return pw, context, page, cookies


async def close_browser(pw, context, browser=None):
    """Close browser. Browser arg is optional for backwards compat."""
    try:
        # Save trace if it was running
        if context and hasattr(context, "_specter_trace_service"):
            service = context._specter_trace_service
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            trace_path = TRACE_DIR / f"{service}_{ts}.zip"
            await context.tracing.stop(path=str(trace_path))
            log(f"Trace saved: npx playwright show-trace {trace_path}", "◆")
    except:
        pass
    try:
        if context:
            await context.close()
    except:
        pass
    try:
        b = browser or (context.browser if context else None)
        if b:
            await b.close()
    except:
        pass
    try:
        if pw:
            await pw.stop()
    except:
        pass


async def create_browser(headed: bool = True, viewport: ViewportSize | None = None):
    """Create browser for CLI tools. Returns: (playwright, browser, context, page)"""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel="chrome", headless=not headed, args=CHROME_ARGS)
    context = await browser.new_context(
        viewport=viewport or VIEWPORT,
        user_agent=USER_AGENT,
    )
    # CRITICAL: Disable Patchright's route injection to prevent cross-domain navigation errors
    context._impl_obj.route_injecting = True
    page = await context.new_page()
    await page.add_init_script(DARK_THEME_SCRIPT)
    return pw, browser, context, page


async def is_logged_in(page, login_selectors: list[str]) -> bool:
    """Check if logged in by looking for login buttons."""
    try:
        await page.wait_for_load_state("domcontentloaded")
        for selector in login_selectors:
            if await page.locator(selector).count() > 0:
                return False
        return True
    except:
        return False


async def handle_login(service: str, event_name: str, login_selectors: list[str]) -> dict:
    """Handle login flow - send event and wait for session."""
    import asyncio

    from server import PromptServer

    log(f"Not logged in to {service} - opening authentication popup...", "⚠")
    PromptServer.instance.send_sync(event_name, {})

    log("Waiting for login to complete...", "◌")
    timeout, elapsed = 300, 0

    while elapsed < timeout:
        await asyncio.sleep(2)
        elapsed += 2
        session = load_session(service)
        if session:
            log("Login detected!", "●")
            return session

    raise Exception(f"Login timed out after 5 minutes for {service}")


class ProgressTracker:
    def __init__(self, pbar=None, preview: bool = False):
        self.pbar = pbar
        self.preview = preview
        self.current = 0
        self.preview_image = None
        self._preview_task = None

    def update(self, step: int, preview_image=None):
        if not self.pbar:
            return
        if preview_image:
            self.preview_image = preview_image
        if step > self.current:
            self.current = step
        if self.preview and self.preview_image:
            self.pbar.update_absolute(self.current, 100, ("JPEG", self.preview_image, None))
        else:
            self.pbar.update_absolute(self.current, 100)

    def update_async(self, step: int, page=None):
        """Update progress and capture preview in parallel (non-blocking)."""
        import asyncio
        if not self.pbar:
            return
        if step > self.current:
            self.current = step

        # Start preview capture in background if needed
        if self.preview and page and (self._preview_task is None or self._preview_task.done()):
            self._preview_task = asyncio.create_task(self._capture_and_update(step, page))
        elif not self.preview:
            self.pbar.update_absolute(self.current, 100)

    async def _capture_and_update(self, step: int, page):
        """Capture preview and update progress bar (runs in background)."""
        try:
            preview_img = await capture_preview(page)
            if preview_img and self.pbar:
                self.preview_image = preview_img
                if step >= self.current:  # Only update if still current
                    self.pbar.update_absolute(step, 100, ("JPEG", preview_img, None))
        except Exception:
            pass  # Ignore preview capture errors


async def capture_preview(page, height: int = 1200) -> Image.Image | None:
    try:
        data = await page.screenshot(type="jpeg", quality=70, clip={"x": 0, "y": 0, "width": 767, "height": height})
        img = Image.open(BytesIO(data))
        scale = 500 / img.height
        return img.resize((int(img.width * scale), 500), Image.Resampling.BILINEAR)
    except:
        return None
