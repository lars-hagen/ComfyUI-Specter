"""ChatGPT node - Browser automation for ChatGPT web interface."""

import asyncio
import json
import os
import tempfile
import time

import numpy as np
import torch
from PIL import Image

from .specter import get_session, load_session, delete_session, is_logged_in, interactive_login

SELECTORS = {
    "textarea": "#prompt-textarea",
    "send_button": '[data-testid="send-button"]',
    "response": '[data-message-author-role="assistant"]',
}

LOGIN_SELECTORS = [
    'button:has-text("Log in")',
    'a:has-text("Log in")',
    'button:has-text("Sign up")',
    'a:has-text("Sign up")',
]

CHATGPT_CONFIG = {
    "service": "chatgpt",
    "login_url": "https://chatgpt.com/auth/login",
    "login_selectors": LOGIN_SELECTORS,
    "success_url_contains": "chatgpt.com",
    "success_url_excludes": "/auth/",
    "workspace_selector": '[data-testid="modal-workspace-switcher"]',
}


def tensor_to_pil(tensor: torch.Tensor) -> Image.Image:
    if tensor.dim() == 4:
        tensor = tensor[0]
    arr = (tensor.cpu().numpy() * 255).astype(np.uint8)
    return Image.fromarray(arr)


def bytes_to_tensor(image_bytes: bytes) -> torch.Tensor:
    from io import BytesIO
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


def empty_image_tensor() -> torch.Tensor:
    """Return 1x1 black image tensor for when no image is generated."""
    return torch.zeros((1, 1, 1, 3), dtype=torch.float32)


def log(msg):
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[Specter:ChatGPT {ts}] {msg}")


async def chat_with_gpt(
    prompt: str,
    model: str,
    image_path: str = None,
    force_login: bool = False,
    pbar=None,
    preview: bool = False,
) -> tuple[str, bytes | None]:
    """Send message to ChatGPT and return response text + captured image."""
    from camoufox.async_api import AsyncCamoufox

    current_progress = [0]
    preview_image = [None]
    captured_images = []
    listening = False
    conversation_count = [0]

    def progress(step):
        if pbar and step > current_progress[0]:
            current_progress[0] = step
            if preview and preview_image[0]:
                pbar.update_absolute(step, 100, ("JPEG", preview_image[0], None))
            else:
                pbar.update_absolute(step, 100)

    async def update_preview(page):
        if not preview:
            return
        try:
            from io import BytesIO
            data = await page.screenshot(type="jpeg", quality=70, clip={"x": 0, "y": 0, "width": 767, "height": 1200})
            img = Image.open(BytesIO(data))
            scale = 500 / img.height
            preview_image[0] = img.resize((int(img.width * scale), 500), Image.Resampling.BILINEAR)
        except:
            pass

    async def handle_response(response):
        nonlocal listening
        if not listening:
            return
        url = response.url
        if 'backend-api/f/conversation' in url or 'backend-api/conversation' in url:
            conversation_count[0] += 1
            progress(min(40 + conversation_count[0] * 3, 50))
        content_type = response.headers.get('content-type', '')
        if 'image' in content_type and response.status == 200:
            if 'estuary/content' in url or 'oaiusercontent.com' in url:
                try:
                    data = await response.body()
                    if len(data) > 500000:
                        captured_images.append(data)
                        log(f"Captured image: {len(data)} bytes")
                        progress(min(50 + len(captured_images) * 15, 95))
                except:
                    pass

    progress(5)
    log("Starting...")

    if force_login:
        log("Force login requested")
        delete_session("chatgpt")

    session = load_session("chatgpt")
    log("Session loaded" if session else "No session found")

    browser = None
    ctx = None
    page = None

    try:
        browser = await AsyncCamoufox(headless=True, humanize=False, window=(767, 1800)).__aenter__()
        ctx = await browser.new_context(storage_state=session) if session else await browser.new_context()
        page = await ctx.new_page()
        page.on("response", handle_response)

        async def intercept(route):
            try:
                post_data = route.request.post_data
                if post_data:
                    body = json.loads(post_data)
                    if 'model' in body and body['model'] != model:
                        log(f"Model: {body['model']} -> {model}")
                        body['model'] = model
                        await route.continue_(post_data=json.dumps(body))
                        return
            except:
                pass
            await route.continue_()

        await page.route("**/backend-api/**/conversation", intercept)
        progress(10)

        await page.goto("https://chatgpt.com/", timeout=60000)

        if not await is_logged_in(page, LOGIN_SELECTORS):
            log("Not logged in, starting login...")
            await page.close()
            await ctx.close()
            session = await interactive_login(**CHATGPT_CONFIG)
            ctx = await browser.new_context(storage_state=session)
            page = await ctx.new_page()
            page.on("response", handle_response)
            await page.route("**/backend-api/**/conversation", intercept)
            await page.goto("https://chatgpt.com/", timeout=60000)
            await page.wait_for_selector(SELECTORS["textarea"], state="visible", timeout=30000)

        progress(20)
        log("Ready")

        if image_path:
            log("Uploading image...")
            await page.locator('input[type="file"]').first.set_input_files(image_path)
            await asyncio.sleep(0.5)
            progress(25)

        await page.locator(SELECTORS["textarea"]).fill(prompt)
        progress(30)

        send_btn = page.locator(SELECTORS["send_button"])
        await send_btn.wait_for(state="visible", timeout=10000)
        for _ in range(50):
            if await send_btn.is_enabled():
                break
            await asyncio.sleep(0.1)
        progress(35)

        listening = True
        await page.keyboard.press("Enter")
        log("Sent")
        await asyncio.sleep(0.3)
        await update_preview(page)
        progress(40)

        await page.wait_for_selector(SELECTORS["response"], timeout=180000)
        progress(50)

        for _ in range(60):
            if conversation_count[0] > 0:
                break
            await asyncio.sleep(0.5)

        download_btn = 'button[aria-label="Download this image"]'
        thumbs_up = 'button[data-testid="good-response-turn-action-button"]'
        wait_start = time.time()
        last_preview = 0

        while time.time() - wait_start < 300:
            await asyncio.sleep(0.5)
            if preview and time.time() - last_preview > 3:
                await update_preview(page)
                last_preview = time.time()
                if preview_image[0]:
                    pbar.update_absolute(current_progress[0], 100, ("JPEG", preview_image[0], None))
            try:
                if await page.locator(download_btn).count() > 0:
                    log("Image complete")
                    break
                if await page.locator(thumbs_up).count() > 0:
                    log("Text complete")
                    break
            except:
                pass
            elapsed = time.time() - wait_start
            if int(elapsed) % 10 == 0:
                progress(min(50 + int(elapsed / 6), 85))

        progress(95)

        # DOM fallback for images
        if not captured_images:
            try:
                imgs = page.locator('[data-message-author-role="assistant"] img')
                for i in range(await imgs.count()):
                    src = await imgs.nth(i).get_attribute('src')
                    if src and src.startswith('http'):
                        resp = await page.request.get(src)
                        if resp.ok:
                            data = await resp.body()
                            if len(data) > 50000:
                                captured_images.append(data)
            except:
                pass

        # Extract text
        response_text = ""
        try:
            prose = page.locator(f'{SELECTORS["response"]} .markdown.prose').last
            if await prose.count() > 0:
                response_text = await prose.inner_text(timeout=2000)
        except:
            pass

        progress(100)
        if preview:
            await update_preview(page)
            if preview_image[0]:
                pbar.update_absolute(100, 100, ("JPEG", preview_image[0], None))

        log(f"Done: {len(response_text)} chars, image: {'yes' if captured_images else 'no'}")
        return response_text, captured_images[-1] if captured_images else None

    except asyncio.CancelledError:
        log("Interrupted - cleaning up browser...")
        raise
    finally:
        if browser:
            try:
                await browser.__aexit__(None, None, None)
                log("Browser closed")
            except:
                pass


