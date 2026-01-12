"""Grok service - Browser automation for X/Grok web interface."""

from ...core.browser import log
from ..base import ChatService, ServiceConfig

# Defined here to avoid circular import with __init__.py
AGE_VERIFICATION_INIT_SCRIPT = """localStorage.setItem('age-verif', '{"state":{"stage":"pass"},"version":3}');"""
DISMISS_NOTIFICATIONS_SCRIPT = """localStorage.setItem('notifications-toast-dismiss-count', '999');"""


class GrokService(ChatService):
    """Grok implementation of ChatService."""

    config = ServiceConfig(
        service_name="grok",
        base_url="https://grok.com",
        selectors={
            "textarea": 'textarea[aria-label="Ask Grok anything"], div[contenteditable="true"]',
            "response": ".response-content-markdown",
        },
        login_selectors=[
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'button:has-text("Sign up")',
            'a[href*="/login"]',
            'a[href*="/i/flow/login"]',
            '[data-testid="loginButton"]',
        ],
        login_event="specter-grok-login-required",
        image_url_patterns=["assets.grok.com"],
        image_min_size=80000,  # 80KB for Grok images (thumbnails are ~50KB)
        response_timeout=40,
        init_scripts=[AGE_VERIFICATION_INIT_SCRIPT, DISMISS_NOTIFICATIONS_SCRIPT],
    )

    def _get_intercept_pattern(self) -> str:
        return "**/rest/app-chat/**"

    def _is_api_response(self, url: str) -> bool:
        return "rest/app-chat" in url

    def _needs_interception(self, model: str, system_message: str | None, **kwargs) -> bool:
        """Grok always needs interception to disable side-by-side feedback."""
        return True

    def modify_request_body(self, body: dict, model: str, system_message: str | None, **kwargs) -> bool:
        """Modify request for model, image count and system message."""
        modified = False
        image_count = kwargs.get("image_count")
        disable_tools = kwargs.get("disable_tools", False)

        # Disable tools at API level (for prompt enhancer)
        if disable_tools:
            if not body.get("disableSearch"):
                log("Intercepting request: disableSearch = true", "⟳")
                body["disableSearch"] = True
                modified = True
            if body.get("enableImageGeneration"):
                log("Intercepting request: enableImageGeneration = false", "⟳")
                body["enableImageGeneration"] = False
                modified = True

        # Disable side-by-side feedback dialog
        if body.get("enableSideBySide"):
            log("Intercepting request: enableSideBySide = false", "⟳")
            body["enableSideBySide"] = False
            modified = True

        # Disable text follow-ups
        if not body.get("disableTextFollowUps"):
            log("Intercepting request: disableTextFollowUps = true", "⟳")
            body["disableTextFollowUps"] = True
            modified = True

        # Disable self-harm short circuit
        if not body.get("disableSelfHarmShortCircuit"):
            log("Intercepting request: disableSelfHarmShortCircuit = true", "⟳")
            body["disableSelfHarmShortCircuit"] = True
            modified = True

        # Inject model
        if model and "modelName" in body and body.get("modelName") != model:
            log(f"Intercepting request: modelName = '{model}'", "⟳")
            body["modelName"] = model
            modified = True

        # Modify image generation count
        if image_count is not None and "imageGenerationCount" in body:
            if body["imageGenerationCount"] != image_count:
                log(f"Intercepting request: imageGenerationCount = {image_count}", "⟳")
                body["imageGenerationCount"] = image_count
                modified = True

        # Inject system message via customPersonality
        if system_message:
            log(f"Intercepting request: customPersonality = '{system_message[:40]}...')", "⟳")
            # Full system message in debug mode
            from ...core.debug import is_debug_logging_enabled

            if is_debug_logging_enabled():
                log(f"Full system message:\n{system_message}", "◆")
            body["customPersonality"] = system_message
            modified = True

        return modified

    async def handle_api_response_body(self, body: dict) -> dict | None:
        """Parse Grok's streaming API response for completion signals."""
        if not body:
            return None

        # Extract nested response object
        result = body.get("result", {})
        response = result.get("response", {})

        # Check for modelResponse with message (this means response is complete)
        model_response = response.get("modelResponse", {})
        text = model_response.get("message", "")

        if text:
            log("API completion: modelResponse received", "✓")
            return {
                "complete": True,
                "text": text,
                "metadata": response.get("finalMetadata", {}),
            }

        return None

    async def handle_upload_response_body(self, body: dict) -> dict | None:
        """Detect Grok upload completion."""
        if "fileMetadataId" in body and "fileMimeType" in body:
            return {
                "complete": True,
                "file_id": body["fileMetadataId"],
                "metadata": body,
            }
        return None

    def _matches_image_pattern(self, url: str) -> bool:
        """Only capture images from /generated/ path (not uploads)."""
        return "assets.grok.com" in url and "/generated/" in url

    async def extract_response_text(self) -> str:
        """Extract response text from Grok's markdown response."""
        try:
            response_el = self.page.locator(self.config.selectors["response"]).last
            if await response_el.count() > 0:
                return await response_el.inner_text(timeout=2000)
        except Exception:
            pass
        return ""


# Singleton instance
_service = GrokService()


async def chat_with_grok(
    prompt: str,
    model: str = "grok-3",
    image_path: str | None = None,
    system_message: str | None = None,
    pbar=None,
    preview: bool = False,
    image_count: int | None = None,
    _expect_image: bool = False,
    disable_tools: bool = False,
) -> tuple[str, bytes | None]:
    """Send message to Grok and return response text + captured image.

    This is the public API that nodes use.
    """
    return await _service.chat(
        prompt=prompt,
        model=model,
        image_path=image_path,
        system_message=system_message,
        pbar=pbar,
        preview=preview,
        image_count=image_count,
        _expect_image=_expect_image,
        disable_tools=disable_tools,
    )
