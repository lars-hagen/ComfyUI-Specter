"""Debug utilities for Specter - dumps screenshots and page info on failure."""

import json
import os
from datetime import datetime
from pathlib import Path


def _env_bool(name: str) -> bool:
    """Check if env var is set to a truthy value."""
    return os.getenv(name, "").lower() in ("1", "true", "yes")


def is_debug_logging_enabled() -> bool:
    """Check if SPECTER_DEBUG env var is set."""
    return _env_bool("SPECTER_DEBUG")


def is_trace_enabled() -> bool:
    """Check if SPECTER_TRACE env var is set."""
    return _env_bool("SPECTER_TRACE")


# Debug dumps directory
# specter/ directory (parent of core/)
SPECTER_DIR = Path(__file__).parent.parent
DEBUG_DIR = SPECTER_DIR / "debug_dumps"

# Settings file
SETTINGS_PATH = SPECTER_DIR / "settings.json"

_settings: dict | None = None


def load_settings() -> dict:
    """Load settings from file."""
    global _settings
    if _settings is not None:
        return _settings
    try:
        with open(SETTINGS_PATH) as f:
            _settings = json.load(f)
    except FileNotFoundError:
        _settings = {}
    return _settings or {}


def save_settings(settings: dict):
    """Save settings to file."""
    global _settings
    _settings = settings
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def get_setting(key: str, default=None):
    """Get a setting value."""
    return load_settings().get(key, default)


def is_headed_mode() -> bool:
    """Check if headed browser mode is enabled."""
    return bool(get_setting("headed_browser", False))


def is_debug_dumps_enabled() -> bool:
    """Check if debug dumps are enabled (default: True)."""
    return bool(get_setting("debug_dumps", True))


