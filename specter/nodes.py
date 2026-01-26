"""Specter nodes for ComfyUI - DRY factory-based definitions."""

import asyncio
import functools

from .core.browser import log_context
from .core.config import (
    TOOLTIPS,
    get_all_text_models,
    get_image_model,
    get_image_models,
    get_image_sizes,
    get_models,
    get_preset_prompt,
    get_presets_by_category,
    get_provider_for_model,
    get_size_resolution,
    load_config,
)
from .core.utils import (
    bytes_list_to_tensor,
    bytes_to_tensor,
    combine_videos,
    empty_image_tensor,
    extract_last_frame_from_video,
    temp_image,
    temp_images,
    video_to_bytes,
)
from .providers.chatgpt import chat_with_gpt
from .providers.flow_i2i import ASPECT_RATIOS as FLOW_EDIT_RATIOS
from .providers.flow_i2i import MODELS as FLOW_EDIT_MODELS
from .providers.flow_i2i import edit_image as flow_edit_image
from .providers.flow_i2v import ASPECT_RATIOS as FLOW_I2V_RATIOS
from .providers.flow_i2v import MODELS as FLOW_I2V_MODELS
from .providers.flow_i2v import generate_i2v as flow_generate_i2v
from .providers.flow_ref2v import ASPECT_RATIOS as FLOW_REF2V_RATIOS
from .providers.flow_ref2v import MODELS as FLOW_REF2V_MODELS
from .providers.flow_ref2v import generate_ref2v as flow_generate_ref2v
from .providers.flow_t2i import ASPECT_RATIOS as FLOW_ASPECT_RATIOS
from .providers.flow_t2i import MODELS as FLOW_MODELS
from .providers.flow_t2i import imagine_t2i as flow_imagine_t2i
from .providers.flow_t2v import ASPECT_RATIOS as FLOW_VIDEO_RATIOS
from .providers.flow_t2v import MODELS as FLOW_VIDEO_MODELS
from .providers.flow_t2v import generate_t2v as flow_generate_t2v
from .providers.gemini import chat_with_gemini, generate_image_with_gemini
from .providers.grok_chat import chat_with_grok
from .providers.grok_t2i import imagine_t2i
from .providers.grok_video import SIZES, VIDEO_MODES, imagine_edit, imagine_i2v, imagine_t2v

# =============================================================================
# INPUT HELPERS - Reduce repetition in INPUT_TYPES
# =============================================================================

_PREVIEW = ("BOOLEAN", {"default": False, "tooltip": "Show browser preview."})
_SYSTEM = ("STRING", {"multiline": True, "default": "", "tooltip": TOOLTIPS["system_message"]})
_IMAGE_IN = ("IMAGE", {"tooltip": TOOLTIPS["image"]})


def _prompt(tip: str = "") -> tuple:
    return ("STRING", {"multiline": True, "default": "", "tooltip": tip or TOOLTIPS["prompt"]})


def _int(d: int, lo: int, hi: int, tip: str = "") -> tuple:
    return ("INT", {"default": d, "min": lo, "max": hi, "tooltip": tip})

# Provider-specific option lists
GROK_SIZES = list(SIZES.keys())
GROK_MODES = list(VIDEO_MODES.keys())
FLOW_MODEL_IDS = list(FLOW_MODELS.keys())
FLOW_RATIOS = list(FLOW_ASPECT_RATIOS.keys())
FLOW_EDIT_MODEL_IDS = list(FLOW_EDIT_MODELS.keys())
FLOW_EDIT_RATIO_IDS = list(FLOW_EDIT_RATIOS.keys())
FLOW_I2V_MODEL_IDS = FLOW_I2V_MODELS  # Already a list
FLOW_I2V_RATIO_IDS = FLOW_I2V_RATIOS  # Already a list
FLOW_REF2V_MODEL_IDS = FLOW_REF2V_MODELS  # Already a list
FLOW_REF2V_RATIO_IDS = FLOW_REF2V_RATIOS  # Already a list
FLOW_VIDEO_MODEL_IDS = FLOW_VIDEO_MODELS  # Already a list
FLOW_VIDEO_RATIO_IDS = FLOW_VIDEO_RATIOS  # Already a list

