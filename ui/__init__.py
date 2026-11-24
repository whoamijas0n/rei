"""
REI - UI Module
Package initialization for UI rendering, views, and input handlers.

Imports are guarded so that a machine (or unit-test runner) without Pillow can
still import ``ui.input_handler`` and friends; the rendering classes simply
stay unavailable instead of crashing the whole package.
"""

from .input_handler import InputEvent, GPIOInputHandler

try:
    from .display import (
        ScreenManager,
        BaseView,
        HeroCardDeckView,
        DetailCardView,
        HeroCard,
        IconRenderer,
        VirtualKeyboardInputView,
        KeyboardLayer,
    )
    from .wifi_password_view import WiFiPasswordView

    KeyboardInputView = VirtualKeyboardInputView
except ImportError:  # Pillow not installed: rendering stack unavailable.
    ScreenManager = None
    BaseView = None
    HeroCardDeckView = None
    DetailCardView = None
    HeroCard = None
    IconRenderer = None
    VirtualKeyboardInputView = None
    KeyboardInputView = None
    KeyboardLayer = None
    WiFiPasswordView = None

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
    "WiFiPasswordView",
]
