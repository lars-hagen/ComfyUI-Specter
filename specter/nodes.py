"""Specter nodes for ComfyUI."""

import asyncio
import functools

from .core.browser import log_context
from .core.config import (
    TOOLTIPS,
    get_image_model,
    get_image_models,
    get_image_sizes,
    get_models,
    get_preset_prompt,
    get_presets_by_category,
    get_size_resolution,
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
from .providers.gemini import chat_with_gemini
from .providers.grok_chat import chat_with_grok
from .providers.grok_t2i import imagine_t2i
from .providers.grok_video import SIZES, VIDEO_MODES, imagine_edit, imagine_i2v, imagine_t2v

# Size names for Grok Imagine
GROK_SIZES = list(SIZES.keys())
GROK_MODES = list(VIDEO_MODES.keys())


# =============================================================================
# UTILITY NODES
# =============================================================================


class LoadFilesNode:
    """Load files from disk for use with AI chat nodes."""

    DISPLAY_NAME = "Load Files"
    CATEGORY = "Specter/utils"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "file_path": ("STRING", {"default": "", "tooltip": "Path to file (PDF, TXT, etc). Supports comma-separated paths."}),
            },
        }

    RETURN_TYPES = ("SPECTER_FILES",)
    RETURN_NAMES = ("files",)
    FUNCTION = "run"

    def run(self, file_path: str):
        import os

        paths = []
        for p in file_path.split(","):
            p = p.strip()
            if p and os.path.exists(p):
                paths.append(p)
            elif p:
                print(f"[Specter] Warning: File not found: {p}")

        return (paths,)


# =============================================================================
# CHATGPT NODES
# =============================================================================


