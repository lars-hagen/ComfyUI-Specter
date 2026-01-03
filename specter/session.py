"""Specter - Session management for AI web interfaces."""

import asyncio
import json
import os

SESSION_DIR = os.path.dirname(os.path.dirname(__file__))


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
    """Open headed browser for user to log in manually."""
    from camoufox.async_api import AsyncCamoufox

    print("\n" + "=" * 60)
    print(f"[Specter] LOGIN REQUIRED - {service.upper()}")
    print("A browser window will open. Please log in.")
    print("The window will close automatically once logged in.")
    print("=" * 60 + "\n")

    async with AsyncCamoufox(headless=False, window=(800, 900)) as browser:
        ctx = await browser.new_context()
        page = await ctx.new_page()

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
        save_session(service, storage_state)

        return storage_state
