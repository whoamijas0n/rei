"""
REI - Vista OLED para ingreso de credenciales de consola serial mediante teclado USB.

Reutiliza el lenguaje visual unificado de WiFiPasswordView (marco de instrumento,
badge de distribución LA/ES/US, detección no bloqueante de teclado físico USB y
campo compacto de 18 px) para solicitar:
- Usuario de consola (texto visible)
- Contraseña de consola (oculta con alternancia mediante KEY2/Tab)
- Clave Enable opcional (permite enviar vacío con ENTER para continuar en modo lectura '>')
"""

import logging
from typing import Any, Callable, Optional, Tuple

from PIL import ImageDraw

from core.parser import sanitize_oled_text
from .display import BaseView, ViewAction, ViewActionType
from .input_handler import InputEvent
from .keyboard_layouts import get_layout_badge
from .theme import (
    draw_instrument_frame,
    draw_mini_glyph,
    draw_text_vcenter,
    fit_text_px,
    measure_text_px,
)

logger = logging.getLogger("REI.UI.ConsoleAuth")

MAX_CREDENTIAL_LENGTH = 64
FIELD_BOX: Tuple[int, int, int, int] = (6, 31, 121, 48)
DOT_SPACING = 6
DOT_SIZE = 4


class ConsoleCredentialView(BaseView):
    """
    Pantalla de entrada por teclado físico USB para autenticación en consola serial.

    Controles:
    - Teclado USB (CHAR): ingresa caracteres del campo.
    - Backspace: borra el último carácter.
    - KEY2 / Tab: alterna ocultar/mostrar cuando `masked=True`.
    - ENTER / KEY1 / PRESS: confirma y ejecuta `on_submit(value)`.
    - KEY3 / ESC / BACK: cancela y regresa a la vista anterior.
    """

    accepts_text_input: bool = True

    def __init__(
        self,
        title: str,
        field_label: str = "Clave",
        masked: bool = True,
        allow_empty: bool = False,
        placeholder: Optional[str] = None,
        max_length: int = MAX_CREDENTIAL_LENGTH,
        on_submit: Optional[Callable[[str], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        keyboard_status: Optional[Callable[[], bool]] = None,
        layout_provider: Optional[Callable[[], str]] = None,
        usb_gadget_active: bool = False,
    ):
        super().__init__(title=title)
        self.field_label = field_label or "Campo"
        self.secret_field: bool = bool(masked)
        self.is_visible: bool = not bool(masked)
        self.allow_empty: bool = bool(allow_empty)
        self.placeholder = placeholder or (
            "Enter=omitir" if allow_empty else "Escriba en teclado..."
        )
        self.max_length = max(1, int(max_length))
        self.value: str = ""
        self.error_message: str = ""
        self.on_submit = on_submit
        self.on_cancel = on_cancel
        self.keyboard_status = keyboard_status
        self.layout_provider = layout_provider
        self.usb_gadget_active = bool(usb_gadget_active)

        self.cursor_visible: bool = True
        self._cursor_tick: int = 0
        self._keyboard_connected: bool = (
            bool(keyboard_status()) if keyboard_status is not None else True
        )
        raw_layout = layout_provider() if layout_provider is not None else "latam"
        self._active_layout: str = raw_layout if isinstance(raw_layout, str) else "latam"
        self._submitted: bool = False

    @property
    def input_text(self) -> str:
        """Acceso de lectura al texto ingresado."""
        return self.value

    def set_text(self, text: str) -> None:
        """Establece el texto actual y limpia avisos previos."""
        self.value = (text or "")[: self.max_length]
        self.error_message = ""
        self._submitted = False

    def toggle_visibility(self) -> bool:
        """Alterna visibilidad si se trata de un campo secreto."""
        if not self.secret_field:
            return True
        self.is_visible = not self.is_visible
        return self.is_visible

    def _append_char(self, char: Optional[str]) -> None:
        if not char or len(char) != 1:
            return
        if ord(char) < 32 or ord(char) == 127 or char in ("\r", "\n", "\t"):
            return
        if len(self.value) >= self.max_length:
            self.error_message = f"Max. {self.max_length} caract."
            return
        self.value += char
        self.error_message = ""

    def _backspace(self) -> None:
        if self.value:
            self.value = self.value[:-1]
        self.error_message = ""

    def _submit(self) -> ViewAction:
        if self._submitted:
            return ViewAction(ViewActionType.NONE)

        if not self.value and not self.allow_empty:
            self.error_message = f"{sanitize_oled_text(self.field_label)} vacio"
            return ViewAction(ViewActionType.NONE)

        self._submitted = True
        if self.on_submit:
            try:
                self.on_submit(self.value)
            except Exception as ex:
                self._submitted = False
                logger.exception(f"Error al enviar credencial de consola: {ex}")
                self.error_message = "Error al continuar"
        return ViewAction(ViewActionType.NONE)

    def update(self) -> None:
        """Actualiza cursor parpadeante y estado del teclado sin bloquear el hilo UI."""
        self._cursor_tick = (self._cursor_tick + 1) % 30
        self.cursor_visible = self._cursor_tick < 15

        if self.keyboard_status is not None:
            try:
                self._keyboard_connected = bool(self.keyboard_status())
            except OSError:
                self._keyboard_connected = False

        if self.layout_provider is not None:
            try:
                raw_layout = self.layout_provider()
                if isinstance(raw_layout, str):
                    self._active_layout = raw_layout
            except OSError:
                pass

    def handle_input(self, event: InputEvent, char: Optional[str] = None, **kwargs: Any) -> ViewAction:
        """Procesa eventos del teclado USB y controles del HAT OLED."""
        if event == InputEvent.CHAR:
            self._append_char(char)
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.BACKSPACE:
            self._backspace()
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.KEY2 and self.secret_field:
            self.toggle_visibility()
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.ENTER, InputEvent.KEY1, InputEvent.PRESS):
            return self._submit()

        if event in (InputEvent.KEY3, InputEvent.ESCAPE, InputEvent.BACK):
            if self.on_cancel:
                try:
                    self.on_cancel()
                except Exception as ex:
                    logger.debug(f"Error en on_cancel de ConsoleCredentialView: {ex}")
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)

    def _fit_tail_window(
        self,
        draw: ImageDraw.ImageDraw,
        display_text: str,
        cursor_char: str,
        max_px: int,
    ) -> Tuple[str, bool]:
        if measure_text_px(draw, display_text + cursor_char, self.font) <= max_px:
            return display_text, False
        inner_px = max(12, max_px - 8)
        visible = display_text
        while visible and measure_text_px(draw, visible + cursor_char, self.font) > inner_px:
            visible = visible[1:]
        return visible, True

    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        """Renderiza el cuadro de credenciales de consola en 128x64 de 1 bit."""
        has_alert = bool(self.error_message or self.usb_gadget_active or not self._keyboard_connected)
        badge = get_layout_badge(self._active_layout)

        inner_x1, _, inner_x2, _ = draw_instrument_frame(
            draw,
            title=self.title or "CONSOLA",
            icon_glyph="warn" if has_alert else None,
            right_text=badge,
            font=self.small_font,
            width=width,
            height=height,
        )
        self._draw_status_line(draw, inner_x1, inner_x2)
        self._draw_input_field(draw, inner_x1, inner_x2)

    def _draw_status_line(self, draw: ImageDraw.ImageDraw, inner_x1: int, inner_x2: int) -> None:
        y_top, y_bottom = 15, 24
        inner_w = max(16, inner_x2 - inner_x1 + 1)

        if self.error_message:
            err_txt = fit_text_px(draw, sanitize_oled_text(self.error_message), self.font, inner_w)
            draw_text_vcenter(draw, err_txt, x=inner_x1, y_top=y_top, y_bottom=y_bottom, font=self.font, fill="white")
            return

        if self.usb_gadget_active:
            msg = fit_text_px(draw, "USB en modo HID", self.font, inner_w)
            draw_text_vcenter(draw, msg, x=inner_x1, y_top=y_top, y_bottom=y_bottom, font=self.font, fill="white")
            return

        if not self._keyboard_connected:
            msg = fit_text_px(draw, "Conecte teclado USB", self.font, inner_w)
            draw_text_vcenter(draw, msg, x=inner_x1, y_top=y_top, y_bottom=y_bottom, font=self.font, fill="white")
            return

        counter_txt = f"{len(self.value)}/{self.max_length}"
        counter_w = measure_text_px(draw, counter_txt, self.small_font)
        counter_x = max(inner_x1 + 42, inner_x2 - counter_w + 1)

        if self.secret_field:
            eye_glyph = "eye_open" if self.is_visible else "eye_closed"
            gw = draw_mini_glyph(draw, eye_glyph, x=inner_x1, y=17, fill="white")
            label_x = inner_x1 + gw + 3
            mode_txt = "Visible" if self.is_visible else "Oculta"
        else:
            label_x = inner_x1
            mode_txt = sanitize_oled_text(self.field_label)

        mode_max_px = max(12, counter_x - label_x - 4)
        fitted_mode = fit_text_px(draw, mode_txt, self.small_font, mode_max_px)
        if fitted_mode:
            draw_text_vcenter(
                draw,
                fitted_mode,
                x=label_x,
                y_top=y_top,
                y_bottom=y_bottom,
                font=self.small_font,
                fill="white",
            )
        draw_text_vcenter(
            draw,
            counter_txt,
            x=counter_x,
            y_top=y_top,
            y_bottom=y_bottom,
            font=self.small_font,
            fill="white",
        )

    def _draw_input_field(self, draw: ImageDraw.ImageDraw, inner_x1: int, inner_x2: int) -> None:
        bx1, by1, bx2, by2 = FIELD_BOX
        draw.rectangle([bx1, by1, bx2, by2], outline="white", fill="black")

        pad_x = 4
        text_x = bx1 + pad_x
        max_px = (bx2 - bx1) - (pad_x * 2)

        if not self.value:
            hint = sanitize_oled_text(self.placeholder)
            cursor = "_" if self.cursor_visible else ""
            fitted = fit_text_px(draw, f"{hint}{cursor}", self.small_font, max_px)
            draw_text_vcenter(draw, fitted, x=text_x, y_top=by1 + 1, y_bottom=by2 - 1, font=self.small_font, fill="white")
            return

        if self.is_visible or not self.secret_field:
            clean_val = sanitize_oled_text(self.value)
            cursor = "_" if self.cursor_visible else " "
            visible, scrolled = self._fit_tail_window(draw, clean_val, cursor, max_px)
            if scrolled:
                draw_text_vcenter(draw, "<", x=text_x, y_top=by1 + 1, y_bottom=by2 - 1, font=self.font, fill="white")
                draw_text_vcenter(
                    draw,
                    visible + cursor,
                    x=text_x + 7,
                    y_top=by1 + 1,
                    y_bottom=by2 - 1,
                    font=self.font,
                    fill="white",
                )
            else:
                draw_text_vcenter(
                    draw,
                    visible + cursor,
                    x=text_x,
                    y_top=by1 + 1,
                    y_bottom=by2 - 1,
                    font=self.font,
                    fill="white",
                )
        else:
            max_dots = max(1, (max_px - 6) // DOT_SPACING)
            total_chars = len(self.value)
            scrolled = total_chars > max_dots
            dots_to_draw = min(total_chars, max_dots - (1 if scrolled else 0))

            dot_x = text_x
            if scrolled:
                draw_text_vcenter(draw, "<", x=dot_x, y_top=by1 + 1, y_bottom=by2 - 1, font=self.small_font, fill="white")
                dot_x += 7

            dot_y = (by1 + by2 - DOT_SIZE) // 2
            for _ in range(dots_to_draw):
                draw.ellipse([dot_x, dot_y, dot_x + DOT_SIZE, dot_y + DOT_SIZE], fill="white")
                dot_x += DOT_SPACING

            if self.cursor_visible and dot_x + 2 <= bx2 - 3:
                draw.line([(dot_x + 1, by1 + 4), (dot_x + 1, by2 - 4)], fill="white")