class ChatGPTNode:
    """Send prompts to ChatGPT via browser automation."""

    CATEGORY = "Specter"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": ([
                    "gpt-image-1.5",
                    "gpt-5.2", "gpt-5.2-instant", "gpt-5.2-thinking",
                    "gpt-5.1-instant", "gpt-5.1-thinking",
                    "gpt-5-instant", "gpt-5-thinking-mini", "gpt-5-thinking",
                    "gpt-4o", "gpt-4.1", "o3", "o4-mini",
                ],),
            },
            "optional": {
                "image": ("IMAGE",),
                "size": (["auto", "1024x1024 (square)", "1536x1024 (landscape)", "1024x1536 (portrait)"], {"default": "auto"}),
                "preview": ("BOOLEAN", {"default": True}),
                "force_login": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("STRING", "IMAGE")
    RETURN_NAMES = ("response", "image")
    FUNCTION = "run"

    async def run(self, prompt: str, model: str, image=None, size: str = "auto", preview: bool = True, force_login: bool = False):
        from comfy.utils import ProgressBar

        # Handle virtual gpt-image-1.5 model
        actual_model = model
        final_prompt = prompt
        if model == "gpt-image-1.5":
            actual_model = "gpt-5.2-instant"
            if image is not None:
                final_prompt = f"Use image_gen to edit this image: {prompt}"
            else:
                final_prompt = f"Use image_gen to create: {prompt}"

        if size != "auto":
            res = size.split(" ")[0]  # Extract "1024x1024" from "1024x1024 (square)"
            final_prompt = f"{final_prompt}\n\n[Generate image at {res} resolution.]"

        image_path = None
        if image is not None:
            pil_img = tensor_to_pil(image)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                pil_img.save(f.name)
                image_path = f.name

        try:
            pbar = ProgressBar(100)
            response_text, image_bytes = await chat_with_gpt(
                final_prompt, actual_model, image_path, force_login=force_login, pbar=pbar, preview=preview
            )
            output_image = bytes_to_tensor(image_bytes) if image_bytes else empty_image_tensor()
            return (response_text, output_image)
        finally:
            if image_path and os.path.exists(image_path):
                os.unlink(image_path)
