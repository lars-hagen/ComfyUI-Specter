"""ComfyUI-Specter - Browser automation nodes for AI web interfaces."""

from .specter import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, routes  # noqa: F401
from .specter.core.browser import check_browser_health, ensure_firefox_installed

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

# Ensure Playwright Firefox is installed, then check if it can launch
ensure_firefox_installed()
ready, error = check_browser_health()

# Print banner with status
print("\033[38;5;245m")
print("    ╔═╗╔═╗╔═╗╔═╗╔╦╗╔═╗╦═╗")
print("    ╚═╗╠═╝║╣ ║   ║ ║╣ ╠╦╝")
print("    ╚═╝╩  ╚═╝╚═╝ ╩ ╚═╝╩╚═")
print(f"    \033[38;5;240m{len(NODE_CLASS_MAPPINGS)} nodes | ChatGPT + Grok | stealth automation")
if ready:
    print("    \033[92m● Browser ready\033[0m")
else:
    print(f"    \033[91;1m▲ {error}\033[0m")
print("\033[0m")
