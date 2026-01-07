"""API routes for Specter - handles auth triggers from frontend."""

import json

from aiohttp import web
from server import PromptServer

from .browser import browser_stream
from .core.browser import check_browser_health, log
from .core.debug import load_settings, save_settings
from .core.session import delete_session, load_session, save_session
from .providers.chatgpt import ChatGPTService
from .providers.grok import GrokService


def _clean_error(error: str) -> str:
    """Extract clean error message from verbose Playwright output."""
    if "install-deps" in error:
        return "Missing system dependencies. Run: sudo playwright install-deps"
    # Get first meaningful line
    return error.split("\n")[0].strip()


def _error_response(error: Exception, context: str):
    """Log error and return JSON error response."""
    error_str = str(error)
    msg = _clean_error(error_str) if error_str else f"{type(error).__name__}: {error!r}"
    print(f"[Specter] ERROR {context}: {msg}")
    return web.json_response({"status": "error", "message": msg}, status=500)


# Map service names to provider classes
SERVICE_PROVIDERS = {
    "chatgpt": ChatGPTService,
    "grok": GrokService,
}

# Login-specific config (URLs and success patterns - not duplicated in providers)
LOGIN_CONFIGS = {
    "chatgpt": {
        "login_url": "https://chatgpt.com/auth/login",
        "success_url_contains": "chatgpt.com",
        "success_url_excludes": "/auth/",
        "workspace_selector": '[data-testid="modal-workspace-switcher"]',
        "settings_url": "https://chatgpt.com/#settings",
    },
    "grok": {
        "login_url": "https://accounts.x.ai/sign-in?redirect=grok-com",
        "success_url_contains": "grok.com",
        "success_url_excludes": "/sign-in",
        "workspace_selector": None,
        "settings_url": "https://grok.com/imagine?_s=home",
        # DISABLED: post_login_url triggers Cloudflare challenge
        # "post_login_url": "https://grok.com/imagine",
    },
}


def get_service_config(service: str) -> dict:
    """Get configuration for a service, combining provider config with login config."""
    if service not in SERVICE_PROVIDERS:
        raise ValueError(f"Unknown service: {service}")
    provider_class = SERVICE_PROVIDERS[service]
    login_cfg = LOGIN_CONFIGS[service]
    provider_cfg = provider_class.config

    return {
        "service": provider_cfg.service_name,
        "login_url": login_cfg["login_url"],
        "login_selectors": provider_cfg.login_selectors,
        "success_url_contains": login_cfg["success_url_contains"],
        "success_url_excludes": login_cfg["success_url_excludes"],
        "workspace_selector": login_cfg.get("workspace_selector"),
        "post_login_url": login_cfg.get("post_login_url"),
        "init_scripts": provider_cfg.init_scripts,
        "cookies": provider_cfg.cookies,
    }


# =============================================================================
# HEALTH CHECK
# =============================================================================


@PromptServer.instance.routes.get("/specter/health")
async def health(_request):
    """Check if browser is ready to launch."""
    ready, error = check_browser_health()
    return web.json_response({"ready": ready, "error": error})


# =============================================================================
# SERVICE-SPECIFIC ROUTES (parameterized)
# =============================================================================


@PromptServer.instance.routes.get("/specter/{service}/status")
async def get_service_status(request):
    """Check if logged in to a service."""
    service = request.match_info["service"]
    session = load_session(service)
    has_session = session is not None and len(session.get("cookies", [])) > 0
    return web.json_response({"logged_in": has_session})


@PromptServer.instance.routes.post("/specter/{service}/logout")
async def trigger_service_logout(request):
    """Clear session for a service."""
    service = request.match_info["service"]
    delete_session(service)
    return web.json_response({"status": "ok"})


@PromptServer.instance.routes.post("/specter/{service}/browser/start")
async def start_service_browser(request):
    """Start embedded browser for login to a service."""
    service = request.match_info["service"]
    # Check browser health first
    ready, error = check_browser_health()
    if not ready:
        return web.json_response({"status": "error", "message": error}, status=500)
    config = get_service_config(service)
    try:
        await browser_stream.start(
            url=config["login_url"],
            width=600,
            height=800,
            login_config=config,
            purpose="login",
        )
        return web.json_response({"status": "ok"})
    except Exception as e:
        return _error_response(e, f"starting browser for {service}")


