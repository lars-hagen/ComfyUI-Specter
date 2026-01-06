"""ComfyUI-Specter - Browser automation nodes for AI web interfaces."""

from .specter import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, routes  # noqa: F401
from .specter.core.browser import ensure_firefox_installed

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

print("\033[38;5;245m")
print("    ╔═╗╔═╗╔═╗╔═╗╔╦╗╔═╗╦═╗")
print("    ╚═╗╠═╝║╣ ║   ║ ║╣ ╠╦╝")
print("    ╚═╝╩  ╚═╝╚═╝ ╩ ╚═╝╩╚═")
print(f"    \033[38;5;240m{len(NODE_CLASS_MAPPINGS)} nodes | ChatGPT + Grok | stealth automation\033[0m")
print("\033[0m")

# Ensure Playwright Firefox is installed
ensure_firefox_installed()
