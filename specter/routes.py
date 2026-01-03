"""API routes for Specter - handles auth triggers from frontend."""

import json
from aiohttp import web
from server import PromptServer

from .session import delete_session, save_session, load_session
from .browser import browser_stream

# ChatGPT login configuration
CHATGPT_CONFIG = {
    "login_url": "https://chatgpt.com/auth/login",
    "login_selectors": [
        'button:has-text("Log in")',
        'a:has-text("Log in")',
        'button:has-text("Sign up")',
        'a:has-text("Sign up")',
    ],
    "success_url_contains": "chatgpt.com",
    "success_url_excludes": "/auth/",
    "workspace_selector": '[data-testid="modal-workspace-switcher"]',
}


@PromptServer.instance.routes.get("/specter/status")
async def get_status(_request):
    """Check if logged in."""
    session = load_session("chatgpt")
    has_session = session is not None and len(session.get("cookies", [])) > 0
    return web.json_response({"logged_in": has_session})


@PromptServer.instance.routes.post("/specter/logout")
async def trigger_logout(_request):
    """Clear ChatGPT session."""
    delete_session("chatgpt")
    return web.json_response({"status": "ok"})


@PromptServer.instance.routes.post("/specter/browser/start")
async def start_browser(_request):
    """Start embedded browser for login."""
    try:
        await browser_stream.start(
            url=CHATGPT_CONFIG["login_url"],
            width=600,
            height=800,
            login_config=CHATGPT_CONFIG,
        )
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


@PromptServer.instance.routes.post("/specter/browser/stop")
async def stop_browser(_request):
    """Stop embedded browser and save session if logged in."""
    try:
        if await browser_stream.is_logged_in():
            storage = await browser_stream.get_storage_state()
            if storage:
                save_session("chatgpt", storage)
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
