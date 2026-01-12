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
        response_timeout=300,
        image_ready_selector='button[aria-label="Download this image"]',
        init_scripts=[
            """localStorage.setItem('oai/apps/theme', '"dark"');""",
            """localStorage.removeItem('RESUME_TOKEN_STORE_KEY');""",
        ],
        cookies=[
            {"name": "oai-allow-ne", "value": "true", "domain": ".chatgpt.com", "path": "/"},
        ],
    )

    def _get_intercept_pattern(self) -> str:
        return "**/backend-api/**/conversation"

    def _is_api_response(self, url: str) -> bool:
        return (
            "backend-api/f/conversation" in url
            or "backend-api/conversation" in url
            or "backend-api/files/process_upload_stream" in url
        )

    def modify_request_body(self, body: dict, model: str, system_message: str | None, **kwargs) -> bool:
        """Modify request to set model and inject system message."""
        modified = False

        if model and "model" in body and body["model"] != model:
            log(f"Intercepting request: model = '{model}'", "⟳")
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
            log(f"Intercepting request: messages[0] = system('{system_message[:40]}...')", "⟳")
            # Full system message in debug mode
            from ..core.debug import is_debug_logging_enabled

            if is_debug_logging_enabled():
                log(f"Full system message:\n{system_message}", "◆")
            modified = True

        return modified

    async def handle_upload_response_body(self, body: dict) -> dict | None:
        """Detect ChatGPT upload completion."""
        event = body.get("event", "")
        if event == "file.processing.completed" and body.get("progress") == 100:
            return {
                "complete": True,
                "file_id": body.get("file_id", "unknown"),
                "metadata": body,
            }
        return None

    async def extract_response_text(self) -> str:
        """Extract response text from ChatGPT's markdown prose."""
        try:
            prose = self.page.locator(f"{self.config.selectors['response']} .markdown.prose").last
            if await prose.count() > 0:
                return await prose.inner_text(timeout=2000)
        except Exception:
            pass
        return ""

    async def _handle_response(self, response):
        """Override to ignore conversation/init 404 errors (new conversation flow)."""
        if response.status == 404 and "conversation/init" in response.url:
            log("Ignoring 404 from conversation/init (new conversation)", "○")
            return
        await super()._handle_response(response)


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
    disable_tools: bool = False,  # Unused - for API parity with Grok
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