class ChatGPTTextNode:
    """Text chat with ChatGPT."""

    DISPLAY_NAME = "OpenAI ChatGPT"
    CATEGORY = "Specter/text/OpenAI"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_models("chatgpt") or ["gpt-5-2-instant"]
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": TOOLTIPS["prompt"]}),
                "model": (models, {"default": models[0], "tooltip": TOOLTIPS["model"]}),
            },
            "optional": {
                "system_message": ("STRING", {"multiline": True, "default": "", "tooltip": TOOLTIPS["system_message"]}),
                "image": ("IMAGE", {"tooltip": TOOLTIPS["image"]}),
                "preview": ("BOOLEAN", {"default": False, "tooltip": "Show browser preview."}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("response",)
    FUNCTION = "run"

    async def run(self, prompt: str, model: str, system_message: str | None = None, image=None, preview: bool = False):
        from comfy.utils import ProgressBar

        with temp_image(image) as image_path:
            pbar = ProgressBar(100)
            text, _ = await chat_with_gpt(
                prompt,
                model,
                image_path,
                system_message=system_message if system_message and system_message.strip() else None,
                pbar=pbar,
                preview=preview,
            )
            return (text,)


class ChatGPTImageNode:
    """Image generation with ChatGPT."""

    DISPLAY_NAME = "OpenAI GPT Image 1"
    CATEGORY = "Specter/image/OpenAI"

    @classmethod
    def INPUT_TYPES(cls):
        image_models = get_image_models("chatgpt") or ["gpt-image-1.5"]
        sizes = get_image_sizes() or ["Auto"]
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "Image description."}),
            },
            "optional": {
                "model": (image_models, {"default": image_models[0], "tooltip": "Image model."}),
                "image": ("IMAGE", {"tooltip": "Reference image for editing."}),
                "size": (sizes, {"default": sizes[0], "tooltip": TOOLTIPS["size"]}),
                "preview": ("BOOLEAN", {"default": False, "tooltip": "Show browser preview."}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "run"

    async def run(
        self, prompt: str, model: str = "gpt-image-1.5", image=None, size: str = "Auto", preview: bool = False
    ):
        from comfy.utils import ProgressBar

        config = get_image_model("chatgpt", model)
        proxy_model = config.get("proxy_model", "gpt-5-2-instant")
        prefix = config.get("edit_prefix" if image is not None else "prompt_prefix", "")

        final_prompt = f"{prefix} {prompt}" if prefix else prompt
        resolution = get_size_resolution(size)
        if resolution:
            final_prompt = f"{final_prompt}\n\n[Generate at {resolution}.]"

        with temp_image(image) as image_path:
            pbar = ProgressBar(100)
            _, image_bytes = await chat_with_gpt(
                final_prompt, proxy_model, image_path, pbar=pbar, preview=preview, _expect_image=True
            )
            return (bytes_to_tensor(image_bytes) if image_bytes else empty_image_tensor(),)


# =============================================================================
# GEMINI NODES
# =============================================================================


class GeminiTextNode:
    """Multimodal chat with Gemini."""

    DISPLAY_NAME = "Google Gemini"
    CATEGORY = "Specter/text/Google"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_models("gemini") or ["gemini-1.5-flash"]
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": TOOLTIPS["prompt"]}),
                "model": (models, {"default": models[0], "tooltip": TOOLTIPS["model"]}),
            },
            "optional": {
                "images": ("IMAGE", {"tooltip": "Images to include (single or batch)."}),
                "audio": ("AUDIO", {"tooltip": "Audio file to include."}),
                "video": ("VIDEO", {"tooltip": "Video file to include."}),
                "files": ("SPECTER_FILES", {"forceInput": True, "tooltip": "Files to include (from Load Files node)."}),
                "preview": ("BOOLEAN", {"default": False, "tooltip": "Show browser preview."}),
                "system_prompt": ("STRING", {"multiline": True, "tooltip": "System instructions injected into request."}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("response",)
    FUNCTION = "run"

    async def run(self, prompt: str, model: str, images=None, audio=None, video=None, files=None, system_prompt=None, preview: bool = False):
        import os
        import tempfile

        from comfy.utils import ProgressBar

        temp_files = []
        try:
            with temp_images(images) as image_paths:
                # Handle AUDIO type (dict with waveform tensor and sample_rate)
                audio_path = None
                if audio:
                    if isinstance(audio, dict) and "waveform" in audio:
                        # Convert audio tensor to MP3 file
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

                # Handle VIDEO type (can be dict, str, or VideoFromFile with save_to)
                video_path = None
                if video:
                    if isinstance(video, dict) and "filename" in video:
                        video_path = video["filename"]
                    elif isinstance(video, str):
                        video_path = video
                    elif hasattr(video, "save_to"):
                        # VideoFromFile - save to temp file
                        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                            temp_video_path = f.name
                        video.save_to(temp_video_path)  # type: ignore[union-attr]
                        video_path = temp_video_path
                        temp_files.append(temp_video_path)

                # Handle files input (SPECTER_FILES = list of paths from Load Files node)
                file_paths = list(files) if files else []

                pbar = ProgressBar(100)
                text = await chat_with_gemini(
                    prompt, model,
                    image_paths=image_paths,
                    audio_path=audio_path,
                    video_path=video_path,
                    file_paths=file_paths,
                    system_prompt=system_prompt.strip() if system_prompt else None,
                    pbar=pbar, preview=preview
                )
                return (text,)
        finally:
            for f in temp_files:
                if os.path.exists(f):
                    try:
                        os.unlink(f)
                    except:
                        pass


# =============================================================================
# GROK NODES
# =============================================================================


class GrokTextNode:
    """Text chat with Grok."""

    DISPLAY_NAME = "xAI Grok"
    CATEGORY = "Specter/text/xAI"

    @classmethod
    def INPUT_TYPES(cls):
        models = get_models("grok") or ["grok-3"]
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": TOOLTIPS["prompt"]}),
                "model": (models, {"default": models[0], "tooltip": TOOLTIPS["model"]}),
            },
            "optional": {
                "system_message": ("STRING", {"multiline": True, "default": "", "tooltip": TOOLTIPS["system_message"]}),
                "image": ("IMAGE", {"tooltip": TOOLTIPS["image"]}),
                "preview": ("BOOLEAN", {"default": False, "tooltip": "Show browser preview."}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("response",)
    FUNCTION = "run"

    async def run(self, prompt: str, model: str, system_message: str | None = None, image=None, preview: bool = False):
        from comfy.utils import ProgressBar

        with temp_image(image) as image_path:
            pbar = ProgressBar(100)
            text, _ = await chat_with_grok(
                prompt,
                model,
                image_path,
                system_message=system_message if system_message and system_message.strip() else None,
                pbar=pbar,
                preview=preview,
            )
            return (text,)


class GrokImageNode:
    """Image generation with Grok Imagine."""

    DISPLAY_NAME = "xAI Grok Imagine"
    CATEGORY = "Specter/image/xAI"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "Image description."}),
            },
            "optional": {
                "size": (GROK_SIZES, {"default": GROK_SIZES[0], "tooltip": "Image size/aspect ratio."}),
                "max_images": ("INT", {"default": 1, "min": 1, "max": 6, "tooltip": "Number of images to generate."}),
                "preview": ("BOOLEAN", {"default": False, "tooltip": "Show browser preview."}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "run"

    async def run(
        self,
        prompt: str,
        size: str = "Square (960x960)",
        max_images: int = 4,
        preview: bool = False,
    ):
        from comfy.utils import ProgressBar

        pbar = ProgressBar(100)
        images = await imagine_t2i(prompt, size=size, max_images=max_images, pbar=pbar, preview=preview)
        return (bytes_list_to_tensor(images),)


class GrokImageEditNode:
    """Image editing with Grok Imagine."""

    DISPLAY_NAME = "xAI Grok Imagine Edit"
    CATEGORY = "Specter/image/xAI"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "Image to edit."}),
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "Edit instructions."}),
            },
            "optional": {
                "max_images": ("INT", {"default": 1, "min": 1, "max": 2, "tooltip": "Number of images to generate."}),
                "preview": ("BOOLEAN", {"default": False, "tooltip": "Show browser preview."}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "run"

    async def run(self, image, prompt: str, max_images: int = 1, preview: bool = False):
        from comfy.utils import ProgressBar

        with temp_image(image) as image_path:
            if not image_path:
                return (empty_image_tensor(),)
            pbar = ProgressBar(100)
            images = await imagine_edit(
                prompt, image_path=image_path, max_images=max_images, pbar=pbar, preview=preview
            )
            return (bytes_list_to_tensor(images),)


class GrokTextToVideoNode:
    """Text-to-video generation with Grok Imagine."""

    DISPLAY_NAME = "xAI Grok Imagine Video"
    CATEGORY = "Specter/video/xAI"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "Video description."}),
            },
            "optional": {
                "size": (GROK_SIZES, {"default": GROK_SIZES[0], "tooltip": "Video size/aspect ratio."}),
                "mode": (GROK_MODES, {"default": "custom", "tooltip": "Content mode: normal, custom, fun, spicy."}),
                "preview": ("BOOLEAN", {"default": False, "tooltip": "Show browser preview."}),
            },
        }

    RETURN_TYPES = ("VIDEO", "IMAGE")
    RETURN_NAMES = ("video", "last_frame")
    FUNCTION = "run"

    async def run(
        self,
        prompt: str,
        size: str = "1:1 Square (960x960)",
        mode: str = "custom",
        preview: bool = False,
    ):
        from io import BytesIO

        from comfy.utils import ProgressBar
        from comfy_api.input_impl import VideoFromFile

        pbar = ProgressBar(100)
        video_bytes = await imagine_t2v(prompt, size=size, mode=mode, pbar=pbar, preview=preview)
        if not video_bytes:
            raise RuntimeError("Video generation failed - no video captured")
        last_frame = extract_last_frame_from_video(video_bytes)
        return (VideoFromFile(BytesIO(video_bytes)), last_frame)


