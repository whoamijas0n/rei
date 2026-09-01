"""
REI - Virtual Keyboard View for 1.3" OLED (128x64 px)
Interactive on-screen keyboard operated via 5-way joystick and hardware buttons (KEY1, KEY2, KEY3).
Provides multi-layer character sets (Lowercase, Uppercase, Numbers, Symbols), high-contrast
inverted active cell highlighting, text box scrolling, and non-blocking navigation.
Adheres strictly to AGENT.md minimalist layout standards (Zero Useless Screen Legends).
"""

from dataclasses import dataclass
from enum import Enum, auto
import logging
from typing import Callable, Dict, List, Optional, Tuple, Any

from PIL import ImageDraw, ImageFont

from .display import BaseView, ViewAction, ViewActionType
from .input_handler import InputEvent

logger = logging.getLogger("REI.UI.Keyboard")


class KeyboardLayer(Enum):
    """Available character layers for the virtual keyboard."""
    LOWER = auto()
    UPPER = auto()
    NUM = auto()
    SYM = auto()


@dataclass
class KeyDef:
    """Definition of a functional or character key in the matrix."""
    label: str
    action_type: str  # 'char', 'layer', 'space', 'backspace', 'submit'
    value: str = ""
    col_span: Tuple[int, int] = (0, 0)  # (start_col, end_col) inclusive


