"""ComfyUI-Specter - Browser automation nodes for AI web interfaces."""

from .specter import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, routes  # noqa: F401
from .specter.core.browser import is_debug_enabled, is_trace_enabled

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

# Print banner
print("\033[38;5;245m")
print("    ╔═╗╔═╗╔═╗╔═╗╔╦╗╔═╗╦═╗")
print("    ╚═╗╠═╝║╣ ║   ║ ║╣ ╠╦╝")
print("    ╚═╝╩  ╚═╝╚═╝ ╩ ╚═╝╩╚═")
print(f"    \033[38;5;240m{len(NODE_CLASS_MAPPINGS)} nodes | ChatGPT + Grok | stealth automation\033[0m")

env_flags = []
if is_trace_enabled():
    env_flags.append("\033[38;5;208m◆ Trace\033[0m")
if is_debug_enabled():
    env_flags.append("\033[38;5;135m⌘ Debug\033[0m")
env_str = f"  {'  '.join(env_flags)}" if env_flags else ""

print(f"    \033[92m● Browser ready{env_str}\033[0m")
print("\033[0m")