@PromptServer.instance.routes.post("/specter/{service}/settings/start")
async def start_service_settings(request):
    """Start embedded browser for provider settings page."""
    service = request.match_info["service"]
    if service not in LOGIN_CONFIGS:
        return web.json_response({"status": "error", "message": f"Unknown service: {service}"}, status=400)
    # Check browser health first
    ready, error = check_browser_health()
    if not ready:
        return web.json_response({"status": "error", "message": error}, status=500)
    settings_url = LOGIN_CONFIGS[service].get("settings_url")
    if not settings_url:
        return web.json_response({"status": "error", "message": f"No settings URL for {service}"}, status=400)
    config = get_service_config(service)
    try:
        # Open settings window exactly like login window (same dimensions, same config)
        await browser_stream.start(
            url=settings_url,
            width=600,
            height=800,
            login_config={**config, "detect_login": False},
            purpose="settings",
        )
        return web.json_response({"status": "ok"})
    except Exception as e:
        return _error_response(e, f"starting settings for {service}")


# =============================================================================
# LEGACY ROUTES (for backwards compatibility)
# =============================================================================


@PromptServer.instance.routes.get("/specter/status")
async def get_status(_request):
    """Check if logged in to ChatGPT (legacy route)."""
    session = load_session("chatgpt")
    has_session = session is not None and len(session.get("cookies", [])) > 0
    return web.json_response({"logged_in": has_session})


@PromptServer.instance.routes.post("/specter/logout")
async def trigger_logout(_request):
    """Clear ChatGPT session (legacy route)."""
    delete_session("chatgpt")
    return web.json_response({"status": "ok"})


@PromptServer.instance.routes.post("/specter/browser/start")
async def start_browser(_request):
    """Start embedded browser for ChatGPT login (legacy route)."""
    ready, error = check_browser_health()
    if not ready:
        return web.json_response({"status": "error", "message": error}, status=500)
    config = get_service_config("chatgpt")
    try:
        await browser_stream.start(
            url=config["login_url"],
            width=600,
            height=800,
            login_config=config,
            purpose="login",
        )
        return web.json_response({"status": "ok"})
    except Exception as e:
        return _error_response(e, "starting browser")


# =============================================================================
# SHARED ROUTES
# =============================================================================


@PromptServer.instance.routes.post("/specter/browser/stop")
async def stop_browser(_request):
    """Stop embedded browser and save session if logged in."""
    try:
        service = browser_stream.current_service or "chatgpt"
        if await browser_stream.is_logged_in():
            storage: dict = await browser_stream.get_storage_state()  # type: ignore
            if storage:
                save_session(service, storage)  # Only cookies - profiles handle localStorage
                cookie_count = len(storage.get("cookies", []))
                log(f"Login successful! Session saved for {service.title()} ({cookie_count} cookies)", "★")
        await browser_stream.stop()
        return web.json_response({"status": "ok"})
    except Exception as e:
        return _error_response(e, "stopping browser")


@PromptServer.instance.routes.post("/specter/browser/navigate")
async def browser_navigate(request):
    """Navigate current browser to a URL (for testing)."""
    try:
        data = await request.json()
        url = data.get("url")
        if not url:
            return web.json_response({"status": "error", "message": "URL required"}, status=400)

        if not browser_stream.page:
            return web.json_response({"status": "error", "message": "No browser running"}, status=400)

        log(f"Navigating to {url}...", "→")
        await browser_stream.page.goto(url, timeout=60000, wait_until="commit")
        return web.json_response({"status": "ok"})
    except Exception as e:
        return _error_response(e, "navigating browser")


@PromptServer.instance.routes.get("/specter/browser/ws")
async def browser_websocket(request):
    """WebSocket endpoint for browser stream."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    browser_stream.clients.add(ws)

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                event = json.loads(msg.data)
                await browser_stream.handle_event(event)
            elif msg.type == web.WSMsgType.ERROR:
                break
    finally:
        browser_stream.clients.discard(ws)

    return ws


@PromptServer.instance.routes.get("/specter/settings")
async def get_settings(_request):
    """Get current settings."""
    return web.json_response(load_settings())


@PromptServer.instance.routes.post("/specter/settings")
async def update_settings(request):
    """Update settings."""
    data = await request.json()
    settings = load_settings()
    settings.update(data)
    save_settings(settings)
    return web.json_response({"status": "ok"})
