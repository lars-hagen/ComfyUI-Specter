"""Shared utilities for Specter nodes."""

import os
import subprocess
import tempfile
from contextlib import contextmanager
from io import BytesIO

import numpy as np
import torch
from PIL import Image


def tensor_to_pil(tensor: torch.Tensor) -> Image.Image:
    """Convert tensor to PIL Image."""
    if tensor.dim() == 4:
        tensor = tensor[0]
    arr = (tensor.cpu().numpy() * 255).astype(np.uint8)
    return Image.fromarray(arr)


def bytes_to_tensor(image_bytes: bytes) -> torch.Tensor:
    """Convert image bytes to tensor."""

    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


def bytes_list_to_tensor(images: list[bytes]) -> torch.Tensor:
    """Convert list of image bytes to batched tensor."""
    if not images:
        return empty_image_tensor()

    tensors = [bytes_to_tensor(img) for img in images]
    return torch.cat(tensors, dim=0)


def empty_image_tensor() -> torch.Tensor:
    """Return 1x1 black image tensor."""
    return torch.zeros((1, 1, 1, 3), dtype=torch.float32)


def create_dummy_image() -> str:
    """Create 1x1 transparent PNG for edit flow experiments."""
    import base64

    # 1x1 transparent PNG (67 bytes)
    png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    png_data = base64.b64decode(png_b64)

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".png", delete=False) as f:
        f.write(png_data)
        return f.name


@contextmanager
def temp_image(tensor_or_none):
    """Context manager for temporary image file from tensor.

    Usage:
        with temp_image(image_tensor) as path:
            # path is None if tensor was None
            # path is temp file path if tensor was provided
            await some_function(path)
        # File automatically cleaned up
    """
    if tensor_or_none is None:
        yield None
        return

    pil_img = tensor_to_pil(tensor_or_none)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        pil_img.save(f.name)
        path = f.name

    try:
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)


@contextmanager
def temp_video(video_bytes: bytes, suffix=".mp4"):
    """Context manager for temporary video file from bytes.

    Usage:
        with temp_video(video_bytes) as path:
            # path is temp file with video written
            do_something(path)
        # File automatically cleaned up
    """
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(fd, video_bytes)
        os.close(fd)
        yield path
    finally:
        try:
            os.unlink(path)
        except:
            pass


def extract_last_frame_from_video(video_bytes: bytes) -> torch.Tensor:
    """Extract the actual last frame from video bytes as IMAGE tensor.

    Args:
        video_bytes: Raw MP4/WebM video bytes

    Returns:
        IMAGE tensor (1, H, W, 3) suitable for ComfyUI nodes
    """
    with temp_video(video_bytes) as video_path:
        # Extract last frame using ffmpeg reverse
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            frame_path = f.name

        try:
            # Use reverse filter to get actual last frame (first frame when reversed)
            cmd = [
                "ffmpeg",
                "-i",
                video_path,
                "-vf",
                "reverse",
                "-frames:v",
                "1",
                "-y",
                frame_path,
            ]
            subprocess.run(cmd, capture_output=True, check=True)

            # Load frame as tensor
            img = Image.open(frame_path).convert("RGB")
            arr = np.array(img).astype(np.float32) / 255.0
            return torch.from_numpy(arr).unsqueeze(0)
        finally:
            if os.path.exists(frame_path):
                os.unlink(frame_path)


def video_to_bytes(video) -> bytes:
    """Extract bytes from various VIDEO type formats.

    Handles: dict with filename, str path, VideoFromFile (save_to), raw bytes.
    """
    import tempfile

    if isinstance(video, bytes):
        return video
    if isinstance(video, str):
        with open(video, "rb") as f:
            return f.read()
    if isinstance(video, dict):
        if "filename" in video:
            with open(video["filename"], "rb") as f:
                return f.read()
        raise RuntimeError(f"Unknown video dict format: {list(video.keys())}")
    if hasattr(video, "save_to"):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            temp_path = f.name
        try:
            video.save_to(temp_path)
            with open(temp_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    raise RuntimeError(f"Invalid video type: {type(video).__name__}")


def combine_videos(video1_bytes: bytes, video2_bytes: bytes, audio: bool = True) -> bytes:
    """Combine two videos sequentially with optional audio.

    Args:
        video1_bytes: First video (raw bytes)
        video2_bytes: Second video (raw bytes)
        audio: Include audio from both videos (default True)

    Returns:
        Combined video bytes (MP4 format)
    """
    with temp_video(video1_bytes, ".mp4") as v1_path, temp_video(video2_bytes, ".mp4") as v2_path:
        # Create output file
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            output_path = f.name

        try:
            # Use concat demuxer for lossless merge
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                concat_file = f.name
                f.write(f"file '{v1_path}'\n")
                f.write(f"file '{v2_path}'\n")

            try:
                if audio:
                    cmd = [
                        "ffmpeg",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        concat_file,
                        "-c",
                        "copy",  # No re-encoding
                        "-y",
                        output_path,
                    ]
                else:
                    cmd = [
                        "ffmpeg",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        concat_file,
                        "-c:v",
                        "copy",
                        "-an",  # No audio
                        "-y",
                        output_path,
                    ]

                subprocess.run(cmd, capture_output=True, check=True)

                # Read combined video
                with open(output_path, "rb") as f:
                    return f.read()
            finally:
                if os.path.exists(concat_file):
                    os.unlink(concat_file)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)