class GrokImageToVideoNode:
    """Image-to-video generation with Grok Imagine."""

    DISPLAY_NAME = "xAI Grok Imagine Video I2V"
    CATEGORY = "Specter/video/xAI"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "Source image for video."}),
            },
            "optional": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "Motion/action description."}),
                "mode": (GROK_MODES, {"default": "custom", "tooltip": "Content mode: normal, custom, fun, spicy."}),
                "preview": ("BOOLEAN", {"default": False, "tooltip": "Show browser preview."}),
            },
        }

    RETURN_TYPES = ("VIDEO", "IMAGE")
    RETURN_NAMES = ("video", "last_frame")
    FUNCTION = "run"

    async def run(self, image, prompt: str = "", mode: str = "custom", preview: bool = False):
        from io import BytesIO

        from comfy.utils import ProgressBar
        from comfy_api.input_impl import VideoFromFile

        with temp_image(image) as image_path:
            if not image_path:
                raise RuntimeError("Image required for image-to-video")
            pbar = ProgressBar(100)
            video_bytes = await imagine_i2v(image_path, prompt=prompt, mode=mode, pbar=pbar, preview=preview)
            if not video_bytes:
                raise RuntimeError("Video generation failed - no video captured")
            last_frame = extract_last_frame_from_video(video_bytes)
            return (VideoFromFile(BytesIO(video_bytes)), last_frame)