# =============================================================================
# NODE FACTORIES
# =============================================================================


def _wrap_with_context(fn, context_name: str):
    """Wrap a function to set log context for all logs during execution."""
    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            with log_context(context_name):
                return await fn(*args, **kwargs)
        return async_wrapper
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            with log_context(context_name):
                return fn(*args, **kwargs)
        return sync_wrapper


def _image_node(display: str, category: str, fn, required: dict, optional: dict | None = None, multi: bool = False):
    """Factory for image generation nodes."""
    opt = {**(optional or {}), "preview": _PREVIEW}

    class Node:
        DISPLAY_NAME = display
        CATEGORY = category
        RETURN_TYPES = ("IMAGE",)
        RETURN_NAMES = ("images" if multi else "image",)
        FUNCTION = "run"

        @classmethod
        def INPUT_TYPES(cls):
            return {"required": required, "optional": opt}

        async def run(self, preview: bool = False, **kw):
            from comfy.utils import ProgressBar
            pbar = ProgressBar(100)
            result = await fn(**kw, pbar=pbar, preview=preview)
            return (bytes_list_to_tensor(result),) if multi else (bytes_to_tensor(result) if result else empty_image_tensor(),)

    return Node


def _image_node_with_input(display: str, category: str, fn, required: dict, optional: dict | None = None, multi: bool = False):
    """Factory for image generation nodes that take an input image."""
    opt = {**(optional or {}), "preview": _PREVIEW}

    class Node:
        DISPLAY_NAME = display
        CATEGORY = category
        RETURN_TYPES = ("IMAGE",)
        RETURN_NAMES = ("images" if multi else "image",)
        FUNCTION = "run"

        @classmethod
        def INPUT_TYPES(cls):
            return {"required": required, "optional": opt}

        async def run(self, image=None, preview: bool = False, **kw):
            from comfy.utils import ProgressBar
            with temp_image(image) as image_path:
                pbar = ProgressBar(100)
                result = await fn(**kw, image_path=image_path, pbar=pbar, preview=preview)
                return (bytes_list_to_tensor(result),) if multi else (bytes_to_tensor(result) if result else empty_image_tensor(),)

    return Node


def _chat_node(display: str, category: str, fn, models_fn, has_image: bool = True):
    """Factory for text chat nodes."""

    class Node:
        DISPLAY_NAME = display
        CATEGORY = category
        RETURN_TYPES = ("STRING",)
        RETURN_NAMES = ("response",)
        FUNCTION = "run"

        @classmethod
        def INPUT_TYPES(cls):
            models = models_fn() or ["default"]
            inputs = {
                "required": {"prompt": _prompt(), "model": (models, {"default": models[0], "tooltip": TOOLTIPS["model"]})},
                "optional": {"system_message": _SYSTEM, "preview": _PREVIEW},
            }
            if has_image:
                inputs["optional"]["image"] = _IMAGE_IN
            return inputs

        async def run(self, prompt: str, model: str, system_message: str | None = None, image=None, preview: bool = False):
            from comfy.utils import ProgressBar
            with temp_image(image) as image_path:
                pbar = ProgressBar(100)
                text, _ = await fn(
                    prompt, model, image_path,
                    system_message=system_message.strip() if system_message else None,
                    pbar=pbar, preview=preview,
                )
                return (text,)

    return Node


def _video_node(display: str, category: str, fn, required: dict, optional: dict | None = None, needs_image: bool = False):
    """Factory for video generation nodes."""
    opt = {**(optional or {}), "preview": _PREVIEW}

    class Node:
        DISPLAY_NAME = display
        CATEGORY = category
        RETURN_TYPES = ("VIDEO", "IMAGE")
        RETURN_NAMES = ("video", "last_frame")
        FUNCTION = "run"

        @classmethod
        def INPUT_TYPES(cls):
            return {"required": required, "optional": opt}

        async def run(self, image=None, preview: bool = False, **kw):
            from io import BytesIO

            from comfy.utils import ProgressBar
            from comfy_api.input_impl import VideoFromFile

            with temp_image(image) as image_path:
                if needs_image and not image_path:
                    raise RuntimeError("Image required")
                pbar = ProgressBar(100)
                video_bytes = await fn(**kw, **({"image_path": image_path} if needs_image else {}), pbar=pbar, preview=preview)
                if not video_bytes:
                    raise RuntimeError("Video generation failed - no video captured")
                return (VideoFromFile(BytesIO(video_bytes)), extract_last_frame_from_video(video_bytes))

    return Node


