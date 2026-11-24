"""
REI - Wi-Fi connection outcome screen.

Dedicated result view for the Wi-Fi flow: a big icon, one clear ASCII reason
line and an action hint. KEY2 retries the connection when the error catalog
marks the failure as retryable, without navigating through the card deck.
"""

from typing import Callable, List, Optional

from PIL import ImageDraw

from .display import BaseView, IconRenderer, ViewAction, ViewActionType
from .input_handler import InputEvent
from .text import to_oled


class WiFiResultView(BaseView):
    """
    Renderiza el resultado de una conexion Wi-Fi en la OLED 128x64.

    Geometria: borde 1px, cabecera (SSID) centrada en y=4, icono 20x20
    centrado en (64, 22), motivo centrado en y=38 y accion al pie en y=50.
    """

    def __init__(
        self,
        title: str,
        icon_name: str,
        reason: str,
        hint: str = "K3=SALIR",
        on_retry: Optional[Callable[[], None]] = None,
        pop_to_root_on_key1: bool = True,
    ):
        super().__init__(title=title)
        self.icon_name = icon_name
        self.reason = to_oled(reason)[:20]
        self.hint = to_oled(hint)[:20]
        self.on_retry = on_retry
        self.pop_to_root_on_key1 = pop_to_root_on_key1

    def set_result(
        self,
        icon_name: str,
        reason: str,
        hint: str,
        on_retry: Optional[Callable[[], None]] = None,
    ) -> None:
        """Updates the outcome in place (used when reusing the view)."""
        self.icon_name = icon_name
        self.reason = to_oled(reason)[:20]
        self.hint = to_oled(hint)[:20]
        self.on_retry = on_retry

    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        # 1. Borde perimetral continuo (1, 1) - (126, 62)
        self.draw_perimeter_border(draw)

        # 2. Cabecera: SSID / pantalla
        header = (self.title or "").strip().upper()[:20]
        self.draw_centered_text(draw, header, y=4, screen_width=width)
        draw.line((4, 15, 123, 15), fill="white")

        # 3. Icono 20x20 centrado
        IconRenderer.draw_icon(draw, self.icon_name, cx=64, cy=22)

        # 4. Motivo del resultado
        self.draw_centered_text(draw, self.reason, y=38, screen_width=width)

        # 5. Accion disponible al pie
        if self.hint:
            self.draw_centered_text(draw, self.hint, y=50, screen_width=width)

    def handle_input(self, event: InputEvent, **kwargs) -> ViewAction:
        if event == InputEvent.KEY2:
            if self.on_retry:
                self.on_retry()
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.KEY1, InputEvent.PRESS):
            if self.pop_to_root_on_key1:
                return ViewAction(ViewActionType.POP_TO_ROOT)
            return ViewAction(ViewActionType.POP_VIEW)

        if event in (InputEvent.KEY3, InputEvent.BACK):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)
