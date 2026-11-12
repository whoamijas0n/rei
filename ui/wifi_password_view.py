"""
REI - Vista OLED para ingreso de contraseña Wi-Fi mediante teclado físico USB.

Sustituye al teclado virtual matricial en pantalla en los flujos Wi-Fi.
El usuario conecta un teclado físico USB (o receptor inalámbrico 2.4 GHz),
escribe la contraseña con soporte LATAM/ES/US, puede alternar entre mostrarla
u ocultarla (KEY2 / Tab / F2) y confirmar con ENTER / KEY1 / PRESS sin bloquear
el bucle de interfaz de 30 FPS.
"""

import logging
from typing import Callable, Optional, Tuple

from PIL import ImageDraw

from core.parser import sanitize_oled_text, shorten_oled_label
from core.wifi import WiFiErrorCode, validate_wifi_password
from .display import BaseView, ViewAction, ViewActionType
from .input_handler import InputEvent
from .keyboard_layouts import get_layout_badge

logger = logging.getLogger("REI.UI.WiFiPassword")

MAX_PASSWORD_LENGTH = 63

# Geometría del rediseño (128x64 px, 1-bit). Caja de contraseña y ritmo de
# los puntos enmascarados; mantener PW_BOX consistente con _fit_tail_window.
PW_BOX = (5, 17, 122, 43)
DOT_SPACING = 6
DOT_SIZE = 4


