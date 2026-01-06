"""Grok service - Browser automation for X/Grok web interface."""

from ...core.browser import log
from ..base import ChatService, ServiceConfig

# Defined here to avoid circular import with __init__.py
AGE_VERIFICATION_INIT_SCRIPT = """localStorage.setItem('age-verif', '{"state":{"stage":"pass"},"version":3}');"""


class GrokService(ChatService):
    """Grok implementation of ChatService."""

    config = ServiceConfig(
        service_name="grok",
        base_url="https://grok.com",
        selectors={
            "textarea": 'div[contenteditable="true"]',
            "send_button": '[aria-label="Submit"]',
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
        completion_selectors=[
            '[aria-label="Download"]',
            '[aria-label="Like"]',
        ],
        response_timeout=90,
        init_scripts=[AGE_VERIFICATION_INIT_SCRIPT],
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

        # Disable side-by-side feedback dialog
        if body.get("enableSideBySide"):
            body["enableSideBySide"] = False
            modified = True

        # Inject model
        if model and "modelName" in body and body.get("modelName") != model:
            log(f"Switching model: {body.get('modelName')} → {model}", "⟳")
            body["modelName"] = model
            modified = True

        # Modify image generation count
        if image_count is not None and "imageGenerationCount" in body:
            if body["imageGenerationCount"] != image_count:
                log(f"Setting image count: {body['imageGenerationCount']} → {image_count}", "⟳")
                body["imageGenerationCount"] = image_count
                modified = True

        # Inject system message via customPersonality
        if system_message:
            log(f'Injecting system prompt: "{system_message[:40]}..."', "⟳")
            body["customPersonality"] = system_message
            modified = True

        return modified

    async def _click_send(self, send_btn):
        """Grok uses Enter key instead of button click."""
        await self.page.keyboard.press("Enter")

    def _matches_image_pattern(self, url: str) -> bool:
        """Only capture images from /generated/ path (not uploads)."""
        return "assets.grok.com" in url and "/generated/" in url

    async def _check_error_state(self) -> bool:
        """Check for Grok error states and handle feedback dialogs."""
        # Handle "Which response do you prefer?" feedback dialog
        skip_btn = self.page.locator('button:has-text("Skip Selection")')
        if await skip_btn.count() > 0:
            log("Skipping feedback dialog", "○")
            await skip_btn.click()
            return False  # Not an error, continue waiting

        retry_btn = 'button:has-text("Retry")'
        error_msg = 'text="unable to finish"'
        if await self.page.locator(retry_btn).count() > 0:
            log("Grok failed to respond - try again or use different prompt", "✕")
            return True
        if await self.page.locator(error_msg).count() > 0:
            log("Grok was unable to finish", "✕")
            return True
        return False

    async def _check_completion(self) -> bool:
        """Check for Grok completion indicators."""
        for selector in self.config.completion_selectors:
            if await self.page.locator(selector).count() > 0:
                if "Download" in selector:
                    log("Image generation complete", "✦")
                else:
                    log("Response complete", "✓")
                return True
        return False

    async def extract_response_text(self) -> str:
        """Extract response text from Grok's markdown response."""
        try:
            response_el = self.page.locator(self.config.selectors["response"]).last
            if await response_el.count() > 0:
                return await response_el.inner_text(timeout=2000)
        except:
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
    )
