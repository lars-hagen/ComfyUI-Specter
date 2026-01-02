"""
Specter - Browser session management for AI web interfaces.
Handles authentication, session persistence, and interactive login flows.
"""

import asyncio
import json
import os

SESSION_DIR = os.path.dirname(__file__)


def get_session_path(service: str) -> str:
    return os.path.join(SESSION_DIR, f"{service}_session.json")


def load_session(service: str) -> dict | None:
    path = get_session_path(service)
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    return None


def save_session(service: str, storage_state: dict):
    with open(get_session_path(service), 'w') as f:
        json.dump(storage_state, f)


def delete_session(service: str):
    path = get_session_path(service)
    if os.path.exists(path):
        os.unlink(path)


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
    success_url_excludes: str = None,
    workspace_selector: str = None,
) -> dict:
    """
    Open headed browser for user to log in manually.

    Args:
        service: Service name for session storage (e.g., 'chatgpt')
        login_url: URL to start login flow
        login_selectors: Selectors that indicate NOT logged in
        success_url_contains: URL must contain this when logged in
        success_url_excludes: URL must NOT contain this when logged in
        workspace_selector: Optional modal to wait for dismissal

    Returns:
        Storage state dict for browser context
    """
    from camoufox.async_api import AsyncCamoufox

    print("\n" + "=" * 60)
    print(f"[Specter] LOGIN REQUIRED - {service.upper()}")
    print("A browser window will open. Please log in.")
    print("The window will close automatically once logged in.")
    print("=" * 60 + "\n")

    async with AsyncCamoufox(
        headless=False,
        window=(800, 900),
        firefox_user_prefs={
            "dom.storageManager.prompt.testing": True,
            "permissions.default.persistent-storage": 1,
        }
    ) as browser:
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(login_url)
        await page.wait_for_load_state("domcontentloaded")
        print("[Specter] Please log in...")

        workspace_msg_shown = False
        logged_in_detected = False

        while True:
            await asyncio.sleep(1)
            url = page.url

            # Check URL conditions
            url_ok = success_url_contains in url
            if success_url_excludes:
                url_ok = url_ok and success_url_excludes not in url

            if url_ok and await is_logged_in(page, login_selectors):
                if not logged_in_detected:
                    logged_in_detected = True
                    await page.wait_for_load_state("networkidle")
                    continue

                # Check for workspace/org selector modal
                if workspace_selector:
                    modal = page.locator(workspace_selector)
                    if await modal.count() > 0:
                        if not workspace_msg_shown:
                            print("[Specter] Please select a workspace...")
                            workspace_msg_shown = True
                        continue

                break

        print("[Specter] Login successful! Saving session...")
        await asyncio.sleep(2)
        storage_state = await ctx.storage_state()
        save_session(service, storage_state)

        return storage_state


async def get_session(
    service: str,
    login_url: str,
    login_selectors: list[str],
    success_url_contains: str,
    success_url_excludes: str = None,
    workspace_selector: str = None,
    force_login: bool = False,
) -> dict | None:
    """Get existing session or trigger interactive login."""
    if force_login:
        delete_session(service)

    session = load_session(service)
    if session:
        return session

    return await interactive_login(
        service=service,
        login_url=login_url,
        login_selectors=login_selectors,
        success_url_contains=success_url_contains,
        success_url_excludes=success_url_excludes,
        workspace_selector=workspace_selector,
    )
