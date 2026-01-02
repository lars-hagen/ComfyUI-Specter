"""
ComfyUI-Specter - Browser automation nodes for AI web interfaces.
"""

from .chatgpt import ChatGPTNode

NODE_CLASS_MAPPINGS = {
    "Specter_ChatGPT": ChatGPTNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Specter_ChatGPT": "Specter ChatGPT",
}

print("=" * 30)
print("ComfyUI-Specter loaded")
print("=" * 30)
