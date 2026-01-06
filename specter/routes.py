"""API routes for Specter - handles auth triggers from frontend."""

import json

from aiohttp import web
from server import PromptServer

from .browser import browser_stream
from .core.debug import load_settings, save_settings
from .core.session import delete_session, load_session, save_session
from .providers.chatgpt import ChatGPTService
from .providers.grok import GrokService

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
        "init_scripts": provider_cfg.init_scripts,
        "cookies": provider_cfg.cookies,
    }


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
    config = get_service_config(service)
    try:
        await browser_stream.start(
            url=config["login_url"],
            width=600,
            height=800,
            login_config=config,
        )
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


@PromptServer.instance.routes.post("/specter/{service}/settings/start")
async def start_service_settings(request):
    """Start embedded browser for provider settings page."""
    service = request.match_info["service"]
    if service not in LOGIN_CONFIGS:
        return web.json_response({"status": "error", "message": f"Unknown service: {service}"}, status=400)
    settings_url = LOGIN_CONFIGS[service].get("settings_url")
    if not settings_url:
        return web.json_response({"status": "error", "message": f"No settings URL for {service}"}, status=400)
    config = get_service_config(service)
    try:
        # Open settings - launch_browser handles session, disable login detection
        await browser_stream.start(
            url=settings_url,
            width=800,
            height=900,
            login_config={**config, "detect_login": False},
        )
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


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
    config = get_service_config("chatgpt")
    try:
        await browser_stream.start(
            url=config["login_url"],
            width=600,
            height=800,
            login_config=config,
        )
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


# =============================================================================
# SHARED ROUTES
# =============================================================================


@PromptServer.instance.routes.post("/specter/browser/stop")
async def stop_browser(_request):
    """Stop embedded browser and save session if logged in."""
    try:
        if await browser_stream.is_logged_in():
            storage: dict = await browser_stream.get_storage_state()  # type: ignore
            if storage:
                service = browser_stream.current_service or "chatgpt"
                save_session(service, storage)  # Only cookies - profiles handle localStorage
        await browser_stream.stop()
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


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
