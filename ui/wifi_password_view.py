"""
REI - USB keyboard Wi-Fi password entry view.

Replaces the on-screen keyboard for secured Wi-Fi networks. The user connects a
physical USB keyboard, types the password and presses ENTER / KEY1 / joystick
PRESS to connect. KEY2 toggles between the masked and visible password.

The view never performs blocking I/O: keyboard availability is polled through a
cheap callback and rendering only uses the 128x64 PIL canvas.
"""

import logging
from typing import Callable, Optional

from PIL import ImageDraw

from .display import BaseView, ViewAction, ViewActionType
from .input_handler import InputEvent
from .text import to_oled

logger = logging.getLogger("REI.UI.WiFiPassword")

MAX_PASSWORD_LENGTH = 63


class WiFiPasswordView(BaseView):
    """
    Minimal Wi-Fi password entry screen for a physical USB keyboard.

    Controls:
    - Alphanumeric keys: append characters.
    - Backspace / Delete: remove the last character.
    - ENTER, KEY1 or joystick PRESS: validate and connect.
    - KEY2: show/hide the password.
    - KEY3, ESCAPE or BACK: cancel and return to the network list.
    """

    accepts_text_input = True

    def __init__(
        self,
        ssid: str,
        on_submit: Optional[Callable[[str, str], None]] = None,
        security: str = "WPA2-PSK",
        keyboard_status: Optional[Callable[[], bool]] = None,
        usb_gadget_active: bool = False,
        max_length: int = MAX_PASSWORD_LENGTH,
        notice: str = "",
        layout_name: str = "",
    ):
        super().__init__(title=f"CLAVE: {self._short_ssid(ssid)}")
        self.ssid = ssid
        self.on_submit = on_submit
        self.security = (security or "").upper()
        self.keyboard_status = keyboard_status
        self.usb_gadget_active = bool(usb_gadget_active)
        self.max_length = max_length
        self.notice = to_oled(notice)[:19]
        self.layout_name = (layout_name or "").strip().lower()

        self.password: str = ""
        self.is_visible: bool = False
        self.error_message: str = ""

        # Cursor blink state (30 FPS)
        self.cursor_visible: bool = True
        self._cursor_tick: int = 0

        self._keyboard_connected: bool = True
        self._submitted: bool = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _short_ssid(ssid: str) -> str:
        """Returns an uppercase SSID that fits the OLED header."""
        clean = (ssid or "RED").strip().upper()
        if len(clean) > 13:
            clean = clean[:12] + "~"
        return clean

    @property
    def min_length(self) -> int:
        """Minimum accepted password length for the detected security type."""
        if "WPA" in self.security:
            return 8
        if "WEP" in self.security:
            return 5
        return 1

    def set_text(self, text: str) -> None:
        """Sets the password directly (used by tests and future flows)."""
        self.password = (text or "")[: self.max_length]
        self.error_message = ""

    def _append_char(self, char: Optional[str]) -> None:
        """Appends one printable character, enforcing the maximum length."""
        if not char:
            return
        if len(char) != 1 or not char.isprintable() or char in ("\r", "\n", "\t"):
            return
        if len(self.password) >= self.max_length:
            self.error_message = "MAXIMO 63 CARACT."
            return
        self.password += char
        self.error_message = ""

    def _backspace(self) -> None:
        """Removes the last password character."""
        if self.password:
            self.password = self.password[:-1]
        self.error_message = ""

    def _toggle_visibility(self) -> None:
        """Shows or hides the password."""
        self.is_visible = not self.is_visible

    def _validate(self) -> tuple:
        """Returns (is_valid, error_message)."""
        if not self.password:
            return False, "INGRESE LA CLAVE"
        if len(self.password) > self.max_length:
            return False, "MAXIMO 63 CARACT."
        minimum = self.min_length
        if len(self.password) < minimum:
            return False, f"MINIMO {minimum} CARACTERES"
        return True, ""

    def _submit(self) -> ViewAction:
        """Validates the password and invokes the connection callback."""
        if self._submitted:
            return ViewAction(ViewActionType.NONE)

        valid, message = self._validate()
        if not valid:
            self.error_message = message
            return ViewAction(ViewActionType.NONE)

        self._submitted = True
        try:
            if self.on_submit:
                self.on_submit(self.ssid, self.password)
        except Exception as exc:
            logger.exception(f"Error submitting Wi-Fi password: {exc}")
            self._submitted = False
            self.error_message = "ERROR INTERNO"
        return ViewAction(ViewActionType.NONE)

    # ------------------------------------------------------------------
    # BaseView contract
    # ------------------------------------------------------------------
    def update(self) -> None:
        """Advances cursor animation and refreshes keyboard availability."""
        self._cursor_tick = (self._cursor_tick + 1) % 30
        self.cursor_visible = self._cursor_tick < 15

        if self.keyboard_status is not None:
            try:
                self._keyboard_connected = bool(self.keyboard_status())
            except Exception as exc:
                logger.debug(f"Keyboard status callback failed: {exc}")
                self._keyboard_connected = True

    def handle_input(self, event: InputEvent, char: Optional[str] = None, **kwargs) -> ViewAction:
        """Processes USB keyboard and HAT button events."""
        if event == InputEvent.CHAR:
            self._append_char(char)
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.BACKSPACE:
            self._backspace()
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.KEY2:
            self._toggle_visibility()
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.ENTER, InputEvent.KEY1, InputEvent.PRESS):
            return self._submit()

        if event in (InputEvent.KEY3, InputEvent.ESCAPE, InputEvent.BACK):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _draw_error_banner(self, draw: ImageDraw.ImageDraw, text: str) -> None:
        """Draws a high-contrast inverted error banner."""
        draw.rectangle((3, 33, 124, 45), fill="white")
        label = to_oled(text)[:19]
        width = self.get_text_width(draw, label)
        x = max(4, (128 - width) // 2)
        self.draw_text(draw, (x, 34), label, fill="black")

    def _draw_no_keyboard(self, draw: ImageDraw.ImageDraw, width: int) -> None:
        """Renders the guidance screen when no typing input is available."""
        if self.password:
            self.draw_centered_text(draw, "TECLADO DESCONECTADO", y=23, screen_width=width)
            if self.usb_gadget_active:
                self.draw_centered_text(draw, "MODO HID ACTIVO", y=35, screen_width=width)
                self.draw_centered_text(draw, "USE MODO NORMAL", y=47, screen_width=width)
            else:
                self.draw_centered_text(draw, "RECONECTE TECLADO", y=35, screen_width=width)
                self.draw_centered_text(draw, "K3=SALIR", y=47, screen_width=width)
            return

        self.draw_centered_text(draw, "CONECTE TECLADO USB", y=23, screen_width=width)
        if self.usb_gadget_active:
            self.draw_centered_text(draw, "MODO HID ACTIVO", y=35, screen_width=width)
            self.draw_centered_text(draw, "USE MODO NORMAL", y=47, screen_width=width)
        else:
            self.draw_centered_text(draw, "INGRESE LA CLAVE", y=35, screen_width=width)
            self.draw_centered_text(draw, "K3=SALIR", y=47, screen_width=width)

    def _draw_password_field(self, draw: ImageDraw.ImageDraw) -> None:
        """Draws the password box with scrolling text and a blinking cursor."""
        draw.rectangle((5, 18, 122, 31), outline="white", fill="black")

        if self.is_visible:
            # The font cannot render non-ASCII glyphs: show them as '?' so the
            # user notices the character is special. The stored password keeps
            # the real characters.
            display_text = "".join(
                ch if ch.isascii() else "?" for ch in self.password
            )
        else:
            display_text = "*" * len(self.password)
        # Inner width is 114 px; leave one character for the cursor.
        max_visible = 18
        if len(display_text) > max_visible:
            display_text = display_text[-max_visible:]

        cursor = "_" if self.cursor_visible else " "
        self.draw_text(draw, (8, 20), display_text + cursor)

    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        """Renders the Wi-Fi password screen into the 128x64 1-bit canvas."""
        self.draw_perimeter_border(draw)

        # Header
        header = self.title.strip().upper()[:20]
        self.draw_centered_text(draw, header, y=4, screen_width=width)
        draw.line((4, 15, 123, 15), fill="white")

        if not self._keyboard_connected:
            self._draw_no_keyboard(draw, width)
            return

        self._draw_password_field(draw)

        if self.error_message:
            self._draw_error_banner(draw, self.error_message)
        elif self.notice:
            self._draw_error_banner(draw, self.notice)
        elif not self.password:
            badge = f"TECLADO: {self.layout_name.upper()[:11]}" if self.layout_name else "INGRESE LA CLAVE"
            self.draw_centered_text(draw, badge, y=35, screen_width=width)
        elif len(self.password) < self.min_length:
            self.draw_centered_text(
                draw,
                f"MINIMO {self.min_length} CARACTERES",
                y=35,
                screen_width=width,
            )
        else:
            self.draw_centered_text(draw, "ENTER=CONECTAR", y=35, screen_width=width)

        if self.password:
            controls = "K2:OCULTAR K3=SALIR" if self.is_visible else "K2:VER K3=SALIR"
            self.draw_centered_text(draw, controls, y=47, screen_width=width)
        else:
            self.draw_centered_text(draw, "K3=SALIR", y=47, screen_width=width)
