"""
REI - UI Module
Package initialization for UI rendering, views, and input handlers.
"""

from .input_handler import InputEvent, GPIOInputHandler

__all__ = [
    "InputEvent",
    "GPIOInputHandler",
]

try:
    from .display import (
        ScreenManager,
        BaseView,
        HeroCardDeckView,
        DetailCardView,
        HeroCard,
        IconRenderer,
        WiFiPasswordView,
        VirtualKeyboardInputView,
        KeyboardInputView,
        KeyboardLayer,
    )

    __all__.extend([
        "ScreenManager",
        "BaseView",
        "HeroCardDeckView",
        "DetailCardView",
        "HeroCard",
        "IconRenderer",
        "WiFiPasswordView",
        "VirtualKeyboardInputView",
        "KeyboardInputView",
        "KeyboardLayer",
    ])
except ImportError as _ui_import_err:
    if "PIL" not in str(_ui_import_err):
        raise