# =============================================================================
# CHAT NODES
# =============================================================================

ChatGPTTextNode = _chat_node("OpenAI ChatGPT", "Specter/text/OpenAI", chat_with_gpt, lambda: get_models("chatgpt"))
GrokTextNode = _chat_node("xAI Grok", "Specter/text/xAI", chat_with_grok, lambda: get_models("grok"))


class GeminiTextNode:
    """Multimodal chat with Gemini - special handling for multiple input types."""
    DISPLAY_NAME = "Google Gemini"
    CATEGORY = "Specter/text/Google"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("response",)
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_models("gemini") or ["gemini-1.5-flash"]
        return {
            "required": {"prompt": _prompt(), "model": (models, {"default": models[0], "tooltip": TOOLTIPS["model"]})},
            "optional": {
                "images": ("IMAGE", {"tooltip": "Images to include (single or batch)."}),
                "audio": ("AUDIO", {"tooltip": "Audio file to include."}),
                "video": ("VIDEO", {"tooltip": "Video file to include."}),
                "files": ("SPECTER_FILES", {"forceInput": True, "tooltip": "Files from Load Files node."}),
                "system_prompt": ("STRING", {"multiline": True, "tooltip": "System instructions."}),
                "preview": _PREVIEW,
            },
        }

    async def run(self, prompt: str, model: str, images=None, audio=None, video=None, files=None, system_prompt=None, preview: bool = False):
        import os
        import tempfile

        from comfy.utils import ProgressBar

        temp_files = []
        try:
            with temp_images(images) as image_paths:
                audio_path = None
                if audio:
                    if isinstance(audio, dict) and "waveform" in audio:
                        from comfy_api_nodes.util.conversions import audio_input_to_mp3
                        mp3_bytes = audio_input_to_mp3(audio)  # type: ignore[arg-type]
                        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                            f.write(mp3_bytes.read())
                            audio_path = f.name
                            temp_files.append(audio_path)
                    elif isinstance(audio, dict) and "path" in audio:
                        audio_path = audio["path"]
                    elif isinstance(audio, str):
                        audio_path = audio

                video_path = None
                if video:
                    if isinstance(video, dict) and "filename" in video:
                        video_path = video["filename"]
                    elif isinstance(video, str):
                        video_path = video
                    elif hasattr(video, "save_to"):
                        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                            temp_video_path = f.name
                        video.save_to(temp_video_path)  # type: ignore[union-attr]
                        video_path = temp_video_path
                        temp_files.append(temp_video_path)

                pbar = ProgressBar(100)
                text = await chat_with_gemini(
                    prompt, model, image_paths=image_paths, audio_path=audio_path, video_path=video_path,
                    file_paths=list(files) if files else [], system_prompt=system_prompt.strip() if system_prompt else None,
                    pbar=pbar, preview=preview,
                )
                return (text,)
        finally:
            for f in temp_files:
                if os.path.exists(f):
                    try:
                        os.unlink(f)
                    except Exception:
                        pass


# =============================================================================
# IMAGE NODES
# =============================================================================

FlowImageNode = _image_node(
    "Google Flow Text to Image", "Specter/image/Google", flow_imagine_t2i,
    {"prompt": _prompt("Image description.")},
    {
        "model": (FLOW_MODEL_IDS, {"default": "nano-banana-pro", "tooltip": "Model: imagen-4, nano-banana, nano-banana-pro."}),
        "aspect_ratio": (FLOW_RATIOS, {"default": "16:9 (1376x768)", "tooltip": "Aspect ratio."}),
        "num_outputs": _int(1, 1, 4, "Number of images (1-4)."),
        "seed": ("INT", {"default": 42, "min": 0, "max": 999999, "tooltip": "Random seed (0 = random)."}),
        "upscale": ("BOOLEAN", {"default": False, "tooltip": "Upscale to 2K resolution."}),
    },
    multi=True,
)

