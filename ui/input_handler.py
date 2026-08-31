"""
REI - Hardware and USB Keyboard Input Handler
Non-blocking GPIO event capture for Waveshare 1.3" OLED HAT (SH1106) and
USB Physical Keyboard listener (evdev / Linux input subsystem).
Maps 5-way joystick, 3 physical buttons, and USB keyboard characters to a thread-safe event queue.
"""

from enum import Enum, auto
import logging
import os
import queue
import select
import sys
import threading
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("REI.UI.Input")


class InputEvent(Enum):
    """Enumeration of hardware and keyboard input events."""
    UP = auto()
    DOWN = auto()
    LEFT = auto()
    RIGHT = auto()
    PRESS = auto()       # Joystick Center Click
    KEY1 = auto()        # Physical Key 1 (Top / Back)
    KEY2 = auto()        # Physical Key 2 (Middle / Action)
    KEY3 = auto()        # Physical Key 3 (Bottom / Select)
    BACK = auto()        # Aliased convenience for Key 1
    CHAR = auto()        # Alphanumeric character or symbol typed
    ENTER = auto()       # Keyboard Enter / Return
    BACKSPACE = auto()   # Keyboard Backspace / Delete
    ESCAPE = auto()      # Keyboard Escape


# Default BCM GPIO Pinout for Waveshare 1.3inch OLED HAT (SH1106)
DEFAULT_PINS = {
    "JOY_UP": 6,
    "JOY_DOWN": 19,
    "JOY_LEFT": 5,
    "JOY_RIGHT": 26,
    "JOY_PRESS": 13,
    "KEY1": 21,
    "KEY2": 20,
    "KEY3": 16,
}


class USBKeyboardListener(threading.Thread):
    """
    Background worker that listens for USB keyboard events via Linux evdev
    and terminal stdin without blocking the main 30 FPS UI loop.
    """

    # Basic evdev scancode to ASCII map
    SCANCODE_MAP: Dict[str, Tuple[str, str]] = {
        "KEY_A": ("a", "A"), "KEY_B": ("b", "B"), "KEY_C": ("c", "C"),
        "KEY_D": ("d", "D"), "KEY_E": ("e", "E"), "KEY_F": ("f", "F"),
        "KEY_G": ("g", "G"), "KEY_H": ("h", "H"), "KEY_I": ("i", "I"),
        "KEY_J": ("j", "J"), "KEY_K": ("k", "K"), "KEY_L": ("l", "L"),
        "KEY_M": ("m", "M"), "KEY_N": ("n", "N"), "KEY_O": ("o", "O"),
        "KEY_P": ("p", "P"), "KEY_Q": ("q", "Q"), "KEY_R": ("r", "R"),
        "KEY_S": ("s", "S"), "KEY_T": ("t", "T"), "KEY_U": ("u", "U"),
        "KEY_V": ("v", "V"), "KEY_W": ("w", "W"), "KEY_X": ("x", "X"),
        "KEY_Y": ("y", "Y"), "KEY_Z": ("z", "Z"),
        "KEY_1": ("1", "!"), "KEY_2": ("2", "@"), "KEY_3": ("3", "#"),
        "KEY_4": ("4", "$"), "KEY_5": ("5", "%"), "KEY_6": ("6", "^"),
        "KEY_7": ("7", "&"), "KEY_8": ("8", "*"), "KEY_9": ("9", "("),
        "KEY_0": ("0", ")"),
        "KEY_MINUS": ("-", "_"), "KEY_EQUAL": ("=", "+"),
        "KEY_SPACE": (" ", " "), "KEY_DOT": (".", ">"),
        "KEY_COMMA": (",", "<"), "KEY_SLASH": ("/", "?"),
        "KEY_BACKSLASH": ("\\", "|"), "KEY_SEMICOLON": (";", ":"),
        "KEY_APOSTROPHE": ("'", "\""), "KEY_GRAVE": ("`", "~"),
        "KEY_LEFTBRACE": ("[", "{"), "KEY_RIGHTBRACE": ("]", "}"),
    }

    def __init__(self, handler: 'GPIOInputHandler'):
        super().__init__(name="REIKeyboardListener", daemon=True)
        self.handler = handler
        self.running = True
        self._shift_pressed = False

    def run(self) -> None:
        """Continuously monitors evdev devices and stdin for key inputs."""
        try:
            import evdev
            has_evdev = True
        except ImportError:
            has_evdev = False

        while self.running:
            handled = False

            # 1. Try Linux evdev devices
            if has_evdev:
                try:
                    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
                    # Filter keyboard devices
                    kb_devices = [d for d in devices if "keyboard" in d.name.lower() or "kbd" in d.name.lower() or d.name]
                    if kb_devices:
                        r, _, _ = select.select(kb_devices, [], [], 0.1)
                        for dev in r:
                            for event in dev.read():
                                if event.type == evdev.ecodes.EV_KEY:
                                    key_event = evdev.categorize(event)
                                    keycode = key_event.keycode
                                    if isinstance(keycode, list):
                                        keycode = keycode[0]

                                    if key_event.keystate in (key_event.key_down, key_event.key_hold):
                                        if keycode in ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"):
                                            self._shift_pressed = True
                                        elif keycode in ("KEY_ENTER", "KEY_KPENTER"):
                                            self.handler.inject_event(InputEvent.ENTER)
                                        elif keycode == "KEY_BACKSPACE":
                                            self.handler.inject_event(InputEvent.BACKSPACE)
                                        elif keycode == "KEY_ESC":
                                            self.handler.inject_event(InputEvent.ESCAPE)
                                        elif keycode in self.SCANCODE_MAP:
                                            norm_char, shift_char = self.SCANCODE_MAP[keycode]
                                            char = shift_char if self._shift_pressed else norm_char
                                            self.handler.inject_char(char)
                                    elif key_event.keystate == key_event.key_up:
                                        if keycode in ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"):
                                            self._shift_pressed = False
                        handled = True
                except Exception as ev_ex:
                    logger.debug(f"evdev keyboard read error: {ev_ex}")

            # 2. Fallback: Check stdin (if in terminal / console)
            if not handled and sys.stdin and not sys.stdin.closed:
                try:
                    r, _, _ = select.select([sys.stdin], [], [], 0.08)
                    if r:
                        line = sys.stdin.readline()
                        if line:
                            for ch in line.strip("\r\n"):
                                self.handler.inject_char(ch)
                            if line.endswith("\n"):
                                self.handler.inject_event(InputEvent.ENTER)
                except Exception:
                    pass

            if not handled:
                select.select([], [], [], 0.1)


