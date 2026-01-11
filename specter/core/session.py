import asyncio
import json
import os

# Project root is 3 levels up from specter/core/session.py (matches browser.py)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
USER_DATA_DIR = os.path.join(PROJECT_ROOT, "user_data")
SESSION_DIR = os.path.join(USER_DATA_DIR, "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)


def get_session_path(service: str) -> str:
    return os.path.join(SESSION_DIR, f"{service}_session.json")


def load_session(service: str) -> dict | None:
    path = get_session_path(service)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
    return None


def save_session(service: str, storage_state: dict):
    with open(get_session_path(service), "w") as f:
        json.dump(storage_state, f)


def delete_session(service: str):
    import shutil

    path = get_session_path(service)
    if os.path.exists(path):
        os.unlink(path)

    profile_dir = os.path.join(USER_DATA_DIR, "profiles", f"firefox-{service}")
    if os.path.exists(profile_dir):
        shutil.rmtree(profile_dir, ignore_errors=True)


async def handle_login_flow(page, service: str, login_event: str, login_selectors: list[str]):
    from server import PromptServer

    from .browser import log

    log("Not logged in - opening authentication popup...", "⚠")
    PromptServer.instance.send_sync(login_event, {})

    log("Waiting for login to complete...", "◌")
    timeout, poll_interval, elapsed = 300, 2, 0

    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        new_session = load_session(service)
        if new_session:
            log("Login detected! Continuing...", "●")
            # Caller should navigate and inject localStorage
            return new_session

    raise Exception("Login timed out after 5 minutes. Please try again.")


async def is_logged_in(page, login_selectors: list[str]) -> bool:
    try:
        await page.wait_for_load_state("domcontentloaded")
        for selector in login_selectors:
            if await page.locator(selector).count() > 0:
                return False
        return True
    except Exception:
        return False