FlowVideoNode = _video_node(
    "Google Flow Text to Video", "Specter/video/Google", flow_generate_t2v,
    {"prompt": _prompt("Video description.")},
    {
        "model": (FLOW_VIDEO_MODEL_IDS, {"default": "veo-3.1-fast", "tooltip": "Veo model. 3.x has audio, 2.x is silent."}),
        "aspect_ratio": (FLOW_VIDEO_RATIO_IDS, {"default": "16:9 (Landscape)", "tooltip": "Aspect ratio."}),
        "seed": ("INT", {"default": 42, "min": 0, "max": 999999, "tooltip": "Random seed (0 = random)."}),
        "upscale": ("BOOLEAN", {"default": False, "tooltip": "Upscale to 1080p (slower download)."}),
    },
)

FlowImageEditNode = _image_node_with_input(
    "Google Flow Image Edit", "Specter/image/Google", flow_edit_image,
    {"image": _IMAGE_IN, "prompt": _prompt("Edit instructions.")},
    {
        "model": (FLOW_EDIT_MODEL_IDS, {"default": "nano-banana-pro", "tooltip": "Model: imagen-4, nano-banana, nano-banana-pro."}),
        "aspect_ratio": (FLOW_EDIT_RATIO_IDS, {"default": "16:9 (Landscape)", "tooltip": "Aspect ratio for crop."}),
        "num_outputs": _int(1, 1, 4, "Number of images (1-4)."),
        "seed": ("INT", {"default": 42, "min": 0, "max": 999999, "tooltip": "Random seed (0 = random)."}),
        "upscale": ("BOOLEAN", {"default": False, "tooltip": "Upscale to 2K resolution."}),
    },
    multi=True,
)


class FlowI2VNode:
    """Image-to-video with first/last frame support via Google Flow."""
    DISPLAY_NAME = "Google Flow Image to Video"
    CATEGORY = "Specter/video/Google"
    RETURN_TYPES = ("VIDEO", "IMAGE")
    RETURN_NAMES = ("video", "last_frame")
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": _prompt("Video description."),
            },
            "optional": {
                "first_frame": ("IMAGE", {"tooltip": "Starting frame image (optional)."}),
                "last_frame": ("IMAGE", {"tooltip": "Ending frame image (optional)."}),
                "model": (FLOW_I2V_MODEL_IDS, {"default": "veo-3.1-fast", "tooltip": "Veo model. 3.x has audio, 2.x is silent."}),
                "aspect_ratio": (FLOW_I2V_RATIO_IDS, {"default": "16:9 (Landscape)", "tooltip": "Aspect ratio."}),
                "seed": ("INT", {"default": 42, "min": 0, "max": 999999, "tooltip": "Random seed (0 = random)."}),
                "upscale": ("BOOLEAN", {"default": False, "tooltip": "Upscale to 1080p (slower download)."}),
                "preview": _PREVIEW,
            },
        }

    async def run(
        self,
        prompt: str,
        first_frame=None,
        last_frame=None,
        model: str = "veo-3.1-fast",
        aspect_ratio: str = "16:9 (Landscape)",
        seed: int = 42,
        upscale: bool = False,
        preview: bool = False,
    ):
        from io import BytesIO

        from comfy.utils import ProgressBar
        from comfy_api.input_impl import VideoFromFile

        with temp_image(first_frame) as first_path, temp_image(last_frame) as last_path:
            if not first_path and not last_path:
                raise RuntimeError("At least one frame (first or last) is required")
            pbar = ProgressBar(100)
            video_bytes = await flow_generate_i2v(
                prompt=prompt,
                first_frame_path=first_path,
                last_frame_path=last_path,
                model=model,
                aspect_ratio=aspect_ratio,
                seed=seed,
                upscale=upscale,
                pbar=pbar,
                preview=preview,
            )
            if not video_bytes:
                raise RuntimeError("Video generation failed - no video captured")
            return (VideoFromFile(BytesIO(video_bytes)), extract_last_frame_from_video(video_bytes))


