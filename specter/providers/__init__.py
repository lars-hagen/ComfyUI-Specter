"""Specter providers."""

from .chatgpt import chat_with_gpt
from .gemini import chat_with_gemini
from .grok_chat import (
    AGE_VERIFICATION_SCRIPT,
    DISMISS_NOTIFICATIONS_SCRIPT,
    chat_with_grok,
)
from .grok_chat import (
    LOGIN_SELECTORS as GROK_LOGIN_SELECTORS,
)
from .grok_t2i import imagine_t2i
from .grok_video import SIZES, VIDEO_MODES, imagine_edit, imagine_i2v, imagine_t2v

__all__ = [
    "SIZES",
    "VIDEO_MODES",
    "GROK_LOGIN_SELECTORS",
    "AGE_VERIFICATION_SCRIPT",
    "DISMISS_NOTIFICATIONS_SCRIPT",
    "chat_with_gemini",
    "chat_with_gpt",
    "chat_with_grok",
    "imagine_edit",
    "imagine_i2v",
    "imagine_t2i",
    "imagine_t2v",
]
