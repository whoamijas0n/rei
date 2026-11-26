"""
REI - Vista OLED para ingreso de contraseña Wi-Fi mediante teclado físico USB.

Alineada con el sistema visual unificado de REI (Opción 3 — Campo compacto):
- Marco fino de instrumento (2, 6)-(125, 57) con SSID y distribución (LA/ES/US)
  incrustados en el borde superior, y borde inferior completamente limpio.
- Línea superior de estado: visibilidad (Oculta/Visible) + contador NN/63, o
  mensaje de error/aviso sin tapar la contraseña ingresada.
- Campo de entrada compacto (18 px de alto) con puntos redondos (oculta),
  texto claro desplazable (visible) o guía contextual.
"""

import logging
from typing import Any, Callable, Optional, Tuple

from PIL import ImageDraw

from core.parser import sanitize_oled_text
from core.wifi import WiFiErrorCode, validate_wifi_password
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

logger = logging.getLogger("REI.UI.WiFiPassword")

MAX_PASSWORD_LENGTH = 63

# Geometría alineada al marco de instrumento (2, 6)-(125, 57)
PW_BOX: Tuple[int, int, int, int] = (6, 31, 121, 48)
DOT_SPACING = 6
DOT_SIZE = 4


class WiFiPasswordView(BaseView):
    """
    Pantalla unificada de ingreso de contraseña Wi-Fi por teclado USB físico.

    Controles preservados:
    - Teclado físico USB (CHAR): escribe caracteres de la contraseña.
    - Backspace / Delete: elimina el último carácter.
    - KEY2 / Tab / F2: alterna entre ocultar y mostrar la contraseña.
    - ENTER / KEY1 / PRESS: valida la clave e inicia la conexión.
    - KEY3 / ESC / BACK: cancela y regresa al menú anterior.
    """

    accepts_text_input: bool = True

    def __init__(
        self,
        ssid: str,
        title: Optional[str] = None,
        on_submit: Optional[Callable[[str, str], None]] = None,
        security: str = "WPA2-PSK",
        masked: bool = True,
        max_length: int = MAX_PASSWORD_LENGTH,
        initial_text: str = "",
        keyboard_status: Optional[Callable[[], bool]] = None,
        layout_provider: Optional[Callable[[], str]] = None,
        usb_gadget_active: bool = False,
    ):
        header_title = title or f"RED: {ssid}"
        super().__init__(title=header_title)
        self.ssid = ssid
        self.on_submit = on_submit
        self.security = (security or "").upper()
        self.max_length = max_length
        self.password: str = (initial_text or "")[:max_length]
        self.is_visible: bool = not bool(masked)
        self.error_message: str = ""
        self.keyboard_status = keyboard_status
        self.layout_provider = layout_provider
        self.usb_gadget_active = bool(usb_gadget_active)

        # Estado de cursor parpadeante (30 FPS) y detección no bloqueante de teclado
        self.cursor_visible: bool = True
        self._cursor_tick: int = 0
        self._keyboard_connected: bool = (
            bool(keyboard_status()) if keyboard_status is not None else True
        )
        raw_layout = layout_provider() if layout_provider is not None else "latam"
        self._active_layout: str = raw_layout if isinstance(raw_layout, str) else "latam"
        self._submitted: bool = False

    @property
    def is_masked(self) -> bool:
        """Compatibilidad con atributos de vistas previas: True cuando está oculta."""
        return not self.is_visible

    @is_masked.setter
    def is_masked(self, value: bool) -> None:
        self.is_visible = not bool(value)

    @property
    def input_text(self) -> str:
        """Alias de compatibilidad para lectura directa del texto ingresado."""
        return self.password

    def set_text(self, text: str) -> None:
        """Establece la contraseña directamente y limpia avisos previos."""
        self.password = (text or "")[: self.max_length]
        self.error_message = ""
        self._submitted = False

    def _append_char(self, char: Optional[str]) -> None:
        """Agrega un carácter imprimible respetando espacios legítimos y límite PSK."""
        if not char or len(char) != 1:
            return
        if ord(char) < 32 or ord(char) == 127 or char in ("\r", "\n", "\t"):
            return
        if len(self.password) >= self.max_length:
            self.error_message = f"Max. {self.max_length} caract."
            return
        self.password += char
        self.error_message = ""

    def _backspace(self) -> None:
        """Elimina el último carácter ingresado."""
        if self.password:
            self.password = self.password[:-1]
        self.error_message = ""

    def toggle_visibility(self) -> bool:
        """Alterna entre mostrar u ocultar la contraseña y devuelve el nuevo estado."""
        self.is_visible = not self.is_visible
        return self.is_visible

    def _validate(self) -> Tuple[bool, str]:
        """Valida longitud y caracteres según el tipo de seguridad de la red."""
        valid, err_msg, code = validate_wifi_password(self.password, security=self.security)
        if not valid and code != WiFiErrorCode.NONE:
            return False, err_msg
        return True, ""

    def _submit_password(self) -> ViewAction:
        """Valida la contraseña y ejecuta el callback de conexión si es válida."""
        if self._submitted:
            return ViewAction(ViewActionType.NONE)

        is_valid, err_msg = self._validate()
        if not is_valid:
            self.error_message = err_msg
            return ViewAction(ViewActionType.NONE)

        self._submitted = True
        try:
            if self.on_submit:
                self.on_submit(self.ssid, self.password)
                return ViewAction(ViewActionType.NONE)
        except Exception as ex:
            self._submitted = False
            logger.exception(f"Error al enviar contraseña Wi-Fi: {ex}")
            self.error_message = "Error al conectar"
            return ViewAction(ViewActionType.NONE)

        return ViewAction(
            ViewActionType.EXECUTE_TASK,
            task_id="sys_wifi_connect",
            payload={"ssid": self.ssid, "password": self.password},
        )

    def update(self) -> None:
        """Actualiza parpadeo del cursor y estado de teclado sin realizar I/O bloqueante."""
        self._cursor_tick = (self._cursor_tick + 1) % 30
        self.cursor_visible = self._cursor_tick < 15

        if self.keyboard_status is not None:
            try:
                self._keyboard_connected = bool(self.keyboard_status())
            except Exception as ex:
                logger.debug(f"Fallo consultando estado del teclado: {ex}")
                self._keyboard_connected = False

        if self.layout_provider is not None:
            try:
                raw_layout = self.layout_provider()
                if isinstance(raw_layout, str):
                    self._active_layout = raw_layout
            except Exception as ex:
                logger.debug(f"Fallo consultando layout de teclado: {ex}")

    def handle_input(self, event: InputEvent, char: Optional[str] = None, **kwargs: Any) -> ViewAction:
        """Procesa eventos del teclado USB y botones físicos del HAT OLED."""
        if event == InputEvent.CHAR:
            self._append_char(char)
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.BACKSPACE:
            self._backspace()
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.KEY2:
            self.toggle_visibility()
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.ENTER, InputEvent.KEY1, InputEvent.PRESS):
            return self._submit_password()

        if event in (InputEvent.KEY3, InputEvent.ESCAPE, InputEvent.BACK):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)

    def _fit_text_to_width(self, draw: ImageDraw.ImageDraw, text: str, max_px: int) -> str:
        """Recorta texto por la derecha para ajustarse exactamente a max_px píxeles."""
        return fit_text_px(draw, text, self.font, max_px)

    def _fit_tail_window(
        self,
        draw: ImageDraw.ImageDraw,
        display_text: str,
        cursor_char: str,
        max_px: int,
    ) -> Tuple[str, bool]:
        """
        Ajusta la ventana visible al final de la contraseña según ancho real en píxeles.
        Retorna (texto_visible, hubo_desplazamiento).
        """
        if measure_text_px(draw, display_text + cursor_char, self.font) <= max_px:
            return display_text, False

        inner_px = max(12, max_px - 8)
        visible = display_text
        while visible and measure_text_px(draw, visible + cursor_char, self.font) > inner_px:
            visible = visible[1:]
        return visible, True

    # ------------------------------------------------------------------ #
    # Render unificado (Opción 3 — Campo compacto + borde inferior limpio)
    # ------------------------------------------------------------------ #

    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        """Renderiza la interfaz Wi-Fi unificada en el canvas 128x64 de 1 bit."""
        has_alert = bool(self.error_message or self.usb_gadget_active or not self._keyboard_connected)
        frame_title = self.ssid or self.title or "CLAVE WI-FI"
        badge = get_layout_badge(self._active_layout)

        inner_x1, _, inner_x2, _ = draw_instrument_frame(
            draw,
            title=frame_title,
            icon_glyph="warn" if has_alert else None,
            right_text=badge,
            font=self.small_font,
            width=width,
            height=height,
        )
        self._draw_status_line(draw, inner_x1, inner_x2)
        self._draw_password_field(draw, inner_x1, inner_x2)

    def _draw_status_line(self, draw: ImageDraw.ImageDraw, inner_x1: int, inner_x2: int) -> None:
        """Fila superior despejada: error/aviso o visibilidad (Oculta/Visible) + contador."""
        y_top, y_bottom = 15, 24
        inner_w = max(16, inner_x2 - inner_x1 + 1)

        if self.error_message:
            err_txt = fit_text_px(draw, self.error_message, self.font, inner_w)
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

        eye_glyph = "eye_open" if self.is_visible else "eye_closed"
        gw = draw_mini_glyph(draw, eye_glyph, x=inner_x1, y=17, fill="white")
        mode_txt = "Visible" if self.is_visible else "Oculta"
        counter_txt = f"{len(self.password)}/{self.max_length}"
        counter_w = measure_text_px(draw, counter_txt, self.small_font)
        counter_x = max(inner_x1 + gw + 28, inner_x2 - counter_w + 1)

        mode_max_px = max(12, counter_x - (inner_x1 + gw + 3) - 4)
        fitted_mode = fit_text_px(draw, mode_txt, self.small_font, mode_max_px)
        if fitted_mode:
            draw_text_vcenter(
                draw,
                fitted_mode,
                x=inner_x1 + gw + 3,
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

    def _draw_password_field(self, draw: ImageDraw.ImageDraw, inner_x1: int, inner_x2: int) -> None:
        """Campo compacto de 18 px de alto: puntos, texto visible o guía contextual."""
        box = (inner_x1, PW_BOX[1], inner_x2, PW_BOX[3])
        draw.rectangle(box, outline="white", fill="black")

        display_pw = sanitize_oled_text(self.password)
        if not display_pw:
            self._draw_empty_prompt(draw, box)
        elif self.is_visible:
            self._draw_visible_password(draw, display_pw, box)
        else:
            self._draw_masked_password(draw, len(display_pw), box)

    def _draw_empty_prompt(self, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int]) -> None:
        """Texto guía limpio dentro del campo cuando aún no hay caracteres."""
        x1, y1, x2, y2 = box
        if self.usb_gadget_active:
            prompt = "Cambie a USB normal"
        elif not self._keyboard_connected:
            prompt = "Esperando teclado..."
        else:
            prompt = "Ingrese su clave..."

        max_px = max(12, (x2 - x1) - 10)
        fitted = fit_text_px(draw, prompt, self.small_font, max_px)
        draw_text_vcenter(draw, fitted, x=x1 + 4, y_top=y1 + 2, y_bottom=y2 - 2, font=self.small_font, fill="white")
        if self.cursor_visible and self._keyboard_connected and not self.usb_gadget_active:
            cursor_x = min(x1 + 4 + measure_text_px(draw, fitted, self.small_font) + 2, x2 - 3)
            draw.rectangle((cursor_x, y1 + 4, cursor_x + 1, y2 - 4), fill="white")

    def _draw_visible_password(
        self,
        draw: ImageDraw.ImageDraw,
        display_pw: str,
        box: Tuple[int, int, int, int],
    ) -> None:
        """Contraseña en texto claro con ventana deslizante y cursor."""
        x1, y1, x2, y2 = box
        cursor_char = "_" if self.cursor_visible else " "
        max_px = max(16, (x2 - x1) - 8)
        visible_pw, scrolled = self._fit_tail_window(draw, display_pw, cursor_char, max_px=max_px)
        start_x = x1 + 4
        if scrolled:
            draw_text_vcenter(draw, "<", x=start_x, y_top=y1 + 2, y_bottom=y2 - 2, font=self.font)
            start_x += 8
        draw_text_vcenter(
            draw,
            visible_pw + cursor_char,
            x=start_x,
            y_top=y1 + 2,
            y_bottom=y2 - 2,
            font=self.font,
        )

    def _draw_masked_password(
        self,
        draw: ImageDraw.ImageDraw,
        count: int,
        box: Tuple[int, int, int, int],
    ) -> None:
        """Contraseña oculta como puntos redondos; nunca dibuja caracteres en claro."""
        x1, y1, x2, y2 = box
        dy1 = (y1 + y2) // 2 - (DOT_SIZE // 2)
        dy2 = dy1 + DOT_SIZE - 1
        inner_right = x2 - 4

        start_x = x1 + 5
        span = (count - 1) * DOT_SPACING + DOT_SIZE
        if start_x + span > inner_right:
            draw_text_vcenter(draw, "<", x=start_x, y_top=y1 + 2, y_bottom=y2 - 2, font=self.font)
            start_x += 8

        max_dots = max(1, (inner_right - start_x - DOT_SIZE) // DOT_SPACING + 1)
        shown = min(count, max_dots)
        last_x = start_x
        for i in range(shown):
            last_x = start_x + i * DOT_SPACING
            draw.ellipse((last_x, dy1, last_x + DOT_SIZE - 1, dy2), fill="white")

        if self.cursor_visible:
            cursor_x = min(last_x + DOT_SIZE + 2, x2 - 3)
            draw.rectangle((cursor_x, y1 + 4, cursor_x + 1, y2 - 4), fill="white")