# =============================================================================
# UTILITY NODES
# =============================================================================


_TEXT_ONLY_SYSTEM_SUFFIX = " Output ONLY text. Do NOT generate images."
_TEXT_ONLY_PROMPT_SUFFIX = "\n\n[Reply with text only. Do not generate images.]"


def _create_prompt_enhancer(provider: str, display_prefix: str, chat_fn, default_model: str):
    """Factory for prompt enhancer nodes."""

    class _PromptEnhancerNode:
        DISPLAY_NAME = f"{display_prefix} Prompt Enhancer"
        CATEGORY = "Specter/tools"

        @classmethod
        def INPUT_TYPES(cls):
            models = get_models(provider) or [default_model]
            presets = ["custom"] + (get_presets_by_category("prompt_enhancement") or ["prompt_enhancer"])
            return {
                "required": {
                    "input_prompt": ("STRING", {"multiline": True, "default": "", "tooltip": TOOLTIPS["input_prompt"]}),
                },
                "optional": {
                    "model": (models, {"default": models[0], "tooltip": TOOLTIPS["model"]}),
                    "style": (presets, {"default": presets[1], "tooltip": TOOLTIPS["enhancement_style"]}),
                    "additional_instructions": (
                        "STRING",
                        {
                            "multiline": True,
                            "default": "",
                            "tooltip": "Additional instructions, or full prompt if style is 'custom'.",
                        },
                    ),
                    "preview": ("BOOLEAN", {"default": False, "tooltip": TOOLTIPS["preview"]}),
                },
            }

        RETURN_TYPES = ("STRING",)
        RETURN_NAMES = ("enhanced_prompt",)
        FUNCTION = "run"

        async def run(
            self,
            input_prompt: str,
            model: str | None = None,
            style: str = "prompt_enhancer",
            additional_instructions: str | None = None,
            preview: bool = False,
        ):
            from comfy.utils import ProgressBar

            model = model or default_model
            extra = additional_instructions.strip() if additional_instructions else ""
            if style == "custom":
                system = (extra or "Enhance this prompt for image generation.") + _TEXT_ONLY_SYSTEM_SUFFIX
            else:
                base = get_preset_prompt(style) or "Enhance this prompt for image generation."
                system = f"{base} {extra}".strip() + _TEXT_ONLY_SYSTEM_SUFFIX
            prompt = input_prompt + _TEXT_ONLY_PROMPT_SUFFIX

            pbar = ProgressBar(100)
            response, _ = await chat_fn(
                prompt, model, None, system_message=system, pbar=pbar, preview=preview, disable_tools=True
            )
            return (response,)

    return _PromptEnhancerNode


