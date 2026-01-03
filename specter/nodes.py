"""Specialized Specter nodes for different use cases."""

import os
import tempfile

import numpy as np
import torch
from PIL import Image

from .chatgpt import chat_with_gpt, bytes_to_tensor, tensor_to_pil, empty_image_tensor
from .config import (
    get_all_model_ids,
    get_model_ids,
    get_image_sizes,
    get_size_resolution,
    get_image_model_config,
    get_preset_names,
    get_preset_prompt,
    get_presets_by_category,
    TOOLTIPS,
)


# =============================================================================
# TEXT-ONLY NODE
# =============================================================================

class ChatGPTTextNode:
    """Text-only ChatGPT node - no image generation, faster for text tasks."""

    CATEGORY = "Specter/Core"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_model_ids("text")
        return {
            "required": {
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": TOOLTIPS["prompt"]
                }),
                "model": (models, {
                    "default": models[0] if models else "gpt-5.2-instant",
                    "tooltip": TOOLTIPS["model"]
                }),
            },
            "optional": {
                "system_message": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": TOOLTIPS["system_message"]
                }),
                "image": ("IMAGE", {
                    "tooltip": TOOLTIPS["image"]
                }),
                "preview": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Show browser window during generation."
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("response",)
    FUNCTION = "run"

    async def run(self, prompt: str, model: str, system_message: str = None,
                  image=None, preview: bool = False):
        from comfy.utils import ProgressBar

        image_path = None
        if image is not None:
            pil_img = tensor_to_pil(image)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                pil_img.save(f.name)
                image_path = f.name

        try:
            pbar = ProgressBar(100)
            response_text, _ = await chat_with_gpt(
                prompt, model, image_path,
                system_message=system_message if system_message and system_message.strip() else None,
                pbar=pbar,
                preview=preview,
            )
            return (response_text,)
        finally:
            if image_path and os.path.exists(image_path):
                os.unlink(image_path)


# =============================================================================
# IMAGE-ONLY NODE
# =============================================================================

class ChatGPTImageNode:
    """Image generation node - optimized for image output."""

    CATEGORY = "Specter/Core"

    @classmethod
    def INPUT_TYPES(cls):
        sizes = get_image_sizes()
        return {
            "required": {
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Describe the image you want to generate."
                }),
                "model": (["gpt-image-1.5"], {
                    "default": "gpt-image-1.5",
                    "tooltip": "ChatGPT image generation model."
                }),
            },
            "optional": {
                "image": ("IMAGE", {
                    "tooltip": "Optional reference image for editing or style guidance."
                }),
                "size": (sizes, {
                    "default": sizes[0] if sizes else "Auto",
                    "tooltip": TOOLTIPS["size"]
                }),
                "preview": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Show browser window during generation."
                }),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "run"

    async def run(self, prompt: str, model: str = "gpt-image-1.5", image=None, size: str = "Auto", preview: bool = False):
        from comfy.utils import ProgressBar

        # Get image model config (uses gpt-image-1.5)
        model_config = get_image_model_config("gpt-image-1.5")
        actual_model = model_config.get("actual_model", "gpt-5.2-instant") if model_config else "gpt-5.2-instant"

        # Build prompt with image generation prefix
        if image is not None:
            prefix = model_config.get("prompt_prefix_edit", "Use image_gen to edit this image:") if model_config else "Use image_gen to edit this image:"
        else:
            prefix = model_config.get("prompt_prefix_new", "Use image_gen to create:") if model_config else "Use image_gen to create:"

        final_prompt = f"{prefix} {prompt}"

        # Add size instruction
        resolution = get_size_resolution(size)
        if resolution:
            final_prompt = f"{final_prompt}\n\n[Generate image at {resolution} resolution.]"

        image_path = None
        if image is not None:
            pil_img = tensor_to_pil(image)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                pil_img.save(f.name)
                image_path = f.name

        try:
            pbar = ProgressBar(100)
            _, image_bytes = await chat_with_gpt(
                final_prompt, actual_model, image_path, pbar=pbar, preview=preview
            )

            if image_bytes:
                return (bytes_to_tensor(image_bytes),)
            return (empty_image_tensor(),)
        finally:
            if image_path and os.path.exists(image_path):
                os.unlink(image_path)