async def dump_debug_info(page, error: Exception, service: str = "unknown"):
    """Dump screenshot and page info on failure.

    Args:
        page: Playwright page object
        error: The exception that occurred
        service: Service name (chatgpt, grok, etc.)
    """
    if page is None:
        return None

    if not is_debug_dumps_enabled():
        return None

    # Create debug directory with date subdirectory
    now = datetime.now()
    date_dir = DEBUG_DIR / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    # Generate timestamp for filenames (time only, date is in folder)
    timestamp = now.strftime("%H%M%S")
    base_name = f"{service}_{timestamp}"

    screenshot_path = date_dir / f"{base_name}.png"
    html_path = date_dir / f"{base_name}.html"
    trace_path = date_dir / f"{base_name}.zip"
    json_path = date_dir / f"{base_name}.json"

    debug_info: dict = {
        "timestamp": datetime.now().isoformat(),
        "service": service,
        "error": str(error),
        "error_type": type(error).__name__,
    }

    try:
        # Capture screenshot
        await page.screenshot(path=str(screenshot_path), full_page=True)
        debug_info["screenshot"] = screenshot_path.name

        # Stop and save trace
        try:
            await page.context.tracing.stop(path=str(trace_path))
            debug_info["trace"] = trace_path.name
        except Exception as e:
            debug_info["trace"] = f"(failed: {e})"

        # Capture minimal DOM structure (no inline content to avoid huge dumps)
        try:
            dom_structure = await page.evaluate("""() => {
                const elements = [];
                document.querySelectorAll('img, button, input, textarea, [contenteditable]').forEach(el => {
                    const info = {
                        tag: el.tagName.toLowerCase(),
                        id: el.id || null,
                        class: el.className || null,
                        alt: el.alt || null,
                        placeholder: el.placeholder || null,
                        type: el.type || null,
                    };
                    // Only include src URL for images, not base64
                    if (el.tagName === 'IMG' && el.src && !el.src.startsWith('data:')) {
                        info.src = el.src;
                    }
                    elements.push(info);
                });
                return elements.slice(0, 50); // Limit to first 50 elements
            }""")
            with open(html_path, "w", encoding="utf-8") as f:
                json.dump(dom_structure, f, indent=2)
            debug_info["html_snapshot"] = html_path.name
        except Exception as e:
            debug_info["html_snapshot"] = f"(failed: {e})"

        # Get page info
        debug_info["url"] = page.url
        debug_info["title"] = await page.title()

        # Get visible text (limited to avoid huge dumps)
        try:
            visible_text = await page.evaluate("""() => {
                const body = document.body;
                if (!body) return '';
                const text = body.innerText || '';
                return text.substring(0, 2000);
            }""")
            debug_info["visible_text"] = visible_text
        except:
            debug_info["visible_text"] = "(failed to capture)"

        # Get localStorage with values (truncated)
        try:
            local_storage = await page.evaluate("({...localStorage})")
            debug_info["localStorage"] = {
                k: (str(v)[:50] + "..." if len(str(v)) > 50 else str(v)) for k, v in local_storage.items()
            }
        except:
            debug_info["localStorage"] = {}

        # Get cookies
        try:
            cookies = await page.context.cookies()
            debug_info["cookies"] = [
                {"name": c["name"], "domain": c["domain"], "secure": c.get("secure", False)}
                for c in cookies[:20]  # Limit to 20 cookies
            ]
        except:
            debug_info["cookies"] = []

        # Get console errors/warnings
        if hasattr(page, "_specter_console"):
            debug_info["console_errors"] = page._specter_console[-50:]  # Last 50 messages
        else:
            debug_info["console_errors"] = []

        # Get network failures
        if hasattr(page, "_specter_network_errors"):
            debug_info["network_errors"] = page._specter_network_errors[-20:]  # Last 20 failures
        else:
            debug_info["network_errors"] = []

        # Get captured URLs (images, videos, API calls)
        if hasattr(page, "_specter_captured_urls"):
            debug_info["captured_urls"] = page._specter_captured_urls[-50:]  # Last 50 URLs
        else:
            debug_info["captured_urls"] = []

        # Extract selector from timeout error if present
        failed_selector = None
        error_str = str(error)
        if 'waiting for locator("' in error_str:
            try:
                start = error_str.index('locator("') + 9
                end = error_str.index('")', start)
                failed_selector = error_str[start:end]
            except:
                pass

        # Validate common selectors + failed selector
        selectors_to_check = {
            "login_indicators": 'a[href*="sign-in"], a[href*="login"], button:has-text("Sign in"), button:has-text("Log in")',
            "input_fields": "input, textarea, [contenteditable]",
            "buttons": "button",
        }
        if failed_selector:
            selectors_to_check["failed_selector"] = failed_selector

        selector_status = {}
        for name, sel in selectors_to_check.items():
            try:
                count = await page.locator(sel).count()
                selector_status[name] = {"selector": sel, "count": count}
            except Exception as e:
                selector_status[name] = {"selector": sel, "error": str(e)}
        debug_info["selectors"] = selector_status

        # Check for common issues
        issues = []
        login_count = selector_status.get("login_indicators", {}).get("count", 0)
        if login_count > 0:
            issues.append(f"Login required ({login_count} login indicators found)")
        if "cloudflare" in (debug_info.get("visible_text", "") or "").lower():
            issues.append("Cloudflare challenge detected")
        if "captcha" in (debug_info.get("visible_text", "") or "").lower():
            issues.append("CAPTCHA detected")
        if failed_selector and selector_status.get("failed_selector", {}).get("count", 0) == 0:
            issues.append(f"Failed selector not found: {failed_selector}")

        # Check console/network errors
        console_errors = debug_info.get("console_errors", [])
        network_errors = debug_info.get("network_errors", [])
        if console_errors:
            error_count = sum(1 for msg in console_errors if msg.get("type") == "error")
            if error_count > 0:
                issues.append(f"{error_count} console error(s) detected")
        if network_errors:
            issues.append(f"{len(network_errors)} failed network request(s)")

        debug_info["detected_issues"] = issues

    except Exception as e:
        debug_info["dump_error"] = str(e)

    # Save JSON
    try:
        with open(json_path, "w") as f:
            json.dump(debug_info, f, indent=2, default=str)
    except Exception as e:
        print(f"[Specter] Failed to save debug JSON: {e}")

    # Print helpful debug info
    print(f"[Specter] Debug dump saved to: {date_dir}")
    print(f"[Specter]   {json_path.name} - error details, captured URLs, detected issues")
    print(f"[Specter]   {screenshot_path.name} - page state at failure")
    print(f"[Specter]   {trace_path.name} - full network log (contains all requests/responses)")
    print(f"[Specter] Quick: cat {json_path} | jq '.captured_urls, .detected_issues'")
    if debug_info.get("trace") and not debug_info["trace"].startswith("(failed"):
        print(f"[Specter] View trace: npx playwright show-trace {trace_path}")
        print(f'[Specter] Network: unzip -p {trace_path} trace.network | grep -o \'"url":"[^"]*"\' | head -50')

    return {
        "screenshot": str(screenshot_path),
        "json": str(json_path),
    }


def cleanup_old_dumps(max_age_days: int = 7):
    """Clean up old debug dump folders."""
    if not DEBUG_DIR.exists():
        return

    import time

    # Remove date folders older than max_age_days
    cutoff_time = time.time() - (max_age_days * 24 * 60 * 60)

    for date_dir in DEBUG_DIR.iterdir():
        if not date_dir.is_dir():
            continue
        try:
            if date_dir.stat().st_mtime < cutoff_time:
                # Remove all files in the directory
                for f in date_dir.iterdir():
                    f.unlink()
                # Remove the directory
                date_dir.rmdir()
        except:
            pass
