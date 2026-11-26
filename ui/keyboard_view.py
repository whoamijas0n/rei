"""
REI - Virtual Keyboard View for 1.3" OLED (128x64 px)
Teclado virtual matricial operado con el joystick de 5 vías y botones físicos (KEY1, KEY2, KEY3).
Alineado con el sistema tipográfico y de marco de ui/theme.py con ajuste exacto en píxeles.
"""

from dataclasses import dataclass
from enum import Enum, auto
import logging
from typing import Any, Callable, List, Optional, Tuple

from PIL import ImageDraw

from core.parser import sanitize_oled_text
from .display import BaseView, ViewAction, ViewActionType
from .input_handler import InputEvent
from .theme import draw_frame, draw_inverted_badge, draw_text_vcenter, measure_text_px

logger = logging.getLogger("REI.UI.Keyboard")


class KeyboardLayer(Enum):
    """Capas de caracteres disponibles para el teclado virtual."""

    LOWER = auto()
    UPPER = auto()
    NUM = auto()
    SYM = auto()


@dataclass
class KeyDef:
    """Definición de una tecla funcional o de carácter en la matriz."""

    label: str
    action_type: str  # 'char', 'layer', 'space', 'backspace', 'submit'
    value: str = ""
    col_span: Tuple[int, int] = (0, 0)  # (start_col, end_col) inclusivo


