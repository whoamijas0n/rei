"""
REI - Display Engine and View Hierarchy
Minimalist Hero Card System for 1.3" SH1106 OLED (128x64 px).
Implements procedural pixel-art rendering, micro-dot pagination, continuous perimeter framing,
and non-blocking navigation stack.
Hardware: Waveshare 1.3" OLED HAT (SPI: DC=GPIO24, RST=GPIO25, rotate=2).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
import logging
from typing import Callable, Dict, List, Optional, Tuple, Any
import time

from PIL import Image, ImageDraw, ImageFont

from .input_handler import InputEvent

logger = logging.getLogger("REI.UI.Display")


class ViewActionType(Enum):
    """Actions emitted by views to the ScreenManager."""
    NONE = auto()
    PUSH_VIEW = auto()
    POP_VIEW = auto()
    REPLACE_VIEW = auto()
    EXECUTE_TASK = auto()


@dataclass
class ViewAction:
    """Action payload returned by view event handlers."""
    action_type: ViewActionType
    target_view: Optional['BaseView'] = None
    task_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)


class IconRenderer:
    """
    Procedural Pixel-Art Icon Engine.
    Draws 20x20 pixel-art icons centered at exact coordinates (cx, cy) = (64, 24).
    """

    @classmethod
    def draw_icon(
        cls,
        draw: ImageDraw.ImageDraw,
        icon_name: str,
        cx: int = 64,
        cy: int = 24,
        center_x: Optional[int] = None,
        center_y: Optional[int] = None,
    ) -> None:
        """Dispatches icon rendering by registered name."""
        if center_x is not None:
            cx = center_x
        if center_y is not None:
            cy = center_y
        name = icon_name.upper().strip()
        draw_fn = getattr(cls, f"_draw_{name}", cls._draw_DEFAULT)
        draw_fn(draw, cx, cy)

    @staticmethod
    def _draw_DEFAULT(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Generic fallback diamond."""
        draw.polygon([(cx, cy - 8), (cx + 8, cy), (cx, cy + 8), (cx - 8, cy)], outline="white")
        draw.point((cx, cy), fill="white")

    @staticmethod
    def _draw_INFO(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Circle badge with 'i' letter."""
        draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), outline="white", fill="black")
        draw.point((cx, cy - 5), fill="white")
        draw.line((cx, cy - 2, cx, cy + 5), fill="white")
        draw.line((cx - 2, cy - 2, cx, cy - 2), fill="white")
        draw.line((cx - 2, cy + 5, cx + 2, cy + 5), fill="white")

    @staticmethod
    def _draw_NETWORK(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Wi-Fi / Signal waves icon."""
        draw.arc((cx - 10, cy - 8, cx + 10, cy + 12), 200, 340, fill="white")
        draw.arc((cx - 6, cy - 4, cx + 6, cy + 8), 200, 340, fill="white")
        draw.ellipse((cx - 2, cy + 3, cx + 2, cy + 7), fill="white")

    @staticmethod
    def _draw_WIFI(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Wi-Fi waves icon alias."""
        IconRenderer._draw_NETWORK(draw, cx, cy)

    @staticmethod
    def _draw_BATTERY(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Battery silhouette with charge level indicator."""
        draw.rectangle((cx - 9, cy - 5, cx + 7, cy + 5), outline="white", fill="black")
        draw.rectangle((cx + 8, cy - 2, cx + 9, cy + 2), fill="white")
        draw.rectangle((cx - 7, cy - 3, cx + 2, cy + 3), fill="white")

    @staticmethod
    def _draw_SYSTEM(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Microprocessor chip with pins."""
        draw.rectangle((cx - 6, cy - 6, cx + 6, cy + 6), outline="white", fill="black")
        draw.rectangle((cx - 3, cy - 3, cx + 3, cy + 3), fill="white")
        for i in (-4, 0, 4):
            draw.line((cx + i, cy - 9, cx + i, cy - 7), fill="white")
            draw.line((cx + i, cy + 7, cx + i, cy + 9), fill="white")
            draw.line((cx - 9, cy + i, cx - 7, cy + i), fill="white")
            draw.line((cx + 7, cy + i, cx + 9, cy + i), fill="white")

    @staticmethod
    def _draw_CPU(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """CPU alias for SYSTEM icon."""
        IconRenderer._draw_SYSTEM(draw, cx, cy)

    @staticmethod
    def _draw_SWITCH(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Network Switch with RJ45 ports."""
        draw.rectangle((cx - 10, cy - 5, cx + 10, cy + 5), outline="white", fill="black")
        for i in (-6, -1, 4):
            draw.rectangle((cx + i, cy - 2, cx + i + 2, cy + 2), fill="white")

    @staticmethod
    def _draw_SWITCHES(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for switch icon."""
        IconRenderer._draw_SWITCH(draw, cx, cy)

    @staticmethod
    def _draw_ENDPOINT(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Desktop PC / Monitor."""
        draw.rectangle((cx - 9, cy - 7, cx + 9, cy + 3), outline="white", fill="black")
        draw.line((cx, cy + 4, cx, cy + 7), fill="white")
        draw.line((cx - 4, cy + 7, cx + 4, cy + 7), fill="white")

    @staticmethod
    def _draw_PC(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for desktop PC icon."""
        IconRenderer._draw_ENDPOINT(draw, cx, cy)

    @staticmethod
    def _draw_VAULT(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Security Lock / Vault Shield."""
        draw.arc((cx - 5, cy - 8, cx + 5, cy + 2), 180, 360, fill="white")
        draw.rectangle((cx - 7, cy - 1, cx + 7, cy + 8), outline="white", fill="black")
        draw.point((cx, cy + 3), fill="white")

    @staticmethod
    def _draw_SHIELD(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for vault / shield icon."""
        IconRenderer._draw_VAULT(draw, cx, cy)

    @staticmethod
    def _draw_IP(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Network IP Node / Globe."""
        draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), outline="white", fill="black")
        draw.line((cx - 8, cy, cx + 8, cy), fill="white")
        draw.ellipse((cx - 4, cy - 8, cx + 4, cy + 8), outline="white")

    @staticmethod
    def _draw_SERIAL(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Serial DB9 Connector."""
        draw.polygon([(cx - 8, cy - 5), (cx + 8, cy - 5), (cx + 6, cy + 6), (cx - 6, cy + 6)], outline="white", fill="black")
        for px in (-4, 0, 4):
            draw.point((cx + px, cy - 2), fill="white")
            draw.point((cx + px, cy + 2), fill="white")

    @staticmethod
    def _draw_SSH(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """SSH Terminal Prompt."""
        draw.rectangle((cx - 9, cy - 7, cx + 9, cy + 7), outline="white", fill="black")
        draw.line((cx - 6, cy - 3, cx - 3, cy), fill="white")
        draw.line((cx - 3, cy, cx - 6, cy + 3), fill="white")
        draw.line((cx - 1, cy + 3, cx + 4, cy + 3), fill="white")

    @staticmethod
    def _draw_SNMP(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """SNMP Radar Scanner."""
        draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), outline="white", fill="black")
        draw.line((cx - 8, cy, cx + 8, cy), fill="white")
        draw.line((cx, cy - 8, cx, cy + 8), fill="white")
        draw.point((cx + 4, cy - 4), fill="white")

    @staticmethod
    def _draw_WINDOWS(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Windows USB / OS logo."""
        draw.rectangle((cx - 7, cy - 6, cx - 1, cy - 1), fill="white")
        draw.rectangle((cx + 1, cy - 6, cx + 7, cy - 1), fill="white")
        draw.rectangle((cx - 7, cy + 1, cx - 1, cy + 6), fill="white")
        draw.rectangle((cx + 1, cy + 1, cx + 7, cy + 6), fill="white")

    @staticmethod
    def _draw_LINUX(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Linux Terminal / Tux icon."""
        IconRenderer._draw_SSH(draw, cx, cy)

    @staticmethod
    def _draw_TOOLS(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Utility Gear icon (20x20)."""
        draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), outline="white", fill="black")
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill="white")
        for dx, dy in [(-8, 0), (8, 0), (0, -8), (0, 8), (-6, -6), (6, 6), (-6, 6), (6, -6)]:
            draw.rectangle((cx + dx - 1, cy + dy - 1, cx + dx + 1, cy + dy + 1), fill="white")

    @staticmethod
    def _draw_UTILIDADES(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for TOOLS / Gear icon."""
        IconRenderer._draw_TOOLS(draw, cx, cy)

    @staticmethod
    def _draw_UTIL(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for TOOLS / Gear icon."""
        IconRenderer._draw_TOOLS(draw, cx, cy)

    @staticmethod
    def _draw_POWER(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Power / Lightning bolt icon (20x20)."""
        draw.polygon([
            (cx, cy - 8),
            (cx - 5, cy),
            (cx - 1, cy),
            (cx - 3, cy + 8),
            (cx + 5, cy - 1),
            (cx + 1, cy - 1)
        ], fill="white")

    @staticmethod
    def _draw_ALIMENTACION(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for POWER icon."""
        IconRenderer._draw_POWER(draw, cx, cy)

    @staticmethod
    def _draw_POWEROFF(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Standard Power Off button icon."""
        draw.arc((cx - 7, cy - 7, cx + 7, cy + 7), 300, 240, fill="white")
        draw.line((cx, cy - 8, cx, cy - 1), fill="white")

    @staticmethod
    def _draw_APAGAR(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for POWEROFF icon."""
        IconRenderer._draw_POWEROFF(draw, cx, cy)

    @staticmethod
    def _draw_SHUTDOWN(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for POWEROFF icon."""
        IconRenderer._draw_POWEROFF(draw, cx, cy)

    @staticmethod
    def _draw_REBOOT(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Circular restart / reboot arrows."""
        draw.arc((cx - 8, cy - 8, cx + 8, cy + 8), 40, 310, fill="white")
        draw.polygon([(cx + 2, cy - 9), (cx + 8, cy - 5), (cx + 3, cy - 3)], fill="white")

    @staticmethod
    def _draw_REINICIAR(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for REBOOT icon."""
        IconRenderer._draw_REBOOT(draw, cx, cy)

    @staticmethod
    def _draw_RESTART(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        """Alias for REBOOT icon."""
        IconRenderer._draw_REBOOT(draw, cx, cy)


class BaseView(ABC):
    """Abstract Base Class for all OLED views."""

    def __init__(self, title: str = ""):
        self.title = title
        self._font: Optional[ImageFont.ImageFont] = None

    @property
    def font(self) -> ImageFont.ImageFont:
        """Lazily loads default bitmap font."""
        if self._font is None:
            self._font = ImageFont.load_default()
        return self._font

    def draw_perimeter_border(self, draw: ImageDraw.ImageDraw) -> None:
        """
        Renders the continuous 1px perimeter border.
        Standard coordinates: (1, 1) to (126, 62).
        """
        draw.rectangle((1, 1, 126, 62), outline="white", fill="black")

    @abstractmethod
    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        """Renders the view content to the 1-bit PIL canvas."""
        pass

    @abstractmethod
    def handle_input(self, event: InputEvent) -> ViewAction:
        """Processes hardware input event and returns a navigation action."""
        pass

    def update(self) -> None:
        """Optional tick hook called on each frame (30 FPS)."""
        pass


@dataclass
class HeroCard:
    """Data representation of a single card within a HeroCardDeckView."""
    title: str
    icon_name: str
    submenu: Optional[BaseView] = None
    action_task_id: Optional[str] = None
    on_select: Optional[Callable[[], None]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class HeroCardDeckView(BaseView):
    """
    Carrusel de tarjetas minimalistas (Borde + Puntos + Icono + Título).
    Renders 20x20 procedural pixel-art hero icon centered at (64, 24),
    continuous perimeter frame (1,1)-(126,62), micro-dot carousel pagination,
    and dynamically centered uppercase title at Y=44.
    """

    def __init__(self, title: str, cards: Optional[List[HeroCard]] = None):
        super().__init__(title=title)
        self.cards: List[HeroCard] = cards or []
        self.active_index: int = 0

    def add_card(self, card: HeroCard) -> 'HeroCardDeckView':
        """Appends a card to the deck."""
        self.cards.append(card)
        return self

    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        # 1. Borde perimetral fijo (1, 1) a (126, 62)
        self.draw_perimeter_border(draw)

        if not self.cards:
            draw.text((30, 28), "NO ITEMS", fill="white", font=self.font)
            return

        total = len(self.cards)
        self.active_index = max(0, min(self.active_index, total - 1))
        active_card = self.cards[self.active_index]

        # 2. Puntos de carrusel en la esquina superior derecha
        start_x = 122 - (total * 5)
        for i in range(total):
            x = start_x + (i * 5)
            if i == self.active_index:
                draw.rectangle((x, 4, x + 2, 6), fill="white")
            else:
                draw.point((x + 1, 5), fill="white")

        # 3. Dibujar Icono Centrado (cx=64, cy=24)
        IconRenderer.draw_icon(draw, active_card.icon_name, cx=64, cy=24)

        # 4. Dibujar Título Centrado en Y=44
        title_text = active_card.title.upper()
        text_w = len(title_text) * 6
        text_x = max(4, (width - text_w) // 2)
        draw.text((text_x, 44), title_text, font=self.font, fill="white")

    def handle_input(self, event: InputEvent) -> ViewAction:
        if not self.cards:
            if event in (InputEvent.KEY3, InputEvent.KEY1, InputEvent.BACK):
                return ViewAction(ViewActionType.POP_VIEW)
            return ViewAction(ViewActionType.NONE)

        total_cards = len(self.cards)

        # Navigation: Left/Right Carousel
        if event == InputEvent.RIGHT:
            self.active_index = (self.active_index + 1) % total_cards
            return ViewAction(ViewActionType.NONE)

        elif event == InputEvent.LEFT:
            self.active_index = (self.active_index - 1 + total_cards) % total_cards
            return ViewAction(ViewActionType.NONE)

        # Selection: Joystick Press or Physical Key 1
        elif event in (InputEvent.PRESS, InputEvent.KEY1):
            current_card = self.cards[self.active_index]
            if current_card.on_select:
                current_card.on_select()
            if current_card.submenu:
                return ViewAction(ViewActionType.PUSH_VIEW, target_view=current_card.submenu)
            if current_card.action_task_id:
                return ViewAction(
                    ViewActionType.EXECUTE_TASK,
                    task_id=current_card.action_task_id,
                    payload={"card_title": current_card.title}
                )
            return ViewAction(ViewActionType.NONE)

        # Back Navigation: Physical Key 3 / BACK
        elif event in (InputEvent.KEY3, InputEvent.BACK):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)


class DetailCardView(BaseView):
    """
    Vista de detalle / métrica final (Mantiene el borde).
    """

    def __init__(
        self,
        title: str,
        initial_lines: Optional[List[str]] = None,
        on_refresh: Optional[Callable[[], None]] = None
    ):
        super().__init__(title=title)
        self.lines: List[str] = initial_lines or []
        self.on_refresh = on_refresh
        self.status_text: str = "LISTO"
        self.is_loading: bool = False
        self.scroll_offset: int = 0
        self._spinner_tick: int = 0

    def set_content(self, lines: List[str], status: str = "OK", is_loading: bool = False) -> None:
        """Updates detail lines and status badge."""
        self.lines = lines
        self.status_text = status
        self.is_loading = is_loading
        self.scroll_offset = 0

    def append_line(self, line: str) -> None:
        """Appends a line to the detail buffer."""
        self.lines.append(line)

    def update(self) -> None:
        """Increments spinner animation ticker."""
        if self.is_loading:
            self._spinner_tick = (self._spinner_tick + 1) % 12

    def render(self, draw: ImageDraw.ImageDraw, width: int = 128, height: int = 64) -> None:
        # 1. Borde perimetral
        self.draw_perimeter_border(draw)

        # 2. Cabecera centrada
        header_text = self.title.upper()
        text_w = len(header_text) * 6
        draw.text(((width - text_w) // 2, 6), header_text, font=self.font, fill="white")
        draw.line((4, 18, 123, 18), fill="white")

        # 3. Contenido limpio (Y=24, espaciado 12px)
        y = 24
        visible_lines = self.lines[self.scroll_offset : self.scroll_offset + 3]
        if not visible_lines:
            draw.text((8, y), "Sin datos / Presione OK", font=self.font, fill="white")
        else:
            for line in visible_lines:
                draw.text((8, y), line[:20], font=self.font, fill="white")
                y += 12

    def handle_input(self, event: InputEvent) -> ViewAction:
        max_scroll = max(0, len(self.lines) - 3)

        if event == InputEvent.UP:
            self.scroll_offset = max(0, self.scroll_offset - 1)
            return ViewAction(ViewActionType.NONE)

        elif event == InputEvent.DOWN:
            self.scroll_offset = min(max_scroll, self.scroll_offset + 1)
            return ViewAction(ViewActionType.NONE)

        elif event == InputEvent.KEY2:
            if self.on_refresh:
                self.is_loading = True
                self.on_refresh()
            return ViewAction(ViewActionType.NONE)

        # Return on Key3, Key1, Press or Back
        elif event in (InputEvent.KEY3, InputEvent.KEY1, InputEvent.PRESS, InputEvent.BACK):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)


class ScreenManager:
    """
    Gestor de pantallas y renderizado OLED SH1106.
    Soporta comunicación SPI directa con el HAT OLED Waveshare 1.3"
    (DC=GPIO24, RST=GPIO25, rotate=2) con fallback a I2C o simulación.
    """

    def __init__(
        self,
        width: int = 128,
        height: int = 64,
        gpio_dc: int = 24,
        gpio_rst: int = 25,
        bus_speed_hz: int = 8000000,
        rotate: int = 2,
        i2c_port: int = 1,
        i2c_address: int = 0x3C,
    ):
        self.width = width
        self.height = height
        self.rotate = rotate
        self._view_stack: List[BaseView] = []
        self._oled_device: Optional[Any] = None
        self._buffer: Image.Image = Image.new("1", (self.width, self.height), "black")
        self._draw: ImageDraw.ImageDraw = ImageDraw.Draw(self._buffer)

        self._initialize_display_hardware(
            gpio_dc=gpio_dc,
            gpio_rst=gpio_rst,
            bus_speed_hz=bus_speed_hz,
            rotate=rotate,
            i2c_port=i2c_port,
            i2c_address=i2c_address,
        )

    def _initialize_display_hardware(
        self,
        gpio_dc: int,
        gpio_rst: int,
        bus_speed_hz: int,
        rotate: int,
        i2c_port: int,
        i2c_address: int,
    ) -> None:
        """Inicializa el controlador luma.oled SH1106 por SPI."""
        try:
            from luma.core.interface.serial import i2c, spi
            from luma.oled.device import sh1106

            # 1. SPI Interface (Waveshare 1.3" OLED HAT: DC=GPIO24, RST=GPIO25, rotate=2)
            try:
                serial_interface = spi(
                    device=0,
                    port=0,
                    bus_speed_hz=bus_speed_hz,
                    gpio_DC=gpio_dc,
                    gpio_RST=gpio_rst,
                )
                self._oled_device = sh1106(
                    serial_interface,
                    width=self.width,
                    height=self.height,
                    rotate=rotate,
                )
                logger.info(
                    f"Initialized SH1106 OLED via SPI (DC={gpio_dc}, RST={gpio_rst}, rotate={rotate})."
                )
                return
            except Exception as spi_ex:
                logger.warning(f"SPI initialization failed ({spi_ex}), trying I2C fallback...")

            # 2. I2C Interface Fallback
            try:
                serial_interface = i2c(port=i2c_port, address=i2c_address)
                self._oled_device = sh1106(
                    serial_interface,
                    width=self.width,
                    height=self.height,
                    rotate=rotate,
                )
                logger.info(f"Initialized SH1106 OLED via I2C (port={i2c_port}, addr={hex(i2c_address)}).")
                return
            except Exception as i2c_ex:
                logger.warning(f"I2C initialization failed ({i2c_ex}).")

        except (ImportError, Exception) as ex:
            self._oled_device = None
            logger.warning(
                f"Physical SH1106 OLED not detected ({ex}). "
                "Operating in virtual frame-buffer mode."
            )

    @property
    def current_view(self) -> Optional[BaseView]:
        """Returns the top view on the stack."""
        return self._view_stack[-1] if self._view_stack else None

    def push_view(self, view: BaseView) -> None:
        """Pushes a new view onto the navigation stack."""
        self._view_stack.append(view)
        logger.debug(f"Pushed view: {view.title} (Stack depth: {len(self._view_stack)})")

    def pop_view(self) -> Optional[BaseView]:
        """Pops the top view from the stack."""
        if len(self._view_stack) > 1:
            popped = self._view_stack.pop()
            logger.debug(f"Popped view: {popped.title} (Stack depth: {len(self._view_stack)})")
            return popped
        return None

    def set_root_view(self, view: BaseView) -> None:
        """Clears stack and sets the root view."""
        self._view_stack = [view]

    def render(self) -> Image.Image:
        """
        Renders the active view into the 128x64 1-bit buffer
        and pushes pixels to the SH1106 OLED device.
        """
        # Clear frame to black
        self._draw.rectangle([0, 0, self.width - 1, self.height - 1], fill="black")

        view = self.current_view
        if view:
            view.update()
            view.render(self._draw, self.width, self.height)

        # Send buffer to OLED hardware
        if self._oled_device is not None:
            try:
                self._oled_device.display(self._buffer)
            except Exception as ex:
                logger.error(f"Error transferring frame to OLED: {ex}")

        return self._buffer

    @property
    def buffer(self) -> Image.Image:
        """Access the current raw PIL 1-bit image buffer."""
        return self._buffer

    def clear(self) -> None:
        """Clears physical display on shutdown."""
        if self._oled_device is not None:
            try:
                self._oled_device.clear()
            except Exception:
                pass
