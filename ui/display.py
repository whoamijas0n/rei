"""
OmniDiag Hub - Display Engine and View Hierarchy
Minimalist Hero Card System for 1.3" SH1106 OLED (128x64 px).
Implements procedural pixel-art rendering, micro-dot pagination, continuous perimeter framing,
and non-blocking navigation stack.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
import logging
from typing import Callable, Dict, List, Optional, Tuple, Any
import time

from PIL import Image, ImageDraw, ImageFont

from .input_handler import InputEvent

logger = logging.getLogger("OmniDiag.UI.Display")


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
    Draws 20x20 pixel-art icons centered at exact coordinates (center_x, center_y).
    Bounding box: (center_x - 10, center_y - 10) to (center_x + 9, center_y + 9).
    """

    @classmethod
    def draw_icon(cls, draw: ImageDraw.ImageDraw, icon_name: str, center_x: int = 64, center_y: int = 24) -> None:
        """Dispatches icon rendering by registered name."""
        name = icon_name.upper().strip()
        draw_fn = getattr(cls, f"_draw_{name}", cls._draw_DEFAULT)
        x0 = center_x - 10
        y0 = center_y - 10
        draw_fn(draw, x0, y0)

    @staticmethod
    def _draw_DEFAULT(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Generic fallback icon (geometric diamond)."""
        draw.polygon([(x + 10, y + 2), (x + 18, y + 10), (x + 10, y + 18), (x + 2, y + 10)], outline=1)
        draw.point((x + 10, y + 10), fill=1)

    @staticmethod
    def _draw_INFO(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Info badge icon (circle badge with 'i' glyph)."""
        # Outer circle outline
        draw.ellipse([x + 1, y + 1, x + 18, y + 18], outline=1)
        # Dot of the 'i'
        draw.rectangle([x + 9, y + 4, x + 10, y + 5], fill=1)
        # Stem of the 'i'
        draw.rectangle([x + 9, y + 8, x + 10, y + 14], fill=1)
        # Base of the 'i'
        draw.line([x + 7, y + 14, x + 12, y + 14], fill=1)

    @staticmethod
    def _draw_NETWORK(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Ethernet Network Switch icon with RJ45 ports and activity LEDs."""
        # Main switch chassis
        draw.rectangle([x + 1, y + 3, x + 18, y + 16], outline=1)
        # 4 Port cutouts
        for px in [x + 3, x + 7, x + 11, x + 15]:
            draw.rectangle([px, y + 9, px + 2, y + 13], outline=1)
            # Port latch notch
            draw.point((px + 1, y + 9), fill=0)
            # Port activity LED
            draw.point((px + 1, y + 5), fill=1)

    @staticmethod
    def _draw_ENDPOINT(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """PC Workstation / Monitor icon."""
        # Monitor outer frame
        draw.rectangle([x + 2, y + 2, x + 17, y + 13], outline=1)
        # Power LED
        draw.point((x + 15, y + 12), fill=1)
        # Stand neck
        draw.line([x + 9, y + 14, x + 10, y + 14], fill=1)
        draw.line([x + 9, y + 15, x + 10, y + 15], fill=1)
        # Stand base
        draw.line([x + 5, y + 16, x + 14, y + 16], fill=1)

    @staticmethod
    def _draw_VAULT(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Security Vault / Safe Door icon."""
        # Safe box body
        draw.rectangle([x + 2, y + 2, x + 17, y + 17], outline=1)
        # Rivets / Corner bolts
        draw.point((x + 4, y + 4), fill=1)
        draw.point((x + 15, y + 4), fill=1)
        draw.point((x + 4, y + 15), fill=1)
        draw.point((x + 15, y + 15), fill=1)
        # Dial ring
        draw.ellipse([x + 7, y + 7, x + 12, y + 12], outline=1)
        # Dial handle notch
        draw.line([x + 13, y + 9, x + 15, y + 9], fill=1)

    @staticmethod
    def _draw_IP(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """IP / Network Node Globe icon."""
        # Globe circle
        draw.ellipse([x + 2, y + 2, x + 17, y + 17], outline=1)
        # Equator
        draw.line([x + 2, y + 9, x + 17, y + 9], fill=1)
        # Meridian curve
        draw.ellipse([x + 6, y + 2, x + 13, y + 17], outline=1)

    @staticmethod
    def _draw_WIFI(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Wi-Fi Signal Radiating Waves icon."""
        # Center signal dot
        draw.rectangle([x + 9, y + 15, x + 10, y + 16], fill=1)
        # Inner wave
        draw.arc([x + 6, y + 11, x + 13, y + 18], start=210, end=330, fill=1)
        # Middle wave
        draw.arc([x + 3, y + 7, x + 16, y + 20], start=210, end=330, fill=1)
        # Outer wave
        draw.arc([x + 0, y + 3, x + 19, y + 22], start=210, end=330, fill=1)

    @staticmethod
    def _draw_BATTERY(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Battery Gauge icon."""
        # Main shell
        draw.rectangle([x + 2, y + 5, x + 16, y + 14], outline=1)
        # Positive terminal nib
        draw.rectangle([x + 17, y + 7, x + 18, y + 12], fill=1)
        # Charge bars (80% full)
        draw.rectangle([x + 4, y + 7, x + 6, y + 12], fill=1)
        draw.rectangle([x + 8, y + 7, x + 10, y + 12], fill=1)
        draw.rectangle([x + 12, y + 7, x + 14, y + 12], fill=1)

    @staticmethod
    def _draw_CPU(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """CPU / System Chip icon."""
        # Main chip body
        draw.rectangle([x + 4, y + 4, x + 15, y + 15], outline=1)
        # Core die
        draw.rectangle([x + 7, y + 7, x + 12, y + 12], fill=1)
        # Top & bottom pins
        for px in [x + 6, x + 9, x + 13]:
            draw.line([px, y + 1, px, y + 3], fill=1)
            draw.line([px, y + 16, px, y + 18], fill=1)
        # Left & right pins
        for py in [y + 6, y + 9, y + 13]:
            draw.line([x + 1, py, x + 3, py], fill=1)
            draw.line([x + 16, py, x + 18, py], fill=1)

    @staticmethod
    def _draw_SERIAL(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """RS232 / Cisco Serial DB9 connector icon."""
        # D-Sub shell
        draw.polygon([
            (x + 2, y + 5),
            (x + 17, y + 5),
            (x + 15, y + 15),
            (x + 4, y + 15)
        ], outline=1)
        # Screw lugs
        draw.point((x + 1, y + 9), fill=1)
        draw.point((x + 18, y + 9), fill=1)
        # Pin dots (top row 5, bottom row 4)
        for px in range(x + 5, x + 15, 2):
            draw.point((px, y + 8), fill=1)
        for px in range(x + 6, x + 14, 2):
            draw.point((px, y + 12), fill=1)

    @staticmethod
    def _draw_SSH(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """SSH Terminal / CLI Prompt icon."""
        # Window frame
        draw.rectangle([x + 1, y + 2, x + 18, y + 17], outline=1)
        # Title bar divider
        draw.line([x + 1, y + 5, x + 18, y + 5], fill=1)
        # Window buttons
        draw.point((x + 3, y + 3), fill=1)
        draw.point((x + 5, y + 3), fill=1)
        # Prompt '>_'
        draw.line([x + 3, y + 8, x + 6, y + 11], fill=1)
        draw.line([x + 6, y + 11, x + 3, y + 14], fill=1)
        draw.line([x + 8, y + 14, x + 12, y + 14], fill=1)

    @staticmethod
    def _draw_SNMP(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """SNMP Radar / Scanner icon."""
        # Outer circle
        draw.ellipse([x + 1, y + 1, x + 18, y + 18], outline=1)
        # Crosshair lines
        draw.line([x + 1, y + 9, x + 18, y + 9], fill=1)
        draw.line([x + 9, y + 1, x + 9, y + 18], fill=1)
        # Radar sweep target blip
        draw.rectangle([x + 12, y + 4, x + 14, y + 6], fill=1)

    @staticmethod
    def _draw_WINDOWS(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Windows / RNDIS USB Endpoint icon."""
        # 4 Quadrants logo
        draw.polygon([(x + 2, y + 5), (x + 8, y + 4), (x + 8, y + 9), (x + 2, y + 9)], fill=1)
        draw.polygon([(x + 10, y + 4), (x + 17, y + 2), (x + 17, y + 9), (x + 10, y + 9)], fill=1)
        draw.polygon([(x + 2, y + 11), (x + 8, y + 11), (x + 8, y + 16), (x + 2, y + 15)], fill=1)
        draw.polygon([(x + 10, y + 11), (x + 17, y + 11), (x + 17, y + 18), (x + 10, y + 16)], fill=1)

    @staticmethod
    def _draw_LINUX(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
        """Linux Tux Console icon."""
        # Penguin body outline
        draw.ellipse([x + 5, y + 2, x + 14, y + 15], outline=1)
        # Eyes
        draw.point((x + 8, y + 5), fill=1)
        draw.point((x + 11, y + 5), fill=1)
        # Beak
        draw.line([x + 8, y + 7, x + 11, y + 7], fill=1)
        # Belly
        draw.ellipse([x + 7, y + 9, x + 12, y + 14], fill=1)
        # Feet
        draw.line([x + 4, y + 16, x + 8, y + 16], fill=1)
        draw.line([x + 11, y + 16, x + 15, y + 16], fill=1)


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
        draw.rectangle([1, 1, 126, 62], outline=1)

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
    Hero Card Deck View (Carousel).
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
        # 1. Continuous Perimeter Border (1, 1) to (126, 62)
        self.draw_perimeter_border(draw)

        if not self.cards:
            draw.text((30, 28), "NO ITEMS", fill=1, font=self.font)
            return

        total_cards = len(self.cards)
        self.active_index = max(0, min(self.active_index, total_cards - 1))
        active_card = self.cards[self.active_index]

        # 2. Micro-Dot Pagination (Top-Right Corner)
        if total_cards > 1:
            dot_spacing = 4
            total_pagination_width = (total_cards - 1) * dot_spacing
            start_x = 122 - total_pagination_width
            dot_y = 5

            for idx in range(total_cards):
                px = start_x + (idx * dot_spacing)
                if idx == self.active_index:
                    # Active dot: 2x2 filled micro-dot
                    draw.rectangle([px, dot_y, px + 1, dot_y + 1], fill=1)
                else:
                    # Inactive dot: 1x1 micro-dot
                    draw.point((px, dot_y), fill=1)

        # 3. Hero Icon (20x20 px centered at 64, 24)
        IconRenderer.draw_icon(draw, active_card.icon_name, center_x=64, center_y=24)

        # 4. Dynamically Centered Uppercase Title at Y=44
        title_text = active_card.title.upper()
        bbox = draw.textbbox((0, 0), title_text, font=self.font)
        text_width = bbox[2] - bbox[0]
        title_x = max(4, (width - text_width) // 2)
        draw.text((title_x, 44), title_text, fill=1, font=self.font)

    def handle_input(self, event: InputEvent) -> ViewAction:
        if not self.cards:
            if event in (InputEvent.KEY1, InputEvent.BACK):
                return ViewAction(ViewActionType.POP_VIEW)
            return ViewAction(ViewActionType.NONE)

        total_cards = len(self.cards)

        # Navigation: Left/Right Carousel
        if event == InputEvent.LEFT:
            self.active_index = (self.active_index - 1) % total_cards
            return ViewAction(ViewActionType.NONE)

        elif event == InputEvent.RIGHT:
            self.active_index = (self.active_index + 1) % total_cards
            return ViewAction(ViewActionType.NONE)

        # Selection: Joystick Press or Physical Key 3
        elif event in (InputEvent.PRESS, InputEvent.KEY3):
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

        # Back Navigation: Physical Key 1 / BACK
        elif event in (InputEvent.KEY1, InputEvent.BACK):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)


class DetailCardView(BaseView):
    """
    Detail Card View.
    Displays diagnostic metrics, live status outputs, and telemetry data
    while retaining the continuous perimeter frame.
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
        # 1. Continuous Perimeter Border
        self.draw_perimeter_border(draw)

        # 2. Header Title (Uppercase, Y=4)
        header_text = self.title.upper()
        draw.text((5, 4), header_text[:14], fill=1, font=self.font)

        # Status / Activity Badge (Top Right)
        if self.is_loading:
            spinner_frames = ["-", "\\", "|", "/"]
            spin_char = spinner_frames[(self._spinner_tick // 3) % 4]
            draw.text((114, 4), spin_char, fill=1, font=self.font)
        else:
            draw.text((96, 4), self.status_text[:5].upper(), fill=1, font=self.font)

        # Header Separator
        draw.line([4, 15, 123, 15], fill=1)

        # 3. Content Lines (Y=18 to Y=58, max 4 visible lines)
        visible_lines = 4
        line_height = 10
        y_start = 18

        if not self.lines:
            draw.text((8, 28), "Sin datos / Presione OK", fill=1, font=self.font)
        else:
            for idx in range(visible_lines):
                line_idx = self.scroll_offset + idx
                if line_idx < len(self.lines):
                    draw.text(
                        (5, y_start + (idx * line_height)),
                        self.lines[line_idx][:22],
                        fill=1,
                        font=self.font
                    )

    def handle_input(self, event: InputEvent) -> ViewAction:
        max_scroll = max(0, len(self.lines) - 4)

        if event == InputEvent.UP:
            self.scroll_offset = max(0, self.scroll_offset - 1)
            return ViewAction(ViewActionType.NONE)

        elif event == InputEvent.DOWN:
            self.scroll_offset = min(max_scroll, self.scroll_offset + 1)
            return ViewAction(ViewActionType.NONE)

        elif event in (InputEvent.PRESS, InputEvent.KEY2, InputEvent.KEY3):
            if self.on_refresh:
                self.is_loading = True
                self.on_refresh()
            return ViewAction(ViewActionType.NONE)

        elif event in (InputEvent.KEY1, InputEvent.BACK):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)


class ScreenManager:
    """
    Manages view stack transitions and OLED frame rendering.
    Supports hardware SH1106 OLED (SPI/I2C) with seamless fallback
    to virtual buffer simulation.
    """

    def __init__(self, width: int = 128, height: int = 64, i2c_port: int = 1, i2c_address: int = 0x3C):
        self.width = width
        self.height = height
        self._view_stack: List[BaseView] = []
        self._oled_device: Optional[Any] = None
        self._buffer: Image.Image = Image.new("1", (self.width, self.height), 0)
        self._draw: ImageDraw.ImageDraw = ImageDraw.Draw(self._buffer)

        self._initialize_display_hardware(i2c_port, i2c_address)

    def _initialize_display_hardware(self, i2c_port: int, i2c_address: int) -> None:
        """Attempts to initialize luma.oled SH1106 hardware driver."""
        try:
            from luma.core.interface.serial import i2c, spi
            from luma.oled.device import sh1106

            # Try SPI first (Waveshare 1.3inch OLED default), then I2C fallback
            try:
                # SPI configuration (CS=GPIO8, DC=GPIO25, RST=GPIO27)
                serial_interface = spi(device=0, port=0, bus_speed_hz=8000000, gpio_DC=25, gpio_RST=27)
                self._oled_device = sh1106(serial_interface, width=self.width, height=self.height, rotate=0)
                logger.info("Initialized SH1106 OLED via SPI interface.")
            except Exception as spi_ex:
                logger.debug(f"SPI initialization failed ({spi_ex}), trying I2C...")
                serial_interface = i2c(port=i2c_port, address=i2c_address)
                self._oled_device = sh1106(serial_interface, width=self.width, height=self.height, rotate=0)
                logger.info("Initialized SH1106 OLED via I2C interface.")

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
        # Clear frame (0 = Black)
        self._draw.rectangle([0, 0, self.width - 1, self.height - 1], fill=0)

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