class WiFiPasswordView(BaseView):
    """
    Pantalla de contraseña Wi-Fi rediseñada (128x64 px, 1-bit).

    Layout (menos cargado y más intuitivo que la versión anterior):
    - Cabecera: título (prefijo + SSID) truncado a la izquierda; badge de
      distribución (LA/ES/US) y punto de estado del teclado a la derecha
      (sólido = conectado, hueco = ausente). Sin pills ni separadores.
    - Cuadro central: la contraseña como puntos redondos (oculta) o texto
      claro (visible) con cursor parpadeante; los errores de validación se
      muestran invertidos dentro del propio cuadro; si está vacía, un texto
      guía ("Ingrese su clave...") ocupa su lugar.
    - Banda inferior: contador "NN/63" a la izquierda y hints compactos con
      glyph a la derecha: [ojo] K2 (ver/ocultar), [check] ENT (conectar),
      [flecha] K3 (cancelar).

    Controles (sin cambios respecto a la versión previa):
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
            except Exception:
                pass

    def handle_input(self, event: InputEvent, char: Optional[str] = None, **kwargs) -> ViewAction:
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
        clean = sanitize_oled_text(text)
        if self.get_text_width(draw, clean) <= max_px:
            return clean
        if max_px <= 10:
            return ""
        trimmed = clean
        while trimmed and self.get_text_width(draw, trimmed + "..") > max_px:
            trimmed = trimmed[:-1]
        return (trimmed + "..") if trimmed else ""

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
        if self.get_text_width(draw, display_text + cursor_char) <= max_px:
            return display_text, False

        # Reservar espacio para marcador '<' a la izquierda
        inner_px = max(12, max_px - 8)
        visible = display_text
        while visible and self.get_text_width(draw, visible + cursor_char) > inner_px:
            visible = visible[1:]
        return visible, True

    # ------------------------------------------------------------------ #
    # Render (rediseño)
    # ------------------------------------------------------------------ #

    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        """Renderiza la interfaz Wi-Fi rediseñada en el canvas 128x64 1-bit."""
        self.draw_perimeter_border(draw)
        self._draw_header(draw)
        self._draw_password_field(draw, width)
        self._draw_bottom_band(draw)

    def _draw_header(self, draw: ImageDraw.ImageDraw) -> None:
        """Cabecera ligera: título a la izquierda, badge + punto de teclado a la derecha."""
        # Punto de estado del teclado (5x5): sólido = conectado, hueco = ausente
        if self._keyboard_connected:
            draw.rectangle((117, 6, 121, 10), fill="white")
        else:
            draw.rectangle((117, 6, 121, 10), outline="white")

        # Badge de distribución del teclado (LA / ES / US)
        badge = get_layout_badge(self._active_layout)
        badge_w = self.get_text_width(draw, badge)
        badge_x = 117 - badge_w - 3
        draw.text((badge_x, 2), badge, font=self.font, fill="white")

        # Título (prefijo + SSID) truncado al ancho disponible
        title = sanitize_oled_text(self.title or f"RED: {self.ssid}").strip().upper()
        title_label = self._fit_text_to_width(draw, title, max(24, badge_x - 8))
        draw.text((5, 2), title_label, font=self.font, fill="white")

    def _draw_password_field(self, draw: ImageDraw.ImageDraw, width: int) -> None:
        """Cuadro central: puntos/texto/cursor, error invertido o texto guía."""
        x1, y1, x2, y2 = PW_BOX
        draw.rectangle(PW_BOX, outline="white", fill="black")

        # Errores de validación: barra invertida dentro del propio cuadro
        if self.error_message:
            draw.rectangle((x1 + 2, y1 + 2, x2 - 2, y2 - 2), fill="white")
            err_txt = shorten_oled_label(self.error_message, 19)
            self.draw_centered_text(draw, err_txt, y=24, screen_width=width, fill="black")
            return

        display_pw = sanitize_oled_text(self.password)
        if not display_pw:
            self._draw_empty_prompt(draw)
        elif self.is_visible:
            self._draw_visible_password(draw, display_pw)
        else:
            self._draw_masked_password(draw, len(display_pw))

    def _draw_empty_prompt(self, draw: ImageDraw.ImageDraw) -> None:
        """Texto guía dentro del cuadro cuando aún no hay caracteres."""
        if self.usb_gadget_active:
            prompt = "USB en modo HID..."
        elif not self._keyboard_connected:
            prompt = "Conecte teclado..."
        else:
            prompt = "Ingrese su clave..."
        # Recorte defensivo: el prompt nunca debe invadir el borde del cuadro
        prompt = self._fit_text_to_width(draw, prompt, 103)
        draw.text((9, 23), prompt, font=self.font, fill="white")
        if self.cursor_visible:
            cursor_x = min(9 + self.get_text_width(draw, prompt) + 3, 116)
            draw.rectangle((cursor_x, 24, cursor_x + 1, 34), fill="white")

    def _draw_visible_password(self, draw: ImageDraw.ImageDraw, display_pw: str) -> None:
        """Contraseña en texto claro con ventana deslizante y cursor."""
        cursor_char = "_" if self.cursor_visible else " "
        visible_pw, scrolled = self._fit_tail_window(draw, display_pw, cursor_char, max_px=104)
        start_x = 9
        if scrolled:
            draw.text((start_x, 23), "<", font=self.font, fill="white")
            start_x += 8
        draw.text((start_x, 23), visible_pw + cursor_char, font=self.font, fill="white")

    def _draw_masked_password(self, draw: ImageDraw.ImageDraw, count: int) -> None:
        """
        Contraseña oculta como puntos redondos (estilo cajita moderna).
        Muestra la cola de la clave; los caracteres reales nunca se dibujan.
        """
        dy1 = (PW_BOX[1] + PW_BOX[3]) // 2 - 2
        dy2 = dy1 + DOT_SIZE - 1
        inner_right = PW_BOX[2] - 3

        start_x = 10
        span = (count - 1) * DOT_SPACING + DOT_SIZE
        if start_x + span > inner_right:
            draw.text((start_x, 23), "<", font=self.font, fill="white")
            start_x += 8

        max_dots = (inner_right - start_x - DOT_SIZE) // DOT_SPACING + 1
        shown = min(count, max_dots)
        last_x = start_x
        for i in range(shown):
            last_x = start_x + i * DOT_SPACING
            draw.ellipse((last_x, dy1, last_x + DOT_SIZE - 1, dy2), fill="white")

        # Cursor parpadeante tras el último punto visible (el final de la clave)
        if self.cursor_visible:
            cursor_x = last_x + DOT_SIZE + 2
            draw.rectangle((cursor_x, dy1 - 4, cursor_x + 1, dy2 + 4), fill="white")

    def _draw_bottom_band(self, draw: ImageDraw.ImageDraw) -> None:
        """Banda inferior: contador a la izquierda, hints con glyph a la derecha."""
        if self.password:
            count_txt = f"{len(self.password):02d}/{self.max_length}"
            draw.text((5, 48), count_txt, font=self.font, fill="white")

        # Hints compactos: [ojo] K2 · [check] ENT · [flecha] K3
        self._draw_eye(draw, 42, 50, hidden=not self.is_visible)
        draw.text((53, 48), "K2", font=self.font, fill="white")
        self._draw_check(draw, 70, 51)
        draw.text((78, 48), "ENT", font=self.font, fill="white")
        self._draw_back_arrow(draw, 100, 52)
        draw.text((108, 48), "K3", font=self.font, fill="white")

    @staticmethod
    def _draw_eye(draw: ImageDraw.ImageDraw, x: int, y: int, hidden: bool) -> None:
        """Glyph de ojo 9x6: pupila sólida = clave oculta, hueca = visible."""
        draw.ellipse((x, y, x + 8, y + 5), outline="white")
        if hidden:
            draw.rectangle((x + 3, y + 2, x + 4, y + 3), fill="white")

    @staticmethod
    def _draw_check(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Glyph de confirmación (checkmark de 7x5 px)."""
        draw.line((x, y + 2, x + 2, y + 4), fill="white")
        draw.line((x + 2, y + 4, x + 6, y), fill="white")

    @staticmethod
    def _draw_back_arrow(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Glyph de flecha hacia la izquierda (6x5 px)."""
        draw.line((x + 5, y, x + 1, y + 2), fill="white")
        draw.line((x + 1, y + 2, x + 5, y + 4), fill="white")