class VirtualKeyboardInputView(BaseView):
    """
    Teclado virtual para pantalla OLED 128x64.
    Controles preservados:
    - Joystick UP / DOWN / LEFT / RIGHT: mueve el foco en la matriz 4x10.
    - Joystick PRESS: escribe carácter o ejecuta tecla funcional.
    - KEY1 / ENTER: confirmar y enviar.
    - KEY2 / BACKSPACE: borrar último carácter.
    - KEY3 / ESCAPE / BACK: cancelar y regresar.
    """

    accepts_text_input: bool = True

    def __init__(
        self,
        ssid: str,
        title: Optional[str] = None,
        on_submit: Optional[Callable[[str, str], None]] = None,
        masked: bool = False,
        max_length: int = 63,
        initial_text: str = "",
    ):
        header_title = title or f"CLAVE: {ssid}"
        super().__init__(title=header_title)
        self.ssid = ssid
        self.on_submit = on_submit
        self.is_masked = masked
        self.max_length = max_length
        self.input_text: str = initial_text[:max_length]

        self.cursor_visible: bool = True
        self._cursor_tick: int = 0

        self.current_layer: KeyboardLayer = KeyboardLayer.LOWER
        self.cursor_row: int = 0
        self.cursor_col: int = 0

        self._init_keyboard_layouts()

    def _init_keyboard_layouts(self) -> None:
        """Inicializa las matrices de teclas para las 4 capas (4 filas x 10 columnas)."""
        self._layout_lower: List[List[Any]] = [
            ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
            ["a", "s", "d", "f", "g", "h", "j", "k", "l", "@"],
            ["z", "x", "c", "v", "b", "n", "m", ".", "_", "-"],
            [
                KeyDef(label="ABC", action_type="layer", value="UPPER", col_span=(0, 1)),
                KeyDef(label="123", action_type="layer", value="NUM", col_span=(2, 3)),
                KeyDef(label="SYM", action_type="layer", value="SYM", col_span=(4, 5)),
                KeyDef(label="SP", action_type="space", value=" ", col_span=(6, 6)),
                KeyDef(label="DEL", action_type="backspace", col_span=(7, 8)),
                KeyDef(label="OK", action_type="submit", col_span=(9, 9)),
            ],
        ]

        self._layout_upper: List[List[Any]] = [
            ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
            ["A", "S", "D", "F", "G", "H", "J", "K", "L", "@"],
            ["Z", "X", "C", "V", "B", "N", "M", ".", "_", "-"],
            [
                KeyDef(label="abc", action_type="layer", value="LOWER", col_span=(0, 1)),
                KeyDef(label="123", action_type="layer", value="NUM", col_span=(2, 3)),
                KeyDef(label="SYM", action_type="layer", value="SYM", col_span=(4, 5)),
                KeyDef(label="SP", action_type="space", value=" ", col_span=(6, 6)),
                KeyDef(label="DEL", action_type="backspace", col_span=(7, 8)),
                KeyDef(label="OK", action_type="submit", col_span=(9, 9)),
            ],
        ]

        self._layout_num: List[List[Any]] = [
            ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
            ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")"],
            ["-", "_", "=", "+", "[", "]", "{", "}", ";", ":"],
            [
                KeyDef(label="abc", action_type="layer", value="LOWER", col_span=(0, 1)),
                KeyDef(label="ABC", action_type="layer", value="UPPER", col_span=(2, 3)),
                KeyDef(label="SYM", action_type="layer", value="SYM", col_span=(4, 5)),
                KeyDef(label="SP", action_type="space", value=" ", col_span=(6, 6)),
                KeyDef(label="DEL", action_type="backspace", col_span=(7, 8)),
                KeyDef(label="OK", action_type="submit", col_span=(9, 9)),
            ],
        ]

        self._layout_sym: List[List[Any]] = [
            ["~", "`", "|", "\\", "/", "?", "<", ">", ",", "."],
            ["'", '"', "$", "%", "^", "&", "*", "+", "=", "#"],
            ["!", "@", ":", ";", "(", ")", "[", "]", "{", "}"],
            [
                KeyDef(label="abc", action_type="layer", value="LOWER", col_span=(0, 1)),
                KeyDef(label="ABC", action_type="layer", value="UPPER", col_span=(2, 3)),
                KeyDef(label="123", action_type="layer", value="NUM", col_span=(4, 5)),
                KeyDef(label="SP", action_type="space", value=" ", col_span=(6, 6)),
                KeyDef(label="DEL", action_type="backspace", col_span=(7, 8)),
                KeyDef(label="OK", action_type="submit", col_span=(9, 9)),
            ],
        ]

    def _get_current_layout(self) -> List[List[Any]]:
        """Retorna la matriz activa según la capa seleccionada."""
        if self.current_layer == KeyboardLayer.LOWER:
            return self._layout_lower
        if self.current_layer == KeyboardLayer.UPPER:
            return self._layout_upper
        if self.current_layer == KeyboardLayer.NUM:
            return self._layout_num
        return self._layout_sym

    def _get_layer_badge(self) -> str:
        """Retorna la etiqueta de la capa activa."""
        if self.current_layer == KeyboardLayer.LOWER:
            return "abc"
        if self.current_layer == KeyboardLayer.UPPER:
            return "ABC"
        if self.current_layer == KeyboardLayer.NUM:
            return "123"
        return "SYM"

    def set_text(self, text: str) -> None:
        """Establece el texto de entrada directamente."""
        self.input_text = text[: self.max_length]

    def update(self) -> None:
        """Actualiza el parpadeo del cursor a 30 FPS."""
        self._cursor_tick = (self._cursor_tick + 1) % 30
        self.cursor_visible = self._cursor_tick < 15

    def _fit_tail_px(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        cursor_char: str,
        max_px: int,
    ) -> str:
        """Ajusta la cola visible del texto al ancho real en píxeles de la caja."""
        if measure_text_px(draw, text + cursor_char, self.small_font) <= max_px:
            return text + cursor_char
        visible = text
        while visible and measure_text_px(draw, "<" + visible + cursor_char, self.small_font) > max_px:
            visible = visible[1:]
        return "<" + visible + cursor_char

    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        """Renderiza el teclado virtual con marco unificado y ajuste exacto en píxeles."""
        draw_frame(draw, width, height)

        # Cabecera compacta: caja de entrada izquierda + badge invertido de capa derecha
        badge_left = draw_inverted_badge(
            draw,
            self._get_layer_badge(),
            right_x=width - 4,
            y_top=3,
            y_bottom=12,
            font=self.small_font,
            pad_x=2,
        )
        box_right = max(40, badge_left - 3)
        draw.rectangle((4, 3, box_right, 12), outline="white", fill="black")

        safe_input = sanitize_oled_text(self.input_text)
        display_str = ("*" * len(safe_input)) if self.is_masked else safe_input
        cursor_char = "_" if self.cursor_visible else " "
        visible_text = self._fit_tail_px(draw, display_str, cursor_char, max_px=(box_right - 4) - 4)
        draw_text_vcenter(draw, visible_text, x=6, y_top=3, y_bottom=12, font=self.small_font, fill="white")

        # Divisor unificado
        draw.line((2, 14, width - 3, 14), fill="white")

        # Matriz de teclas (4 filas x 10 cols)
        layout = self._get_current_layout()
        row_y_coords = [17, 28, 39, 50]
        cell_w = 12
        grid_x0 = 3

        for r_idx, row in enumerate(layout):
            row_y = row_y_coords[r_idx]

            if r_idx < 3:
                for c_idx, char in enumerate(row):
                    col_x = grid_x0 + (c_idx * cell_w)
                    is_focused = self.cursor_row == r_idx and self.cursor_col == c_idx
                    ch_str = str(char)
                    ch_w = measure_text_px(draw, ch_str, self.small_font)
                    tx = col_x + max(1, (cell_w - ch_w) // 2)

                    if is_focused:
                        draw.rectangle((col_x, row_y, col_x + cell_w - 1, row_y + 9), fill="white")
                        draw_text_vcenter(draw, ch_str, x=tx, y_top=row_y, y_bottom=row_y + 9, font=self.small_font, fill="black")
                    else:
                        draw_text_vcenter(draw, ch_str, x=tx, y_top=row_y, y_bottom=row_y + 9, font=self.small_font, fill="white")
            else:
                for key_def in row:
                    start_col, end_col = key_def.col_span
                    x1 = grid_x0 + (start_col * cell_w)
                    span_count = end_col - start_col + 1
                    x2 = min(width - 3, x1 + (span_count * cell_w) - 1)
                    is_focused = self.cursor_row == 3 and start_col <= self.cursor_col <= end_col

                    label_w = measure_text_px(draw, key_def.label, self.small_font)
                    text_x = x1 + max(0, ((x2 - x1 + 1) - label_w) // 2)

                    if is_focused:
                        draw.rectangle((x1, row_y, x2, row_y + 9), fill="white")
                        draw_text_vcenter(draw, key_def.label, x=text_x, y_top=row_y, y_bottom=row_y + 9, font=self.small_font, fill="black")
                    else:
                        draw_text_vcenter(draw, key_def.label, x=text_x, y_top=row_y, y_bottom=row_y + 9, font=self.small_font, fill="white")

    def _trigger_key_action(self) -> ViewAction:
        """Ejecuta la acción asociada a la tecla actualmente enfocada."""
        layout = self._get_current_layout()

        if self.cursor_row < 3:
            char = layout[self.cursor_row][self.cursor_col]
            if len(self.input_text) < self.max_length:
                self.input_text += str(char)
            return ViewAction(ViewActionType.NONE)

        row = layout[3]
        selected_key: Optional[KeyDef] = None
        for key_def in row:
            if key_def.col_span[0] <= self.cursor_col <= key_def.col_span[1]:
                selected_key = key_def
                break

        if not selected_key:
            return ViewAction(ViewActionType.NONE)

        if selected_key.action_type == "char":
            if len(self.input_text) < self.max_length:
                self.input_text += selected_key.value
        elif selected_key.action_type == "space":
            if len(self.input_text) < self.max_length:
                self.input_text += " "
        elif selected_key.action_type == "backspace":
            if self.input_text:
                self.input_text = self.input_text[:-1]
        elif selected_key.action_type == "layer":
            layer_map = {
                "LOWER": KeyboardLayer.LOWER,
                "UPPER": KeyboardLayer.UPPER,
                "NUM": KeyboardLayer.NUM,
                "SYM": KeyboardLayer.SYM,
            }
            self.current_layer = layer_map.get(selected_key.value, self.current_layer)
        elif selected_key.action_type == "submit":
            return self._submit_password()

        return ViewAction(ViewActionType.NONE)

    def _submit_password(self) -> ViewAction:
        """Invoca el callback de envío o dispara la tarea de conexión."""
        if self.on_submit:
            self.on_submit(self.ssid, self.input_text)
            return ViewAction(ViewActionType.NONE)
        return ViewAction(
            ViewActionType.EXECUTE_TASK,
            task_id="sys_wifi_connect",
            payload={"ssid": self.ssid, "password": self.input_text},
        )

    def handle_input(self, event: InputEvent, char: Optional[str] = None, **kwargs: Any) -> ViewAction:
        """Procesa eventos del joystick, botones físicos y entrada directa."""
        if event == InputEvent.UP:
            self.cursor_row = (self.cursor_row - 1 + 4) % 4
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.DOWN:
            self.cursor_row = (self.cursor_row + 1) % 4
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.LEFT:
            self.cursor_col = (self.cursor_col - 1 + 10) % 10
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.RIGHT:
            self.cursor_col = (self.cursor_col + 1) % 10
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.PRESS:
            return self._trigger_key_action()

        if event in (InputEvent.KEY1, InputEvent.ENTER):
            return self._submit_password()

        if event in (InputEvent.KEY2, InputEvent.BACKSPACE):
            if self.input_text:
                self.input_text = self.input_text[:-1]
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.KEY3, InputEvent.ESCAPE, InputEvent.BACK):
            return ViewAction(ViewActionType.POP_VIEW)

        if event == InputEvent.CHAR:
            if char and len(self.input_text) < self.max_length:
                self.input_text += char
            return ViewAction(ViewActionType.NONE)

        return ViewAction(ViewActionType.NONE)


# Alias de retrocompatibilidad
KeyboardInputView = VirtualKeyboardInputView