class FlowRef2VNode:
    """Reference-to-video via Google Flow (Ingredients to Video)."""
    DISPLAY_NAME = "Google Flow Reference to Video"
    CATEGORY = "Specter/video/Google"
    RETURN_TYPES = ("VIDEO", "IMAGE")
    RETURN_NAMES = ("video", "last_frame")
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": _prompt("Video description."),
            },
            "optional": {
                "image1": ("IMAGE", {"tooltip": "First reference image."}),
                "image2": ("IMAGE", {"tooltip": "Second reference image."}),
                "image3": ("IMAGE", {"tooltip": "Third reference image."}),
                "model": (FLOW_REF2V_MODEL_IDS, {"default": "veo-3.1-fast", "tooltip": "Veo model. 3.x has audio, 2.x is silent."}),
                "aspect_ratio": (FLOW_REF2V_RATIO_IDS, {"default": "16:9 (Landscape)", "tooltip": "Aspect ratio."}),
                "seed": ("INT", {"default": 42, "min": 0, "max": 999999, "tooltip": "Random seed (0 = random)."}),
                "upscale": ("BOOLEAN", {"default": False, "tooltip": "Upscale to 1080p (slower download)."}),
                "preview": _PREVIEW,
            },
        }

    async def run(
        self,
        prompt: str,
        image1=None,
        image2=None,
        image3=None,
        model: str = "veo-3.1-fast",
        aspect_ratio: str = "16:9 (Landscape)",
        seed: int = 42,
        upscale: bool = False,
        preview: bool = False,
    ):
        from io import BytesIO

        from comfy.utils import ProgressBar
        from comfy_api.input_impl import VideoFromFile

        # Collect provided images
        images = [img for img in [image1, image2, image3] if img is not None]
        if not images:
            raise RuntimeError("At least one reference image is required")

        # Convert each image to temp file
        image_paths = []
        temp_contexts = []
        for img in images:
            ctx = temp_image(img)
            path = ctx.__enter__()
            if path:
                image_paths.append(path)
                temp_contexts.append(ctx)

        try:
            pbar = ProgressBar(100)
            video_bytes = await flow_generate_ref2v(
                prompt=prompt,
                image_paths=image_paths,
                model=model,
                aspect_ratio=aspect_ratio,
                seed=seed,
                upscale=upscale,
                pbar=pbar,
                preview=preview,
            )
            if not video_bytes:
                raise RuntimeError("Video generation failed - no video captured")
            return (VideoFromFile(BytesIO(video_bytes)), extract_last_frame_from_video(video_bytes))
        finally:
            for ctx in temp_contexts:
                ctx.__exit__(None, None, None)


GrokImageNode = _image_node(
    "xAI Grok Imagine", "Specter/image/xAI", imagine_t2i,
    {"prompt": _prompt("Image description.")},
    {"size": (GROK_SIZES, {"default": GROK_SIZES[0], "tooltip": "Image size/aspect ratio."}), "max_images": _int(1, 1, 6, "Number of images.")},
    multi=True,
)

GrokImageEditNode = _image_node_with_input(
    "xAI Grok Imagine Edit", "Specter/image/xAI", imagine_edit,
    {"image": _IMAGE_IN, "prompt": _prompt("Edit instructions.")},
    {"max_images": _int(1, 1, 2, "Number of images.")},
    multi=True,
)


class NanoBananaNode:
    """Image generation with Gemini 1.5 Flash."""
    DISPLAY_NAME = "Google Nano Banana"
    CATEGORY = "Specter/image/Google"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"prompt": _prompt("Image description.")}, "optional": {"image": _IMAGE_IN, "preview": _PREVIEW}}

    async def run(self, prompt: str, image=None, preview: bool = False):
        from comfy.utils import ProgressBar
        with temp_image(image) as image_path:
            pbar = ProgressBar(100)
            result = await generate_image_with_gemini(prompt, model="gemini-1.5-flash", image_paths=[image_path] if image_path else None, pbar=pbar, preview=preview)
            return (bytes_to_tensor(result) if result else empty_image_tensor(),)


