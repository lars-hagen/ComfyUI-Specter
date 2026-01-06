"""Custom exceptions for Specter nodes."""


class SpecterException(Exception):
    """Base exception for all Specter errors."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self):
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


class LoginRequiredException(SpecterException):
    """Raised when login is required but not available."""

    def __init__(self, service: str = "chatgpt"):
        super().__init__(
            f"Login required for {service}. Please run with force_login=True or login manually.", {"service": service}
        )


class LoginFailedException(SpecterException):
    """Raised when login attempt fails."""

    def __init__(self, service: str, reason: str = "Unknown error"):
        super().__init__(f"Failed to login to {service}: {reason}", {"service": service, "reason": reason})


class SessionExpiredException(SpecterException):
    """Raised when session has expired."""

    def __init__(self, service: str = "chatgpt"):
        super().__init__(f"Session expired for {service}. Please login again.", {"service": service})


class BrowserException(SpecterException):
    """Base exception for browser-related errors."""

    pass


class BrowserLaunchException(BrowserException):
    """Raised when browser fails to launch."""

    def __init__(self, reason: str = "Unknown error"):
        super().__init__(f"Failed to launch browser: {reason}", {"reason": reason})


class BrowserNavigationException(BrowserException):
    """Raised when page navigation fails."""

    def __init__(self, url: str, reason: str = "Timeout"):
        super().__init__(f"Failed to navigate to {url}: {reason}", {"url": url, "reason": reason})


class BrowserTimeoutException(BrowserException):
    """Raised when browser operation times out."""

    def __init__(self, operation: str, timeout_seconds: int):
        super().__init__(
            f"Operation '{operation}' timed out after {timeout_seconds}s",
            {"operation": operation, "timeout": timeout_seconds},
        )


class ChatException(SpecterException):
    """Base exception for chat-related errors."""

    pass


class MessageSendException(ChatException):
    """Raised when message fails to send."""

    def __init__(self, reason: str = "Unknown error"):
        super().__init__(f"Failed to send message: {reason}", {"reason": reason})


class ResponseTimeoutException(ChatException):
    """Raised when waiting for response times out."""

    def __init__(self, timeout_seconds: int = 300):
        super().__init__(f"No response received within {timeout_seconds}s", {"timeout": timeout_seconds})


class ResponseParseException(ChatException):
    """Raised when response cannot be parsed."""

    def __init__(self, reason: str = "Unknown format"):
        super().__init__(f"Failed to parse response: {reason}", {"reason": reason})


class ImageException(SpecterException):
    """Base exception for image-related errors."""

    pass


class ImageCaptureException(ImageException):
    """Raised when image capture fails."""

    def __init__(self, reason: str = "No image found"):
        super().__init__(f"Failed to capture image: {reason}", {"reason": reason})


class ImageGenerationException(ImageException):
    """Raised when image generation fails."""

    def __init__(self, reason: str = "Generation failed"):
        super().__init__(f"Image generation failed: {reason}", {"reason": reason})


class RateLimitException(SpecterException):
    """Raised when rate limit is hit."""

    def __init__(self, service: str = "chatgpt", retry_after: int | None = None):
        message = f"Rate limit reached for {service}"
        if retry_after:
            message += f". Retry after {retry_after}s"
        super().__init__(message, {"service": service, "retry_after": retry_after})
        self.retry_after = retry_after


class ModelNotFoundException(SpecterException):
    """Raised when requested model is not available."""

    def __init__(self, model: str, available_models: list | None = None):
        super().__init__(
            f"Model '{model}' not found or not available", {"model": model, "available": available_models or []}
        )


class ConfigurationException(SpecterException):
    """Raised when configuration is invalid."""

    def __init__(self, config_file: str, reason: str):
        super().__init__(
            f"Invalid configuration in {config_file}: {reason}", {"config_file": config_file, "reason": reason}
        )
