"""Specter - Session management for AI web interfaces."""

import asyncio
import json
import os

USER_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "user_data")
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
    """Delete session file and persistent browser profile."""
    import shutil

    # Delete session file
    path = get_session_path(service)
    if os.path.exists(path):
        os.unlink(path)

    # Delete persistent Firefox profile
    profile_dir = os.path.join(USER_DATA_DIR, "profiles", f"firefox-{service}")
    if os.path.exists(profile_dir):
        shutil.rmtree(profile_dir, ignore_errors=True)


async def handle_login_flow(page, service: str, login_event: str, login_selectors: list[str]):
    """Handle login flow with popup and polling.

    Opens login popup, waits for user to log in, then injects session.
    """
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
    """Check if page shows logged-in state by absence of login buttons."""
    try:
        await page.wait_for_load_state("domcontentloaded")
        for selector in login_selectors:
            if await page.locator(selector).count() > 0:
                return False
        return True
    except:
        return False


async def interactive_login(
    service: str,
    login_url: str,
    login_selectors: list[str],
    success_url_contains: str,
    success_url_excludes: str | None = None,
    workspace_selector: str | None = None,
):
    """Open headed browser for user to log in manually."""
    from playwright.async_api import async_playwright

    print("\n" + "=" * 60)
    print(f"[Specter] LOGIN REQUIRED - {service.upper()}")
    print("A browser window will open. Please log in.")
    print("The window will close automatically once logged in.")
    print("=" * 60 + "\n")

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 800, "height": 900})
        page = await ctx.new_page()

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        await page.goto(login_url)
        await page.wait_for_load_state("domcontentloaded")
        print("[Specter] Please log in...")

        while True:
            await asyncio.sleep(1)
            url = page.url

            url_ok = success_url_contains in url
            if success_url_excludes:
                url_ok = url_ok and success_url_excludes not in url

            if url_ok and await is_logged_in(page, login_selectors):
                if workspace_selector:
                    if await page.locator(workspace_selector).count() > 0:
                        continue
                break

        print("[Specter] Login successful! Saving session...")
        await asyncio.sleep(2)
        storage_state = await ctx.storage_state()
        save_session(service, storage_state)  # type: ignore[arg-type]

        await browser.close()
        return storage_state