class GPIOInputHandler:
    """
    Unified Input Handler for Waveshare 1.3" OLED HAT GPIO and USB Keyboards.
    Manages debounced GPIO interrupts, physical USB keyboard input, and pushes
    sanitized events to a thread-safe FIFO queue.
    """

    def __init__(self, pin_config: Optional[Dict[str, int]] = None, bounce_time: float = 0.08):
        self.pins = pin_config or DEFAULT_PINS
        self.bounce_time = bounce_time
        self._event_queue: queue.Queue[Tuple[InputEvent, Optional[str]]] = queue.Queue(maxsize=64)
        self._buttons: Dict[str, Any] = {}
        self._hardware_active = False
        self._last_char: Optional[str] = None

        # 1. Initialize physical GPIO buttons
        self._initialize_gpio()

        # 2. Start non-blocking USB Keyboard Listener
        self._keyboard_listener = USBKeyboardListener(self)
        self._keyboard_listener.start()

    def _initialize_gpio(self) -> None:
        """Initializes gpiozero Button listeners with pull-up resistors."""
        try:
            from gpiozero import Button

            button_event_map = {
                "JOY_UP": InputEvent.UP,
                "JOY_DOWN": InputEvent.DOWN,
                "JOY_LEFT": InputEvent.LEFT,
                "JOY_RIGHT": InputEvent.RIGHT,
                "JOY_PRESS": InputEvent.PRESS,
                "KEY1": InputEvent.KEY1,
                "KEY2": InputEvent.KEY2,
                "KEY3": InputEvent.KEY3,
            }

            for name, pin in self.pins.items():
                if name in button_event_map:
                    event = button_event_map[name]
                    btn = Button(pin, pull_up=True, bounce_time=self.bounce_time)
                    btn.when_pressed = self._create_callback(event)
                    self._buttons[name] = btn

            self._hardware_active = True
            logger.info("GPIOInputHandler initialized successfully with gpiozero.")

        except (ImportError, Exception) as ex:
            self._hardware_active = False
            logger.warning(
                f"GPIO hardware not available ({ex}). "
                "Running in fallback simulation/emulation mode."
            )

    def _create_callback(self, event: InputEvent):
        """Creates a non-blocking closure callback for gpiozero interrupt."""
        def callback():
            self.inject_event(event)
        return callback

    def inject_event(self, event: InputEvent, char: Optional[str] = None) -> bool:
        """
        Pushes an input event into the event queue (thread-safe).
        Used by GPIO callbacks, USB keyboards, or unit test simulation.
        """
        try:
            self._event_queue.put_nowait((event, char))
            return True
        except queue.Full:
            try:
                self._event_queue.get_nowait()
                self._event_queue.put_nowait((event, char))
                return True
            except queue.Empty:
                return False

    def inject_char(self, char: str) -> bool:
        """Helper to push a typed character from USB keyboard."""
        self._last_char = char
        return self.inject_event(InputEvent.CHAR, char=char)

    def get_event(self) -> Optional[InputEvent]:
        """
        Non-blocking retrieval of the next input event.
        Returns None if queue is empty.
        """
        try:
            item = self._event_queue.get_nowait()
            event, char = item
            self._last_char = char
            return event
        except queue.Empty:
            return None

    def get_last_char(self) -> Optional[str]:
        """Retrieves the character associated with the most recent CHAR event."""
        return self._last_char

    def clear(self) -> None:
        """Flushes all pending input events from the queue."""
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except queue.Empty:
                break
        self._last_char = None

    @property
    def is_hardware_active(self) -> bool:
        """Returns True if physical GPIO buttons are active."""
        return self._hardware_active

    def close(self) -> None:
        """Releases GPIO and keyboard listener resources."""
        self._keyboard_listener.running = False
        for btn in self._buttons.values():
            try:
                btn.close()
            except Exception:
                pass
        self._buttons.clear()