class NanoBananaProNode:
    """Image generation with Gemini 3.0 models."""
    DISPLAY_NAME = "Google Nano Banana Pro"
    CATEGORY = "Specter/image/Google"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"prompt": _prompt("Image description."), "model": (["gemini-3.0-flash", "gemini-3.0-pro"], {"default": "gemini-3.0-flash"})},
            "optional": {"image": _IMAGE_IN, "preview": _PREVIEW},
        }

    async def run(self, prompt: str, model: str = "gemini-3.0-flash", image=None, preview: bool = False):
        from comfy.utils import ProgressBar
        with temp_image(image) as image_path:
            pbar = ProgressBar(100)
            result = await generate_image_with_gemini(prompt, model=model, image_paths=[image_path] if image_path else None, pbar=pbar, preview=preview)
            return (bytes_to_tensor(result) if result else empty_image_tensor(),)


class ChatGPTImageNode:
    """Image generation with ChatGPT."""
    DISPLAY_NAME = "OpenAI GPT Image 1"
    CATEGORY = "Specter/image/OpenAI"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        image_models = get_image_models("chatgpt") or ["gpt-image-1.5"]
        sizes = get_image_sizes() or ["Auto"]
        return {
            "required": {"prompt": _prompt("Image description.")},
            "optional": {
                "model": (image_models, {"default": image_models[0], "tooltip": "Image model."}),
                "image": _IMAGE_IN,
                "size": (sizes, {"default": sizes[0], "tooltip": TOOLTIPS["size"]}),
                "preview": _PREVIEW,
            },
        }

    async def run(self, prompt: str, model: str = "gpt-image-1.5", image=None, size: str = "Auto", preview: bool = False):
        from comfy.utils import ProgressBar
        config = get_image_model("chatgpt", model)
        proxy_model = config.get("proxy_model", "gpt-5-2-instant")
        prefix = config.get("edit_prefix" if image is not None else "prompt_prefix", "")
        final_prompt = f"{prefix} {prompt}" if prefix else prompt

        resolution = get_size_resolution(size)
        if resolution:
            w, h = map(int, resolution.split("x"))
            orientation = "landscape" if w > h else "portrait" if h > w else "square"
            final_prompt = f"{final_prompt}\n\n[Generate {orientation} image at {resolution}.]"

        with temp_image(image) as image_path:
            pbar = ProgressBar(100)
            _, image_bytes = await chat_with_gpt(final_prompt, proxy_model, image_path, pbar=pbar, preview=preview, _expect_image=True)
            return (bytes_to_tensor(image_bytes) if image_bytes else empty_image_tensor(),)


# =============================================================================
# VIDEO NODES
# =============================================================================

GrokTextToVideoNode = _video_node(
    "xAI Grok Imagine Video", "Specter/video/xAI", imagine_t2v,
    {"prompt": _prompt("Video description.")},
    {"size": (GROK_SIZES, {"default": GROK_SIZES[0]}), "mode": (GROK_MODES, {"default": "custom", "tooltip": "Content mode."})},
)

GrokImageToVideoNode = _video_node(
    "xAI Grok Imagine Video I2V", "Specter/video/xAI", imagine_i2v,
    {"image": _IMAGE_IN},
    {"prompt": _prompt("Motion/action description."), "mode": (GROK_MODES, {"default": "custom"})},
    needs_image=True,
)


# =============================================================================
# UTILITY NODES
# =============================================================================

class LoadFilesNode:
    """Load files from disk for use with AI chat nodes."""
    DISPLAY_NAME = "Load Files"
    CATEGORY = "Specter/utils"
    RETURN_TYPES = ("SPECTER_FILES",)
    RETURN_NAMES = ("files",)
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"file_path": ("STRING", {"default": "", "tooltip": "Path to file. Supports comma-separated paths."})}}

    def run(self, file_path: str):
        import os
        paths = [p.strip() for p in file_path.split(",") if p.strip() and os.path.exists(p.strip())]
        return (paths,)


class GrokVideoCombineNode:
    """Combine two Grok videos sequentially."""
    DISPLAY_NAME = "xAI Grok Video Combine"
    CATEGORY = "Specter/video/xAI"
    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("combined_video",)
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"video1": ("VIDEO", {"tooltip": "First video."}), "video2": ("VIDEO", {"tooltip": "Second video."})},
            "optional": {"audio": ("BOOLEAN", {"default": True, "tooltip": "Include audio."})},
        }

    def run(self, video1, video2, audio: bool = True):
        from io import BytesIO

        from comfy_api.input_impl import VideoFromFile
        return (VideoFromFile(BytesIO(combine_videos(video_to_bytes(video1), video_to_bytes(video2), audio=audio))),)


