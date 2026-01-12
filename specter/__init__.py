"""Specter - Browser automation for AI web interfaces."""

# Only import ComfyUI-specific modules when running inside ComfyUI
try:
    from server import PromptServer  # noqa: F401

    # ComfyUI is available, import nodes and routes
    from . import routes
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "routes"]
except ImportError:
    # Running outside ComfyUI (e.g., CLI tools)
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
