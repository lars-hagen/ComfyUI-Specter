"""Configuration loader for Specter nodes."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
MODELS_PATH = REPO_ROOT / "data" / "models.json"
SYSTEM_PROMPTS_PATH = REPO_ROOT / "data" / "system_prompts.json"

_config: dict = {}
_prompts: dict = {}


def load_config() -> dict:
    """Load models configuration."""
    global _config
    if _config:
        return _config
    try:
        with open(MODELS_PATH) as f:
            _config = json.load(f)
    except FileNotFoundError:
        _config = {"providers": {}, "image_sizes": []}
    return _config


def load_prompts() -> dict:
    """Load system prompts configuration."""
    global _prompts
    if _prompts:
        return _prompts
    try:
        with open(SYSTEM_PROMPTS_PATH) as f:
            _prompts = json.load(f)
    except FileNotFoundError:
        _prompts = {"presets": {}, "categories": {}}
    return _prompts


# =============================================================================
# PROVIDER ACCESS
# =============================================================================


def get_provider(name: str) -> dict:
    """Get provider config by name."""
    return load_config().get("providers", {}).get(name, {})


def get_models(provider: str) -> list[str]:
    """Get text model IDs for a provider."""
    models = get_provider(provider).get("models", [])
    return [m["id"] for m in models]


def get_image_models(provider: str) -> list[str]:
    """Get image model IDs for a provider."""
    models = get_provider(provider).get("image_models", [])
    return [m["id"] for m in models]


def get_image_model(provider: str, model_id: str | None = None) -> dict:
    """Get image model config. If model_id is None, returns default."""
    models = get_provider(provider).get("image_models", [])
    if not models:
        return {}
    if model_id:
        for m in models:
            if m["id"] == model_id:
                return m
    # Return default or first
    for m in models:
        if m.get("default"):
            return m
    return models[0]


# =============================================================================
# IMAGE SIZES
# =============================================================================


def get_image_sizes() -> list[str]:
    """Get image size names for dropdown."""
    sizes = load_config().get("image_sizes", [])
    return [s["name"] for s in sizes]


def get_size_resolution(size_name: str) -> str | None:
    """Get resolution for a size name."""
    for s in load_config().get("image_sizes", []):
        if s["name"] == size_name:
            return s.get("resolution")
    return None


# =============================================================================
# SYSTEM PROMPTS
# =============================================================================


def get_preset_names() -> list[str]:
    """Get all preset names."""
    return list(load_prompts().get("presets", {}).keys())


def get_preset_prompt(name: str) -> str:
    """Get prompt text for a preset."""
    preset = load_prompts().get("presets", {}).get(name, {})
    return preset.get("prompt", "")


def get_presets_by_category(category: str) -> list[str]:
    """Get preset names for a category."""
    return load_prompts().get("categories", {}).get(category, [])


# =============================================================================
# TOOLTIPS
# =============================================================================

TOOLTIPS = {
    "model": "Model to use.",
    "prompt": "Text prompt.",
    "system_message": "System message.",
    "image": "Input image.",
    "size": "Output size.",
    "preview": "Show browser.",
    "enhancement_style": "Enhancement style.",
    "description_style": "Description style.",
    "input_prompt": "Prompt to enhance.",
}


def reload():
    """Force reload all configs."""
    global _config, _prompts
    _config = {}
    _prompts = {}
