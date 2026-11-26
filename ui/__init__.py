"""
REI - Módulo de Interfaz OLED (ui)
Inicialización del paquete de vistas, iconos, tema y entrada de hardware.
"""

from .input_handler import GPIOInputHandler, InputEvent

__all__ = [
    "InputEvent",
    "GPIOInputHandler",
]

try:
    from core.errors import NoticeLevel
    from .display import (
        BaseView,
        BatteryTelemetryView,
        ConfirmDialogView,
        DetailCardView,
        DetailEntity,
        DetailHero,
        DetailPage,
        DetailRow,
        HeroCard,
        HeroCardDeckView,
        IconRenderer,
        KeyboardInputView,
        KeyboardLayer,
        NoticeView,
        QRCodeView,
        ScreenManager,
        SystemTelemetryView,
        UpdateProgressView,
        VirtualKeyboardInputView,
        WiFiPasswordView,
    )

    __all__.extend([
        "ScreenManager",
        "BaseView",
        "HeroCardDeckView",
        "DetailCardView",
        "DetailHero",
        "DetailRow",
        "DetailEntity",
        "DetailPage",
        "BatteryTelemetryView",
        "SystemTelemetryView",
        "ConfirmDialogView",
        "NoticeView",
        "NoticeLevel",
        "UpdateProgressView",
        "QRCodeView",
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
