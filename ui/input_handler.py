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
    Supports physical USB keyboards, 2.4GHz wireless dongles, and console terminal input.
    """

    def __init__(self, handler: 'GPIOInputHandler'):
        super().__init__(name="REIKeyboardListener", daemon=True)
        self.handler = handler
        self.running = True
        self._shift_pressed = False
        self._devices: Dict[str, Any] = {}
        self._last_scan_time: float = 0.0
        self._old_termios = None

        # Build scancode lookup table using evdev ecodes
        self._scancode_char_map: Dict[int, Tuple[str, str]] = {}
        self._scancode_event_map: Dict[int, InputEvent] = {}
        self._init_scancode_maps()
        self._init_terminal_mode()

    def _init_scancode_maps(self) -> None:
        """Populates integer scancode maps for fast evdev lookups."""
        try:
            from evdev import ecodes
        except ImportError:
            return

        # Letters A-Z
        letters = [
            (ecodes.KEY_A, "a", "A"), (ecodes.KEY_B, "b", "B"), (ecodes.KEY_C, "c", "C"),
            (ecodes.KEY_D, "d", "D"), (ecodes.KEY_E, "e", "E"), (ecodes.KEY_F, "f", "F"),
            (ecodes.KEY_G, "g", "G"), (ecodes.KEY_H, "h", "H"), (ecodes.KEY_I, "i", "I"),
            (ecodes.KEY_J, "j", "J"), (ecodes.KEY_K, "k", "K"), (ecodes.KEY_L, "l", "L"),
            (ecodes.KEY_M, "m", "M"), (ecodes.KEY_N, "n", "N"), (ecodes.KEY_O, "o", "O"),
            (ecodes.KEY_P, "p", "P"), (ecodes.KEY_Q, "q", "Q"), (ecodes.KEY_R, "r", "R"),
            (ecodes.KEY_S, "s", "S"), (ecodes.KEY_T, "t", "T"), (ecodes.KEY_U, "u", "U"),
            (ecodes.KEY_V, "v", "V"), (ecodes.KEY_W, "w", "W"), (ecodes.KEY_X, "x", "X"),
            (ecodes.KEY_Y, "y", "Y"), (ecodes.KEY_Z, "z", "Z"),
        ]
        for code, norm, shift in letters:
            self._scancode_char_map[code] = (norm, shift)

        # Numbers 0-9
        numbers = [
            (ecodes.KEY_1, "1", "!"), (ecodes.KEY_2, "2", "@"), (ecodes.KEY_3, "3", "#"),
            (ecodes.KEY_4, "4", "$"), (ecodes.KEY_5, "5", "%"), (ecodes.KEY_6, "6", "^"),
            (ecodes.KEY_7, "7", "&"), (ecodes.KEY_8, "8", "*"), (ecodes.KEY_9, "9", "("),
            (ecodes.KEY_0, "0", ")"),
        ]
        for code, norm, shift in numbers:
            self._scancode_char_map[code] = (norm, shift)

        # Keypad digits & symbols
        keypad = [
            (ecodes.KEY_KP0, "0", "0"), (ecodes.KEY_KP1, "1", "1"), (ecodes.KEY_KP2, "2", "2"),
            (ecodes.KEY_KP3, "3", "3"), (ecodes.KEY_KP4, "4", "4"), (ecodes.KEY_KP5, "5", "5"),
            (ecodes.KEY_KP6, "6", "6"), (ecodes.KEY_KP7, "7", "7"), (ecodes.KEY_KP8, "8", "8"),
            (ecodes.KEY_KP9, "9", "9"), (ecodes.KEY_KPDOT, ".", "."),
            (ecodes.KEY_KPPLUS, "+", "+"), (ecodes.KEY_KPMINUS, "-", "-"),
            (ecodes.KEY_KPASTERISK, "*", "*"), (ecodes.KEY_KPSLASH, "/", "/"),
        ]
        for code, norm, shift in keypad:
            self._scancode_char_map[code] = (norm, shift)

        # Punctuation and Symbols
        symbols = [
            (ecodes.KEY_MINUS, "-", "_"), (ecodes.KEY_EQUAL, "=", "+"),
            (ecodes.KEY_SPACE, " ", " "), (ecodes.KEY_DOT, ".", ">"),
            (ecodes.KEY_COMMA, ",", "<"), (ecodes.KEY_SLASH, "/", "?"),
            (ecodes.KEY_BACKSLASH, "\\", "|"), (ecodes.KEY_SEMICOLON, ";", ":"),
            (ecodes.KEY_APOSTROPHE, "'", "\""), (ecodes.KEY_GRAVE, "`", "~"),
            (ecodes.KEY_LEFTBRACE, "[", "{"), (ecodes.KEY_RIGHTBRACE, "]", "}"),
        ]
        for code, norm, shift in symbols:
            self._scancode_char_map[code] = (norm, shift)

        # Control and Action Keys
        self._scancode_event_map[ecodes.KEY_ENTER] = InputEvent.ENTER
        self._scancode_event_map[ecodes.KEY_KPENTER] = InputEvent.ENTER
        self._scancode_event_map[ecodes.KEY_BACKSPACE] = InputEvent.BACKSPACE
        self._scancode_event_map[ecodes.KEY_DELETE] = InputEvent.BACKSPACE
        self._scancode_event_map[ecodes.KEY_ESC] = InputEvent.ESCAPE
        self._scancode_event_map[ecodes.KEY_UP] = InputEvent.UP
        self._scancode_event_map[ecodes.KEY_DOWN] = InputEvent.DOWN
        self._scancode_event_map[ecodes.KEY_LEFT] = InputEvent.LEFT
        self._scancode_event_map[ecodes.KEY_RIGHT] = InputEvent.RIGHT
        self._scancode_event_map[ecodes.KEY_TAB] = InputEvent.KEY2
        self._scancode_event_map[ecodes.KEY_F1] = InputEvent.KEY1
        self._scancode_event_map[ecodes.KEY_F2] = InputEvent.KEY2
        self._scancode_event_map[ecodes.KEY_F3] = InputEvent.KEY3

    def _init_terminal_mode(self) -> None:
        """Sets non-canonical cbreak mode on stdin for immediate key capture."""
        if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            try:
                import termios, tty
                self._old_termios = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
                logger.info("Stdin terminal cbreak mode enabled.")
            except Exception as ex:
                logger.debug(f"Terminal cbreak mode not applicable: {ex}")

    def _scan_evdev_devices(self) -> None:
        """Discovers new keyboard devices and keeps them open."""
        try:
            import evdev
            from evdev import ecodes
        except ImportError:
            return

        now = os.times().elapsed if hasattr(os, "times") else 0
        current_paths = set(evdev.list_devices())

        # Close removed devices
        for path in list(self._devices.keys()):
            if path not in current_paths:
                try:
                    self._devices[path].close()
                except Exception:
                    pass
                del self._devices[path]
                logger.info(f"Keyboard disconnected: {path}")

        # Scan new devices
        for path in current_paths:
            if path not in self._devices:
                try:
                    dev = evdev.InputDevice(path)
                    caps = dev.capabilities()
                    if ecodes.EV_KEY in caps:
                        key_caps = caps[ecodes.EV_KEY]
                        if ecodes.KEY_A in key_caps or ecodes.KEY_ENTER in key_caps or ecodes.KEY_SPACE in key_caps:
                            self._devices[path] = dev
                            logger.info(f"Attached keyboard device: {dev.name} ({path})")
                except Exception as open_ex:
                    logger.debug(f"Could not open device {path}: {open_ex}")

    def _process_evdev_event(self, event) -> None:
        """Processes an evdev key event."""
        try:
            from evdev import ecodes
        except ImportError:
            return

        code = event.code
        val = event.value  # 0=up, 1=down, 2=hold

        # Shift tracking
        if code in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
            self._shift_pressed = (val > 0)
            return

        # Handle keydown and keyhold
        if val in (1, 2):
            if code in self._scancode_event_map:
                self.handler.inject_event(self._scancode_event_map[code])
            elif code in self._scancode_char_map:
                norm_char, shift_char = self._scancode_char_map[code]
                char = shift_char if self._shift_pressed else norm_char
                self.handler.inject_char(char)

    def _process_stdin_bytes(self, data: bytes) -> None:
        """Parses raw bytes from stdin."""
        if not data:
            return

        # Special escape sequences
        if data == b'\r' or data == b'\n':
            self.handler.inject_event(InputEvent.ENTER)
        elif data in (b'\x7f', b'\x08'):
            self.handler.inject_event(InputEvent.BACKSPACE)
        elif data == b'\x1b':
            self.handler.inject_event(InputEvent.ESCAPE)
        elif data == b'\x1b[A':
            self.handler.inject_event(InputEvent.UP)
        elif data == b'\x1b[B':
            self.handler.inject_event(InputEvent.DOWN)
        elif data == b'\x1b[C':
            self.handler.inject_event(InputEvent.RIGHT)
        elif data == b'\x1b[D':
            self.handler.inject_event(InputEvent.LEFT)
        elif data == b'\t':
            self.handler.inject_event(InputEvent.KEY2)
        else:
            try:
                decoded = data.decode("utf-8", errors="ignore")
                for ch in decoded:
                    if ch in ('\r', '\n'):
                        self.handler.inject_event(InputEvent.ENTER)
                    elif ch.isprintable() and ord(ch) >= 32:
                        self.handler.inject_char(ch)
            except Exception:
                pass

    def run(self) -> None:
        """Continuously monitors evdev devices and stdin for key inputs."""
        last_scan = 0.0

        while self.running:
            now = os.times().elapsed if hasattr(os, "times") else 0
            if (now - last_scan) > 2.0 or len(self._devices) == 0:
                self._scan_evdev_devices()
                last_scan = now

            r_fds = []
            device_map = {}

            # Add evdev devices to select list
            for path, dev in list(self._devices.items()):
                try:
                    fd = dev.fd
                    r_fds.append(fd)
                    device_map[fd] = dev
                except Exception:
                    pass

            # Add stdin to select list
            has_stdin = False
            if sys.stdin and not sys.stdin.closed:
                try:
                    s_fd = sys.stdin.fileno()
                    r_fds.append(s_fd)
                    has_stdin = True
                except Exception:
                    pass

            if not r_fds:
                select.select([], [], [], 0.05)
                continue

            try:
                r_ready, _, _ = select.select(r_fds, [], [], 0.05)
            except (ValueError, OSError):
                select.select([], [], [], 0.05)
                continue

            for fd in r_ready:
                if has_stdin and fd == sys.stdin.fileno():
                    try:
                        data = os.read(fd, 64)
                        if data:
                            self._process_stdin_bytes(data)
                    except Exception:
                        pass
                elif fd in device_map:
                    dev = device_map[fd]
                    try:
                        for ev in dev.read():
                            if ev.type == 1:  # EV_KEY
                                self._process_evdev_event(ev)
                    except BlockingIOError:
                        pass
                    except (OSError, Exception) as dev_ex:
                        logger.debug(f"Device read error on {dev.path}: {dev_ex}")
                        try:
                            dev.close()
                        except Exception:
                            pass
                        self._devices.pop(dev.path, None)

    def close(self) -> None:
        """Cleans up devices and restores terminal settings."""
        self.running = False
        for dev in list(self._devices.values()):
            try:
                dev.close()
            except Exception:
                pass
        self._devices.clear()

        if self._old_termios and sys.stdin and not sys.stdin.closed:
            try:
                import termios
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_termios)
            except Exception:
                pass


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
