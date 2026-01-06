"""Shared utilities for Specter nodes."""

import os
import tempfile
from contextlib import contextmanager

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
    from io import BytesIO

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
