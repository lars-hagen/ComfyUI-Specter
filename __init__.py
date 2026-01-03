"""
ComfyUI-Specter - Browser automation nodes for AI web interfaces.

Provides free access to ChatGPT's web interface via browser automation,
including text generation, image generation, prompt enhancement, and more.
"""

# Register API routes
from .specter import routes  # noqa: F401

# Import nodes
from .specter import (
    ChatGPTNode,
    ChatGPTTextNode,
    ChatGPTImageNode,
    PromptEnhancerNode,
    ImageDescriberNode,
)

NODE_CLASS_MAPPINGS = {
    "Specter_ChatGPT": ChatGPTNode,
    "Specter_ChatGPT_Text": ChatGPTTextNode,
    "Specter_ChatGPT_Image": ChatGPTImageNode,
    "Specter_PromptEnhancer": PromptEnhancerNode,
    "Specter_ImageDescriber": ImageDescriberNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Specter_ChatGPT": "ChatGPT Multimodal",
    "Specter_ChatGPT_Text": "ChatGPT Text",
    "Specter_ChatGPT_Image": "ChatGPT Image",
    "Specter_PromptEnhancer": "Prompt Enhancer",
    "Specter_ImageDescriber": "Image Describer",
}

WEB_DIRECTORY = "./web"

print("=" * 40)
print("ComfyUI-Specter loaded")
print(f"  Nodes: {len(NODE_CLASS_MAPPINGS)}")
print("  Categories: Specter, Specter/Core, Specter/Tools")
print("=" * 40)