# =============================================================================
# PROMPT ENHANCER NODES
# =============================================================================

_TEXT_ONLY_SYSTEM_SUFFIX = " Output ONLY text. Do NOT generate images."
_TEXT_ONLY_prompt_SUFFIX = "\n\n[Reply with text only. Do not generate images.]"

CHAT_ADAPTERS = {
    "chatgpt": chat_with_gpt,
    "grok": chat_with_grok,
    "gemini": lambda prompt, model, image_path, system_message, pbar, preview, disable_tools=False: (
        chat_with_gemini(prompt, model, image_paths=[image_path] if image_path else None, system_prompt=system_message, pbar=pbar, preview=preview, disable_image_gen=disable_tools),
        None,
    ),
}


async def _gemini_adapter(prompt, model, image_path, system_message, pbar, preview, disable_tools=False):
    result = await chat_with_gemini(prompt, model, image_paths=[image_path] if image_path else None, system_prompt=system_message, pbar=pbar, preview=preview, disable_image_gen=disable_tools)
    return (result, None)


CHAT_ADAPTERS["gemini"] = _gemini_adapter


class SpecterPromptEnhancerNode:
    """Universal prompt enhancer."""
    DISPLAY_NAME = "Specter Prompt Enhancer"
    CATEGORY = "Specter/tools"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("enhanced_prompt",)
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_all_text_models()
        presets = ["custom"] + (get_presets_by_category("prompt_enhancement") or ["prompt_enhancer"])
        return {
            "required": {"input_prompt": _prompt(TOOLTIPS["input_prompt"])},
            "optional": {
                "model": (models, {"default": models[0] if models else "", "tooltip": TOOLTIPS["model"]}),
                "style": (presets, {"default": presets[1], "tooltip": TOOLTIPS["enhancement_style"]}),
                "additional_instructions": ("STRING", {"multiline": True, "default": "", "tooltip": "Additional instructions."}),
                "preview": _PREVIEW,
            },
        }

    async def run(self, input_prompt: str, model: str, style: str = "prompt_enhancer", additional_instructions: str | None = None, preview: bool = False):
        from comfy.utils import ProgressBar
        if not model:
            raise ValueError("No model selected.")
        provider_id = get_provider_for_model(model)
        if not provider_id:
            raise ValueError(f"Could not find provider for model: {model}")
        config = load_config()
        adapter_key = config["providers"].get(provider_id, {}).get("chat_adapter", provider_id)
        chat_fn = CHAT_ADAPTERS.get(adapter_key)
        if not chat_fn:
            raise ValueError(f"No chat adapter for provider: {provider_id}")

        extra = additional_instructions.strip() if additional_instructions else ""
        system = ((extra or "Enhance this prompt for image generation.") if style == "custom" else f"{get_preset_prompt(style) or 'Enhance this prompt.'} {extra}".strip()) + _TEXT_ONLY_SYSTEM_SUFFIX
        pbar = ProgressBar(100)
        response, _ = await chat_fn(input_prompt + _TEXT_ONLY_prompt_SUFFIX, model, None, system_message=system, pbar=pbar, preview=preview, disable_tools=True)
        return (response,)


