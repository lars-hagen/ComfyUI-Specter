"""ChatGPT service - Browser automation for ChatGPT web interface."""

import uuid

from ..core.browser import log
from .base import ChatService, ServiceConfig


class ChatGPTService(ChatService):
    """ChatGPT implementation of ChatService."""

    def __init__(self):
        super().__init__()
        self._async_status_ok = False  # Track when async-status returns OK
        self._pending_file_ids: set[str] = set()  # Files waiting for async completion

    config = ServiceConfig(
        service_name="chatgpt",
        base_url="https://chatgpt.com/",
        selectors={
            "textarea": "#prompt-textarea",
            "send_button": '[data-testid="send-button"]',
            "response": '[data-message-author-role="assistant"]',
        },
        login_selectors=[
            'button:has-text("Log in")',
            'a:has-text("Log in")',
            'button:has-text("Sign up")',
            'a:has-text("Sign up")',
        ],
        login_event="specter-login-required",
        image_url_patterns=["estuary/content", "oaiusercontent.com"],
        image_min_size=500000,  # 500KB for ChatGPT images
        completion_selectors=[
            'button[aria-label="Download this image"]',
            'button[aria-label="Like this image"]',
            'button[data-testid="good-response-turn-action-button"]',
        ],
        response_timeout=300,
        init_scripts=[
            """localStorage.setItem('oai/apps/theme', '"dark"');""",
        ],
        cookies=[
            {"name": "oai-allow-ne", "value": "true", "domain": ".chatgpt.com", "path": "/"},
        ],
    )

    def _get_intercept_pattern(self) -> str:
        return "**/backend-api/**/conversation"

    def _is_api_response(self, url: str) -> bool:
        return "backend-api/f/conversation" in url or "backend-api/conversation" in url

    def modify_request_body(self, body: dict, model: str, system_message: str | None, **kwargs) -> bool:
        """Modify request to set model and inject system message."""
        modified = False

        if model and "model" in body and body["model"] != model:
            log(f"Intercepting request: {body['model']} → {model}", "⟳")
            body["model"] = model
            modified = True

        if system_message and "messages" in body and body["messages"]:
            system_msg = {
                "id": str(uuid.uuid4()),
                "author": {"role": "system"},
                "content": {"content_type": "text", "parts": [system_message]},
                "metadata": {},
            }
            body["messages"].insert(0, system_msg)
            log(f'Injecting system prompt: "{system_message[:40]}..."', "⟳")
            modified = True

        return modified

    async def _click_send(self, send_btn):
        """ChatGPT uses Enter key instead of button click."""
        await self.page.keyboard.press("Enter")

    async def extract_response_text(self) -> str:
        """Extract response text from ChatGPT's markdown prose."""
        try:
            prose = self.page.locator(f"{self.config.selectors['response']} .markdown.prose").last
            if await prose.count() > 0:
                return await prose.inner_text(timeout=2000)
        except:
            pass
        return ""


# Singleton instance
_service = ChatGPTService()


async def chat_with_gpt(
    prompt: str,
    model: str,
    image_path: str | None = None,
    system_message: str | None = None,
    pbar=None,
    preview: bool = False,
    _expect_image: bool = False,
) -> tuple[str, bytes | None]:
    """Send message to ChatGPT and return response text + captured image.

    This is the public API that nodes use.
    """
    return await _service.chat(
        prompt=prompt,
        model=model,
        image_path=image_path,
        system_message=system_message,
        pbar=pbar,
        preview=preview,
        _expect_image=_expect_image,
    )