# =============================================================================
# PROMPT ENHANCER NODE
# =============================================================================

class PromptEnhancerNode:
    """Enhance prompts for better image generation results."""

    CATEGORY = "Specter/Tools"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_model_ids("text")
        enhancement_presets = get_presets_by_category("prompt_enhancement")
        if not enhancement_presets:
            enhancement_presets = ["prompt_enhancer"]

        return {
            "required": {
                "input_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": TOOLTIPS["input_prompt"]
                }),
            },
            "optional": {
                "model": (models, {
                    "default": "gpt-5.2-instant",
                    "tooltip": TOOLTIPS["model"]
                }),
                "enhancement_style": (enhancement_presets, {
                    "default": enhancement_presets[0],
                    "tooltip": TOOLTIPS["enhancement_style"]
                }),
                "custom_instruction": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Custom enhancement instruction (overrides style preset)."
                }),
                "preview": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Show browser window during generation."
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("enhanced_prompt",)
    FUNCTION = "run"

    async def run(self, input_prompt: str, model: str = "gpt-5.2-instant",
                  enhancement_style: str = "prompt_enhancer",
                  custom_instruction: str = None, preview: bool = False):
        from comfy.utils import ProgressBar

        # Get system prompt
        if custom_instruction and custom_instruction.strip():
            system_message = custom_instruction.strip()
        else:
            system_message = get_preset_prompt(enhancement_style)
            if not system_message:
                system_message = "Enhance this prompt for image generation. Add artistic details, lighting, composition, and style. Output only the enhanced prompt, no explanations."

        pbar = ProgressBar(100)
        response_text, _ = await chat_with_gpt(
            input_prompt, model, None,
            system_message=system_message, pbar=pbar, preview=preview
        )
        return (response_text,)


# =============================================================================
# IMAGE DESCRIBER NODE
# =============================================================================

class ImageDescriberNode:
    """Describe images using ChatGPT vision capabilities."""

    CATEGORY = "Specter/Tools"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_model_ids("text")
        description_presets = get_presets_by_category("image_description")
        if not description_presets:
            description_presets = ["image_describer"]

        return {
            "required": {
                "image": ("IMAGE", {
                    "tooltip": "The image to describe."
                }),
            },
            "optional": {
                "model": (models, {
                    "default": "gpt-5.2-instant",
                    "tooltip": TOOLTIPS["model"]
                }),
                "description_style": (description_presets, {
                    "default": description_presets[0] if description_presets else "image_describer",
                    "tooltip": TOOLTIPS["description_style"]
                }),
                "custom_instruction": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Custom description instruction (overrides style preset)."
                }),
                "preview": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Show browser window during generation."
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("description",)
    FUNCTION = "run"

    async def run(self, image, model: str = "gpt-5.2-instant",
                  description_style: str = "image_describer",
                  custom_instruction: str = None, preview: bool = False):
        from comfy.utils import ProgressBar

        # Get system prompt
        if custom_instruction and custom_instruction.strip():
            system_message = custom_instruction.strip()
        else:
            system_message = get_preset_prompt(description_style)
            if not system_message:
                system_message = "Describe this image in detail."

        # Save image to temp file
        pil_img = tensor_to_pil(image)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            pil_img.save(f.name)
            image_path = f.name

        try:
            pbar = ProgressBar(100)
            response_text, _ = await chat_with_gpt(
                "Describe this image.",  # Simple prompt, system message does the work
                model, image_path,
                system_message=system_message, pbar=pbar, preview=preview
            )

            return (response_text,)
        finally:
            if image_path and os.path.exists(image_path):
                os.unlink(image_path)


# =============================================================================
# NODE REGISTRATION
# =============================================================================

NODE_CLASS_MAPPINGS = {
    "Specter_ChatGPT_Text": ChatGPTTextNode,
    "Specter_ChatGPT_Image": ChatGPTImageNode,
    "Specter_PromptEnhancer": PromptEnhancerNode,
    "Specter_ImageDescriber": ImageDescriberNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Specter_ChatGPT_Text": "ChatGPT Text",
    "Specter_ChatGPT_Image": "ChatGPT Image",
    "Specter_PromptEnhancer": "Prompt Enhancer",
    "Specter_ImageDescriber": "Image Describer",
}
