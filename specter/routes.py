import json

from aiohttp import web
from server import PromptServer

from .core.browser import (
    DARK_THEME_SCRIPT,
    delete_session,
    load_session,
    load_settings,
    log,
    parse_cookies,
    save_session,
    save_settings,
)
from .login_stream import browser_stream
from .providers import AGE_VERIFICATION_SCRIPT, DISMISS_NOTIFICATIONS_SCRIPT, GROK_LOGIN_SELECTORS


def _error_response(error: Exception, context: str):
    error_str = str(error)
    if error_str and "install-deps" in error_str:
        msg = "Browser not installed. Run: patchright install chrome"
    elif error_str:
        msg = error_str.split("\n")[0].strip()
    else:
        msg = f"{type(error).__name__}: {error!r}"
    print(f"[Specter] ERROR {context}: {msg}")
    return web.json_response({"status": "error", "message": msg}, status=500)


# Service configs for login flows
SERVICE_CONFIGS = {
    "chatgpt": {
        "service": "chatgpt",
        "login_url": "https://chatgpt.com/auth/login",
        "login_selectors": ['button:has-text("Log in")', 'a:has-text("Log in")', 'button:has-text("Sign up")'],
        "success_url_contains": "chatgpt.com",
        "success_url_excludes": "/auth/",
        "workspace_selector": '[data-testid="modal-workspace-switcher"]',
        "settings_url": "https://chatgpt.com/#settings",
        "init_scripts": [DARK_THEME_SCRIPT, "localStorage.removeItem('RESUME_TOKEN_STORE_KEY');"],
        "cookies": [{"name": "oai-allow-ne", "value": "true", "domain": ".chatgpt.com", "path": "/"}],
    },
    "grok": {
        "service": "grok",
        "login_url": "https://accounts.x.ai/sign-in?redirect=grok-com",
        "login_selectors": GROK_LOGIN_SELECTORS,
        "success_url_contains": "grok.com",
        "success_url_excludes": "/sign-in",
        "workspace_selector": None,
        "settings_url": "https://grok.com/imagine?_s=home",
        "init_scripts": [DARK_THEME_SCRIPT, AGE_VERIFICATION_SCRIPT, DISMISS_NOTIFICATIONS_SCRIPT],
        "cookies": [],
    },
    "gemini": {
        "service": "gemini",
        "login_url": "https://accounts.google.com/ServiceLogin?passive=1209600&continue=https://gemini.google.com/app&followup=https://gemini.google.com/app",
        "login_selectors": ['input[type="email"]', 'input[type="password"]'],
        "success_url_contains": "gemini.google.com/app",
        "success_url_excludes": "accounts.google.com",
        "workspace_selector": None,
        "settings_url": "https://gemini.google.com/app",
        "init_scripts": [DARK_THEME_SCRIPT],
        "cookies": [],
    },
}


def get_service_config(service: str) -> dict:
    if service not in SERVICE_CONFIGS:
        raise ValueError(f"Unknown service: {service}")
    return SERVICE_CONFIGS[service]


def check_browser_health() -> tuple[bool, str | None]:
    return True, None


@PromptServer.instance.routes.get("/specter/health")
async def health(_request):
    ready, error = check_browser_health()
    return web.json_response({"ready": ready, "error": error})


@PromptServer.instance.routes.get("/specter/{service}/status")
async def get_service_status(request):
    service = request.match_info["service"]
    session = load_session(service)
    logged_in = session is not None and len(session.get("cookies", [])) > 0
    return web.json_response({"logged_in": logged_in})


@PromptServer.instance.routes.post("/specter/{service}/logout")
async def trigger_service_logout(request):
    service = request.match_info["service"]
    # Close browser if running for this service
    if browser_stream.current_service == service and browser_stream.session_id:
        log(f"Closing browser before logout for {service}", "○")
        await browser_stream.stop()
    deleted = delete_session(service)
    return web.json_response({"status": "ok", "deleted": deleted})


@PromptServer.instance.routes.post("/specter/{service}/import")
async def import_cookies(request):
    service = request.match_info["service"]
    try:
        data = await request.json()
        cookies = parse_cookies(data.get("cookies", ""))
        if not cookies:
            return web.json_response({"status": "error", "message": "No valid cookies found"}, status=400)
        save_session(service, {"cookies": cookies})
        log(f"Imported {len(cookies)} cookies for {service}", "✓")
        return web.json_response({"status": "ok", "count": len(cookies)})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


@PromptServer.instance.routes.post("/specter/reset")
async def reset_all_data(_request):
    """Clear all session data for all services."""
    results = {service: delete_session(service) for service in SERVICE_CONFIGS}
    return web.json_response({"status": "ok", "deleted": results})


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
    if service not in SERVICE_CONFIGS:
        return web.json_response({"status": "error", "message": f"Unknown service: {service}"}, status=400)
    ready, error = check_browser_health()
    if not ready:
        return web.json_response({"status": "error", "message": error}, status=500)
    settings_url = SERVICE_CONFIGS[service].get("settings_url")
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
async def stop_browser(request):
    session_id = browser_stream.session_id
    sid = session_id[:8] if session_id else "none"
    try:
        log(f"[{sid}] Stop requested from frontend ({request.remote})", "○")
        await browser_stream.stop()
        return web.json_response({"status": "ok"})
    except Exception as e:
        log(f"[{sid}] Stop browser error: {e}", "✕")
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
