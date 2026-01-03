"""Configuration loader for Specter nodes."""

import json
from pathlib import Path
from typing import Any

from .exceptions import ConfigurationException

NODE_DIR = Path(__file__).parent
MODELS_PATH = NODE_DIR / "models.json"
SYSTEM_PROMPTS_PATH = NODE_DIR / "system_prompts.json"

# Cached configs
_models_config: dict = {}
_prompts_config: dict = {}


def load_models_config() -> dict:
    """Load models configuration from JSON file."""
    global _models_config
    if _models_config:
        return _models_config

    try:
        with open(MODELS_PATH, "r", encoding="utf-8") as f:
            _models_config = json.load(f)
        return _models_config
    except FileNotFoundError:
        print(f"[Specter] Warning: {MODELS_PATH} not found, using defaults")
        return get_default_models_config()
    except json.JSONDecodeError as e:
        raise ConfigurationException(str(MODELS_PATH), f"Invalid JSON: {e}")


def load_prompts_config() -> dict:
    """Load system prompts configuration from JSON file."""
    global _prompts_config
    if _prompts_config:
        return _prompts_config

    try:
        with open(SYSTEM_PROMPTS_PATH, "r", encoding="utf-8") as f:
            _prompts_config = json.load(f)
        return _prompts_config
    except FileNotFoundError:
        print(f"[Specter] Warning: {SYSTEM_PROMPTS_PATH} not found, using defaults")
        return get_default_prompts_config()
    except json.JSONDecodeError as e:
        raise ConfigurationException(str(SYSTEM_PROMPTS_PATH), f"Invalid JSON: {e}")


def get_default_models_config() -> dict:
    """Return default models configuration."""
    return {
        "text_models": [
            {"id": "gpt-5.2-instant", "name": "GPT-5.2 Instant", "default": True},
            {"id": "gpt-5.2", "name": "GPT-5.2", "default": False},
            {"id": "gpt-5.2-thinking", "name": "GPT-5.2 Thinking", "default": False},
            {"id": "gpt-4o", "name": "GPT-4o", "default": False},
        ],
        "image_models": [
            {"id": "gpt-image-1.5", "name": "GPT Image 1.5", "default": True},
        ],
        "image_sizes": [
            {"id": "auto", "name": "Auto", "resolution": None},
            {"id": "square", "name": "Square (1024x1024)", "resolution": "1024x1024"},
            {"id": "landscape", "name": "Landscape (1536x1024)", "resolution": "1536x1024"},
            {"id": "portrait", "name": "Portrait (1024x1536)", "resolution": "1024x1536"},
        ],
    }


def get_default_prompts_config() -> dict:
    """Return default prompts configuration."""
    return {
        "presets": {
            "default": {"name": "Default Assistant", "prompt": "You are a helpful assistant."},
            "prompt_enhancer": {"name": "Prompt Enhancer", "prompt": "Enhance this prompt for image generation. Output only the enhanced prompt."},
            "image_describer": {"name": "Image Describer", "prompt": "Describe this image in detail."},
        },
        "node_defaults": {
            "ChatGPT": "default",
            "Prompt_Enhancer": "prompt_enhancer",
            "Image_Describer": "image_describer",
        },
    }


def get_model_ids(model_type: str = "text") -> list[str]:
    """Get list of model IDs for dropdown."""
    config = load_models_config()
    key = f"{model_type}_models"
    models = config.get(key, [])
    return [m["id"] for m in models]


def get_all_model_ids() -> list[str]:
    """Get combined list of all model IDs."""
    config = load_models_config()
    image_models = [m["id"] for m in config.get("image_models", [])]
    text_models = [m["id"] for m in config.get("text_models", [])]
    return image_models + text_models


def get_default_model(model_type: str = "text") -> str:
    """Get default model ID."""
    config = load_models_config()
    key = f"{model_type}_models"
    models = config.get(key, [])
    for m in models:
        if m.get("default"):
            return m["id"]
    return models[0]["id"] if models else "gpt-5.2-instant"


def get_image_sizes() -> list[str]:
    """Get list of image size options for dropdown."""
    config = load_models_config()
    sizes = config.get("image_sizes", [])
    return [s["name"] for s in sizes]


def get_size_resolution(size_name: str) -> str | None:
    """Get resolution string for a size name."""
    config = load_models_config()
    sizes = config.get("image_sizes", [])
    for s in sizes:
        if s["name"] == size_name:
            return s.get("resolution")
    return None


def get_image_model_config(model_id: str) -> dict | None:
    """Get image model configuration."""
    config = load_models_config()
    for m in config.get("image_models", []):
        if m["id"] == model_id:
            return m
    return None


def get_preset_names() -> list[str]:
    """Get list of preset names for dropdown."""
    config = load_prompts_config()
    presets = config.get("presets", {})
    return list(presets.keys())


def get_preset_prompt(preset_name: str) -> str:
    """Get prompt text for a preset."""
    config = load_prompts_config()
    presets = config.get("presets", {})
    preset = presets.get(preset_name, {})
    return preset.get("prompt", "")


def get_presets_by_category(category: str) -> list[str]:
    """Get preset names filtered by category."""
    config = load_prompts_config()
    categories = config.get("categories", {})
    return categories.get(category, [])


def get_node_default_preset(node_type: str) -> str:
    """Get default preset for a node type."""
    config = load_prompts_config()
    defaults = config.get("node_defaults", {})
    return defaults.get(node_type, "default")


# Tooltips dictionary
TOOLTIPS = {
    # Model & Generation
    "model": "Select the ChatGPT model to use. Different models have different capabilities and speed.",
    "prompt": "The text prompt to send to ChatGPT.",
    "system_message": "System message to set the AI's behavior and role. This is prepended to the conversation.",

    # Image options
    "image": "Optional input image for vision models or image editing.",
    "size": "Output image size. 'Auto' lets ChatGPT decide the best size.",

    # Advanced options
    "preview": "Show live preview of the browser during generation.",
    "force_login": "Force a new login, clearing any existing session.",
    "cleaning_mode": "How to clean the response text. 'Aggressive' removes thinking blocks and planning.",
    "max_length": "Maximum length of output text (0 = unlimited).",

    # Presets
    "preset": "Select a predefined system prompt preset.",
    "custom_system_prompt": "Custom system prompt (overrides preset if provided).",

    # Enhancer
    "enhancement_style": "Style of prompt enhancement to apply.",
    "input_prompt": "The prompt to enhance for image generation.",

    # Describer
    "description_style": "Style of image description to generate.",
}


def reload_configs():
    """Force reload of all configurations."""
    global _models_config, _prompts_config
    _models_config = {}
    _prompts_config = {}
    load_models_config()
    load_prompts_config()