def _create_image_describer(provider: str, display_prefix: str, chat_fn, default_model: str):
    """Factory for image describer nodes."""

    class _ImageDescriberNode:
        DISPLAY_NAME = f"{display_prefix} Image Describer"
        CATEGORY = "Specter/tools"

        @classmethod
        def INPUT_TYPES(cls):
            models = get_models(provider) or [default_model]
            presets = ["custom"] + (get_presets_by_category("image_description") or ["image_describer"])
            return {
                "required": {
                    "image": ("IMAGE", {"tooltip": "Image to describe."}),
                },
                "optional": {
                    "model": (models, {"default": models[0], "tooltip": TOOLTIPS["model"]}),
                    "style": (presets, {"default": presets[1], "tooltip": TOOLTIPS["description_style"]}),
                    "additional_instructions": (
                        "STRING",
                        {
                            "multiline": True,
                            "default": "",
                            "tooltip": "Additional instructions, or full prompt if style is 'custom'.",
                        },
                    ),
                    "preview": ("BOOLEAN", {"default": False, "tooltip": TOOLTIPS["preview"]}),
                },
            }

        RETURN_TYPES = ("STRING",)
        RETURN_NAMES = ("description",)
        FUNCTION = "run"

        async def run(
            self,
            image,
            model: str | None = None,
            style: str = "image_describer",
            additional_instructions: str | None = None,
            preview: bool = False,
        ):
            from comfy.utils import ProgressBar

            model = model or default_model
            extra = additional_instructions.strip() if additional_instructions else ""
            if style == "custom":
                system = (extra or "Describe this image in detail.") + _TEXT_ONLY_SYSTEM_SUFFIX
            else:
                base = get_preset_prompt(style) or "Describe this image in detail."
                system = f"{base} {extra}".strip() + _TEXT_ONLY_SYSTEM_SUFFIX
            prompt = "Describe this image." + _TEXT_ONLY_PROMPT_SUFFIX

            with temp_image(image) as image_path:
                pbar = ProgressBar(100)
                response, _ = await chat_fn(
                    prompt, model, image_path, system_message=system, pbar=pbar, preview=preview
                )
                return (response,)

    return _ImageDescriberNode


class GrokVideoCombineNode:
    """Combine two Grok videos sequentially for extended generation."""

    DISPLAY_NAME = "xAI Grok Video Combine"
    CATEGORY = "Specter/video/xAI"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video1": ("VIDEO", {"tooltip": "First video."}),
                "video2": ("VIDEO", {"tooltip": "Second video (will follow video1)."}),
            },
            "optional": {
                "audio": ("BOOLEAN", {"default": True, "tooltip": "Include audio from both videos."}),
            },
        }

    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("combined_video",)
    FUNCTION = "run"

    def run(self, video1, video2, audio: bool = True):
        from io import BytesIO

        from comfy_api.input_impl import VideoFromFile

        combined_bytes = combine_videos(video_to_bytes(video1), video_to_bytes(video2), audio=audio)
        return (VideoFromFile(BytesIO(combined_bytes)),)


# Generate utility nodes for both providers
PromptEnhancerNode = _create_prompt_enhancer("chatgpt", "OpenAI", chat_with_gpt, "gpt-5-2-instant")
GrokPromptEnhancerNode = _create_prompt_enhancer("grok", "xAI Grok", chat_with_grok, "grok-3")
ImageDescriberNode = _create_image_describer("chatgpt", "OpenAI", chat_with_gpt, "gpt-5-2-instant")
GrokImageDescriberNode = _create_image_describer("grok", "xAI Grok", chat_with_grok, "grok-3")


# =============================================================================
# NODE REGISTRATION
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


def _register_nodes() -> tuple[dict, dict]:
    """Auto-register all *Node classes with DISPLAY_NAME, CATEGORY, and FUNCTION."""
    import sys

    module = sys.modules[__name__]
    class_mappings = {}
    display_mappings = {}

    for name in dir(module):
        if not name.endswith("Node"):
            continue
        cls = getattr(module, name)
        if not (hasattr(cls, "DISPLAY_NAME") and hasattr(cls, "CATEGORY") and hasattr(cls, "FUNCTION")):
            continue

        # Wrap run method with log context
        run_method = getattr(cls, cls.FUNCTION)
        wrapped = _wrap_with_context(run_method, cls.DISPLAY_NAME)
        setattr(cls, cls.FUNCTION, wrapped)

        key = f"Specter_{name.removesuffix('Node')}"
        class_mappings[key] = cls
        display_mappings[key] = cls.DISPLAY_NAME

    return class_mappings, display_mappings


NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS = _register_nodes()