class SpecterGooglePromptEnhancerNode:
    """Prompt enhancer optimized for Google's censored image models."""
    DISPLAY_NAME = "Google Prompt Enhancer"
    CATEGORY = "Specter/tools"
    STRATEGIES = ["artistic", "cinematic", "storyboard", "segmented", "auto"]
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "negative")
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        text_models = get_all_text_models()
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "tooltip": "Prompt to enhance."}),
                "strategy": (cls.STRATEGIES, {"default": "artistic"}),
                "model": (text_models, {"default": "grok-3", "tooltip": TOOLTIPS["model"]}),
            },
            "optional": {"additional_negatives": ("STRING", {"default": "", "multiline": True}), "preview": _PREVIEW},
        }

    async def run(self, prompt: str, strategy: str, model: str, additional_negatives: str = "", preview: bool = False):
        from comfy.utils import ProgressBar
        preset_map = {"artistic": "prompt_enhancer_google_artistic", "cinematic": "prompt_enhancer_google_cinematic", "storyboard": "prompt_enhancer_google_storyboard", "segmented": "prompt_enhancer_google_segmented", "auto": "prompt_enhancer_google"}
        system_prompt = get_preset_prompt(preset_map[strategy])
        provider_id = get_provider_for_model(model)
        if not provider_id:
            raise ValueError(f"Could not find provider for model: {model}")
        config = load_config()
        adapter_key = config["providers"].get(provider_id, {}).get("chat_adapter", provider_id)
        chat_fn = CHAT_ADAPTERS.get(adapter_key)
        if not chat_fn:
            raise ValueError(f"No chat adapter for provider: {provider_id}")

        pbar = ProgressBar(100)
        result, _ = await chat_fn(prompt, model, None, system_message=system_prompt, pbar=pbar, preview=preview, disable_tools=True)

        enhanced, negative = result, additional_negatives
        if "PROMPT:" in result:
            parts = result.split("PROMPT:", 1)[1]
            if "NEGATIVE:" in parts:
                enhanced, neg_part = parts.split("NEGATIVE:", 1)
                negative = f"{neg_part.strip()}, {additional_negatives}" if additional_negatives else neg_part.strip()
            enhanced = enhanced.strip()
        return (enhanced, negative)


class SpecterImageDescriberNode:
    """Universal image describer."""
    DISPLAY_NAME = "Specter Image Describer"
    CATEGORY = "Specter/tools"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("description",)
    FUNCTION = "run"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_all_text_models()
        presets = ["custom"] + (get_presets_by_category("image_description") or ["image_describer"])
        return {
            "required": {"image": _IMAGE_IN},
            "optional": {
                "model": (models, {"default": models[0] if models else ""}),
                "style": (presets, {"default": presets[1], "tooltip": TOOLTIPS["description_style"]}),
                "additional_instructions": ("STRING", {"multiline": True, "default": ""}),
                "preview": _PREVIEW,
            },
        }

    async def run(self, image, model: str, style: str = "image_describer", additional_instructions: str | None = None, preview: bool = False):
        from comfy.utils import ProgressBar
        if not model:
            raise ValueError("No model selected.")
        provider_id = get_provider_for_model(model)
        if not provider_id:
            raise ValueError(f"Could not find provider for model: {model}")
        config = load_config()
        adapter_key = config["providers"].get(provider_id, {}).get("chat_adapter", provider_id)
        chat_fn = CHAT_ADAPTERS.get(adapter_key)
        if not chat_fn:
            raise ValueError(f"No chat adapter for provider: {provider_id}")

        extra = additional_instructions.strip() if additional_instructions else ""
        system = ((extra or "Describe this image in detail.") if style == "custom" else f"{get_preset_prompt(style) or 'Describe this image.'} {extra}".strip()) + _TEXT_ONLY_SYSTEM_SUFFIX

        with temp_image(image) as image_path:
            pbar = ProgressBar(100)
            response, _ = await chat_fn("Describe this image." + _TEXT_ONLY_prompt_SUFFIX, model, image_path, system_message=system, pbar=pbar, preview=preview)
            return (response,)


# =============================================================================
# NODE REGISTRATION
# =============================================================================

def _register_nodes() -> tuple[dict, dict]:
    """Auto-register all *Node classes."""
    import sys
    module = sys.modules[__name__]
    class_mappings, display_mappings = {}, {}

    for name in dir(module):
        if not name.endswith("Node"):
            continue
        cls = getattr(module, name)
        if not (hasattr(cls, "DISPLAY_NAME") and hasattr(cls, "CATEGORY") and hasattr(cls, "FUNCTION")):
            continue

        run_method = getattr(cls, cls.FUNCTION)
        setattr(cls, cls.FUNCTION, _wrap_with_context(run_method, cls.DISPLAY_NAME))

        key = f"Specter_{name.removesuffix('Node')}"
        class_mappings[key] = cls
        display_mappings[key] = cls.DISPLAY_NAME

    return class_mappings, display_mappings


NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS = _register_nodes()
