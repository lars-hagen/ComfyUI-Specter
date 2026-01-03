"""
ComfyUI-Specter - Browser automation nodes for AI web interfaces.

Provides free access to ChatGPT's web interface via browser automation,
including text generation, image generation, prompt enhancement, and more.
"""

import os

# Register API routes
from . import routes  # noqa: F401

# Core nodes
from .chatgpt import ChatGPTNode

# Specialized nodes
from .nodes import (
    ChatGPTTextNode,
    ChatGPTImageNode,
    PromptEnhancerNode,
    ImageDescriberNode,
)

# Node registrations
NODE_CLASS_MAPPINGS = {
    # Core nodes
    "Specter_ChatGPT": ChatGPTNode,

    # Specialized nodes
    "Specter_ChatGPT_Text": ChatGPTTextNode,
    "Specter_ChatGPT_Image": ChatGPTImageNode,
    "Specter_PromptEnhancer": PromptEnhancerNode,
    "Specter_ImageDescriber": ImageDescriberNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    # Core nodes
    "Specter_ChatGPT": "ChatGPT Multimodal",

    # Specialized nodes
    "Specter_ChatGPT_Text": "ChatGPT Text",
    "Specter_ChatGPT_Image": "ChatGPT Image",
    "Specter_PromptEnhancer": "Prompt Enhancer",
    "Specter_ImageDescriber": "Image Describer",
}

# Web directory for custom UI
WEB_DIRECTORY = "./web"

# Startup message
print("=" * 40)
print("ComfyUI-Specter loaded")
print(f"  Nodes: {len(NODE_CLASS_MAPPINGS)}")
print("  Categories: Specter/Core, Specter/Tools")
print("=" * 40)