class VirtualKeyboardInputView(BaseView):
    """
    Virtual Keyboard View for OLED 128x64 display.
    Allows complete Wi-Fi password entry using only 5-way joystick and physical HAT keys.
    
    Controls (Physical / Hardware):
    - Joystick UP / DOWN / LEFT / RIGHT: Move focus in the 4x10 key matrix.
    - Joystick PRESS: Type selected character or trigger key action (Shift, Layer, Space, Backspace, OK).
    - KEY1: Instant Submit & Connect shortcut.
    - KEY2: Instant Backspace / Delete shortcut.
    - KEY3: Cancel and return to previous view without saving.
    """

    def __init__(
        self,
        ssid: str,
        title: Optional[str] = None,
        on_submit: Optional[Callable[[str, str], None]] = None,
        masked: bool = False,
        max_length: int = 63,
        initial_text: str = ""
    ):
        header_title = title or f"CLAVE: {ssid}"
        super().__init__(title=header_title)
        self.ssid = ssid
        self.on_submit = on_submit
        self.is_masked = masked
        self.max_length = max_length
        self.input_text: str = initial_text[:max_length]
        
        # Cursor & animation state (30 FPS blinking)
        self.cursor_visible: bool = True
        self._cursor_tick: int = 0

        # Layer & Matrix Navigation state
        self.current_layer: KeyboardLayer = KeyboardLayer.LOWER
        self.cursor_row: int = 0  # 0 to 3
        self.cursor_col: int = 0  # 0 to 9

        # Build key layouts for all layers
        self._init_keyboard_layouts()

    def _init_keyboard_layouts(self) -> None:
        """Initializes the key definition grids for all 4 layers (4 rows x 10 cols)."""
        # --- Layer 0: Lowercase (abc) ---
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
            ]
        ]

        # --- Layer 1: Uppercase (ABC) ---
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
            ]
        ]

        # --- Layer 2: Numbers & Basic Symbols (123) ---
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
            ]
        ]

        # --- Layer 3: Extended Symbols (SYM) ---
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
            ]
        ]

    def _get_current_layout(self) -> List[List[Any]]:
        """Returns the active key matrix for current layer."""
        if self.current_layer == KeyboardLayer.LOWER:
            return self._layout_lower
        elif self.current_layer == KeyboardLayer.UPPER:
            return self._layout_upper
        elif self.current_layer == KeyboardLayer.NUM:
            return self._layout_num
        else:
            return self._layout_sym

    def _get_layer_badge(self) -> str:
        """Returns visual badge indicator for active layer."""
        if self.current_layer == KeyboardLayer.LOWER:
            return "abc"
        elif self.current_layer == KeyboardLayer.UPPER:
            return "ABC"
        elif self.current_layer == KeyboardLayer.NUM:
            return "123"
        else:
            return "SYM"

    def set_text(self, text: str) -> None:
        """Sets input text directly."""
        self.input_text = text[:self.max_length]

    def update(self) -> None:
        """Ticks cursor blinking animation at 30 FPS."""
        self._cursor_tick = (self._cursor_tick + 1) % 30
        self.cursor_visible = (self._cursor_tick < 15)

    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        """
        Renders the Virtual Keyboard to the 128x64 1-bit canvas:
        - 1px continuous perimeter border: (1, 1) to (126, 62)
        - Top area (y: 2..14): Text input box with scrolling password, blinking cursor, and layer badge
        - Key matrix (y: 18..59): Spacious 4 rows x 10 cols with high-contrast inverted active cell
        """
        # 1. Borde perimetral continuo
        self.draw_perimeter_border(draw)

        # 2. Línea Superior: Cuadro de texto para la contraseña y badge de capa
        # Cuadro de entrada: x=3..94, y=2..14
        draw.rectangle((3, 2, 94, 14), outline="white", fill="black")
        
        # Badge de capa: x=96..124, y=2..14
        layer_badge = f"[{self._get_layer_badge()}]"
        draw.text((97, 3), layer_badge, font=self.font, fill="white")

        # Texto ingresado con desplazamiento horizontal
        display_str = ("*" * len(self.input_text)) if self.is_masked else self.input_text
        cursor_char = "_" if self.cursor_visible else " "
        
        # Ancho visible dentro de la caja de texto (x=5..92 -> ~14 caracteres @ 6px/char)
        max_visible_chars = 13
        if len(display_str) > max_visible_chars:
            visible_text = display_str[-max_visible_chars:]
        else:
            visible_text = display_str

        # Renderizar texto + cursor dentro de la caja
        draw.text((5, 3), visible_text + cursor_char, font=self.font, fill="white")

        # 3. Línea divisoria superior
        draw.line((2, 15, 125, 15), fill="white")

        # 4. Matriz de Teclas (y: 18..59) - Espaciosa y de alta legibilidad
        layout = self._get_current_layout()
        row_y_coords = [18, 29, 40, 51]
        cell_w = 12

        for r_idx, row in enumerate(layout):
            row_y = row_y_coords[r_idx]

            if r_idx < 3:
                # Filas 0, 1, 2: Caracteres individuales de 1 celda (cols 0..9)
                for c_idx, char in enumerate(row):
                    col_x = 4 + (c_idx * cell_w)
                    is_focused = (self.cursor_row == r_idx and self.cursor_col == c_idx)

                    if is_focused:
                        # Resaltado invertido (Fondo blanco, texto negro)
                        draw.rectangle((col_x, row_y, col_x + cell_w - 1, row_y + 8), fill="white")
                        draw.text((col_x + 3, row_y), str(char), font=self.font, fill="black")
                    else:
                        draw.text((col_x + 3, row_y), str(char), font=self.font, fill="white")

            else:
                # Fila 3: Teclas funcionales con tramos de columna (col_span)
                for key_def in row:
                    start_col, end_col = key_def.col_span
                    x1 = 4 + (start_col * cell_w)
                    span_count = (end_col - start_col + 1)
                    x2 = x1 + (span_count * cell_w) - 1
                    
                    is_focused = (self.cursor_row == 3 and start_col <= self.cursor_col <= end_col)

                    # Centrar texto de la tecla funcional en su celda combinada
                    label_w = self.get_text_width(draw, key_def.label)
                    text_x = x1 + max(0, (span_count * cell_w - label_w) // 2)

                    if is_focused:
                        draw.rectangle((x1, row_y, x2, row_y + 8), fill="white")
                        draw.text((text_x, row_y), key_def.label, font=self.font, fill="black")
                    else:
                        draw.text((text_x, row_y), key_def.label, font=self.font, fill="white")

    def _trigger_key_action(self) -> ViewAction:
        """Executes action for the currently focused key in the matrix."""
        layout = self._get_current_layout()

        if self.cursor_row < 3:
            # Fila de caracteres alfanuméricos / símbolos
            char = layout[self.cursor_row][self.cursor_col]
            if len(self.input_text) < self.max_length:
                self.input_text += str(char)
            return ViewAction(ViewActionType.NONE)

        else:
            # Fila de teclas funcionales
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
                return ViewAction(ViewActionType.NONE)

            elif selected_key.action_type == "space":
                if len(self.input_text) < self.max_length:
                    self.input_text += " "
                return ViewAction(ViewActionType.NONE)

            elif selected_key.action_type == "backspace":
                if len(self.input_text) > 0:
                    self.input_text = self.input_text[:-1]
                return ViewAction(ViewActionType.NONE)

            elif selected_key.action_type == "layer":
                if selected_key.value == "LOWER":
                    self.current_layer = KeyboardLayer.LOWER
                elif selected_key.value == "UPPER":
                    self.current_layer = KeyboardLayer.UPPER
                elif selected_key.value == "NUM":
                    self.current_layer = KeyboardLayer.NUM
                elif selected_key.value == "SYM":
                    self.current_layer = KeyboardLayer.SYM
                return ViewAction(ViewActionType.NONE)

            elif selected_key.action_type == "submit":
                return self._submit_password()

        return ViewAction(ViewActionType.NONE)

    def _submit_password(self) -> ViewAction:
        """Invokes submission callback or returns task execution action."""
        if self.on_submit:
            self.on_submit(self.ssid, self.input_text)
            return ViewAction(ViewActionType.NONE)
        return ViewAction(
            ViewActionType.EXECUTE_TASK,
            task_id="sys_wifi_connect",
            payload={"ssid": self.ssid, "password": self.input_text}
        )

    def handle_input(self, event: InputEvent, char: Optional[str] = None) -> ViewAction:
        """
        Processes hardware input events:
        - Joystick Navigation (UP/DOWN/LEFT/RIGHT)
        - Joystick PRESS -> Types focused character or triggers functional action
        - KEY1 -> Instant Submit & Connect
        - KEY2 -> Instant Backspace / Delete
        - KEY3 / ESCAPE / BACK -> Cancel & Pop View
        - Backward-compatible CHAR / BACKSPACE / ENTER support for test suites & physical inputs
        """
        # 1. Joystick Grid Navigation
        if event == InputEvent.UP:
            self.cursor_row = (self.cursor_row - 1 + 4) % 4
            return ViewAction(ViewActionType.NONE)

        elif event == InputEvent.DOWN:
            self.cursor_row = (self.cursor_row + 1) % 4
            return ViewAction(ViewActionType.NONE)

        elif event == InputEvent.LEFT:
            self.cursor_col = (self.cursor_col - 1 + 10) % 10
            return ViewAction(ViewActionType.NONE)

        elif event == InputEvent.RIGHT:
            self.cursor_col = (self.cursor_col + 1) % 10
            return ViewAction(ViewActionType.NONE)

        # 2. Selection: Joystick PRESS -> Action on focused key
        elif event == InputEvent.PRESS:
            return self._trigger_key_action()

        # 3. Quick Action Button KEY1 -> Submit & Connect
        elif event in (InputEvent.KEY1, InputEvent.ENTER):
            return self._submit_password()

        # 4. Quick Action Button KEY2 -> Instant Backspace
        elif event in (InputEvent.KEY2, InputEvent.BACKSPACE):
            if len(self.input_text) > 0:
                self.input_text = self.input_text[:-1]
            return ViewAction(ViewActionType.NONE)

        # 5. Quick Action Button KEY3 -> Cancel & Return to network list
        elif event in (InputEvent.KEY3, InputEvent.ESCAPE, InputEvent.BACK):
            return ViewAction(ViewActionType.POP_VIEW)

        # 6. Direct Character typing (for test emulation or physical keyboard)
        elif event == InputEvent.CHAR:
            if char and len(self.input_text) < self.max_length:
                self.input_text += char
            return ViewAction(ViewActionType.NONE)

        return ViewAction(ViewActionType.NONE)


# Backwards compatibility alias
KeyboardInputView = VirtualKeyboardInputView
