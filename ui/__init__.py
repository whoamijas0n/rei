"""
REI - UI Module
Package initialization for UI rendering, views, and input handlers.
"""

from .input_handler import InputEvent, GPIOInputHandler
from .display import (
    ScreenManager,
    BaseView,
    HeroCardDeckView,
    DetailCardView,
    HeroCard,
    IconRenderer,
    VirtualKeyboardInputView,
    KeyboardInputView,
    KeyboardLayer,
)

__all__ = [
    "InputEvent",
    "GPIOInputHandler",
    "ScreenManager",
    "BaseView",
    "HeroCardDeckView",
    "DetailCardView",
    "HeroCard",
    "IconRenderer",
    "VirtualKeyboardInputView",
    "KeyboardInputView",
    "KeyboardLayer",
]

