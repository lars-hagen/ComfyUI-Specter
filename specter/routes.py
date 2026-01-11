import json

from aiohttp import web
from server import PromptServer

from .browser import browser_stream
from .core.browser import check_browser_health, log
from .core.debug import load_settings, save_settings
from .core.session import delete_session, load_session, save_session
from .providers.chatgpt import ChatGPTService
from .providers.grok import GrokService


def _error_response(error: Exception, context: str):
    error_str = str(error)
    if error_str and "install-deps" in error_str:
        msg = "Missing system dependencies. Run: sudo playwright install-deps"
    elif error_str:
        msg = error_str.split("\n")[0].strip()
    else:
        msg = f"{type(error).__name__}: {error!r}"
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
    },
}


def get_service_config(service: str) -> dict:
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


@PromptServer.instance.routes.get("/specter/health")
async def health(_request):
    ready, error = check_browser_health()
    return web.json_response({"ready": ready, "error": error})


@PromptServer.instance.routes.get("/specter/{service}/status")
async def get_service_status(request):
    service = request.match_info["service"]
    session = load_session(service)
    has_session = session is not None and len(session.get("cookies", [])) > 0
    return web.json_response({"logged_in": has_session})


@PromptServer.instance.routes.post("/specter/{service}/logout")
async def trigger_service_logout(request):
    service = request.match_info["service"]
    delete_session(service)
    return web.json_response({"status": "ok"})


@PromptServer.instance.routes.post("/specter/{service}/browser/start")
async def start_service_browser(request):
    service = request.match_info["service"]
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
    service = request.match_info["service"]
    if service not in LOGIN_CONFIGS:
        return web.json_response({"status": "error", "message": f"Unknown service: {service}"}, status=400)
    ready, error = check_browser_health()
    if not ready:
        return web.json_response({"status": "error", "message": error}, status=500)
    settings_url = LOGIN_CONFIGS[service].get("settings_url")
    if not settings_url:
        return web.json_response({"status": "error", "message": f"No settings URL for {service}"}, status=400)
    config = get_service_config(service)
    try:
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


@PromptServer.instance.routes.post("/specter/browser/stop")
async def stop_browser(_request):
    try:
        service = browser_stream.current_service or "default"
        if await browser_stream.is_logged_in():
            storage: dict = await browser_stream.get_storage_state()  # type: ignore
            if storage:
                save_session(service, storage)
                cookie_count = len(storage.get("cookies", []))
                log(f"Login successful! Session saved for {service.title()} ({cookie_count} cookies)", "★")
        await browser_stream.stop()
        return web.json_response({"status": "ok"})
    except Exception as e:
        return _error_response(e, "stopping browser")


@PromptServer.instance.routes.post("/specter/browser/navigate")
async def browser_navigate(request):
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
    return web.json_response(load_settings())


@PromptServer.instance.routes.post("/specter/settings")
async def update_settings(request):
    data = await request.json()
    settings = load_settings()
    settings.update(data)
    save_settings(settings)
    return web.json_response({"status": "ok"})
