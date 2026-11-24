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
import time
from typing import Optional, Dict, Any, Tuple

from .keyboard_layouts import (
    DEAD_LITERALS,
    build_key_map,
    compose_dead_key,
    detect_system_layout,
    is_keyboard_capabilities,
    normalize_layout_name,
)

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
    and interactive terminal stdin without blocking the main 30 FPS UI loop.
    Supports physical USB keyboards, 2.4GHz wireless dongles, and console terminal input.
    """

    def __init__(self, handler: 'GPIOInputHandler', layout: Optional[str] = None):
        super().__init__(name="REIKeyboardListener", daemon=True)
        self.handler = handler
        self.running = True
        self._caps_lock = False
        self._pressed_modifiers: set = set()
        self._pending_dead: Optional[str] = None
        self._devices: Dict[str, Any] = {}
        self._devices_lock = threading.Lock()
        self._last_scan_time: float = 0.0
        self._old_termios = None
        self._has_interactive_stdin = False

        self._ecodes = None
        self._key_map = None
        self._shift_codes = set()
        self._ctrl_codes = set()
        self._left_alt_codes = set()
        self._altgr_codes = set()
        self._gui_codes = set()

        selected_layout = normalize_layout_name(layout or "auto")
        if not layout or selected_layout == "auto" or str(layout).strip().lower() == "auto":
            selected_layout = detect_system_layout()
        self.layout = selected_layout

        self._init_key_maps()
        self._init_terminal_mode()

    # ------------------------------------------------------------------
    # Initialization helpers
    # ------------------------------------------------------------------
    def _init_key_maps(self) -> None:
        """Loads evdev scancode maps for the configured layout."""
        try:
            from evdev import ecodes
        except ImportError:
            logger.warning(
                "python-evdev no está disponible: no se podrá leer el teclado USB físico."
            )
            return

        self._ecodes = ecodes
        self._key_map = build_key_map(self.layout)

        self._shift_codes = {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}
        self._ctrl_codes = {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL}
        self._left_alt_codes = {ecodes.KEY_LEFTALT}
        self._altgr_codes = {ecodes.KEY_RIGHTALT}
        self._gui_codes = {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA}

        logger.info(f"Layout de teclado USB activo: {self.layout}")

    def _init_terminal_mode(self) -> None:
        """Sets non-canonical cbreak mode on stdin for immediate key capture if interactive."""
        if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            try:
                import termios, tty
                self._old_termios = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
                self._has_interactive_stdin = True
                logger.info("Stdin terminal cbreak mode enabled for interactive session.")
            except Exception as ex:
                self._has_interactive_stdin = False
                logger.debug(f"Terminal cbreak mode not applicable: {ex}")
        else:
            self._has_interactive_stdin = False

    # ------------------------------------------------------------------
    # Public status API
    # ------------------------------------------------------------------
    @property
    def has_keyboard(self) -> bool:
        """True when at least one real USB keyboard is attached."""
        with self._devices_lock:
            return bool(self._devices)

    @property
    def keyboard_names(self) -> Tuple[str, ...]:
        """Names of the currently attached keyboard devices."""
        with self._devices_lock:
            return tuple(getattr(dev, "name", "keyboard") for dev in self._devices.values())

    @property
    def has_any_input(self) -> bool:
        """True when evdev keyboards or an interactive terminal are available."""
        return self.has_keyboard or self._has_interactive_stdin

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------
    def _scan_evdev_devices(self) -> None:
        """Discovers new keyboard devices and keeps them open."""
        if self._ecodes is None:
            return

        try:
            import evdev
        except ImportError:
            return

        try:
            current_paths = set(evdev.list_devices())
        except Exception as scan_err:
            logger.debug(f"Error listing evdev devices: {scan_err}")
            return

        # Close removed devices
        with self._devices_lock:
            removed = [path for path in self._devices if path not in current_paths]
            removed_devices = [self._devices.pop(path) for path in removed]

        for path, dev in zip(removed, removed_devices):
            try:
                dev.close()
            except Exception:
                pass
            logger.info(f"Keyboard disconnected: {path}")

        # Scan new devices
        for path in current_paths:
            with self._devices_lock:
                already_open = path in self._devices
            if already_open:
                continue

            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                if is_keyboard_capabilities(caps, getattr(dev, "name", "")):
                    with self._devices_lock:
                        self._devices[path] = dev
                    logger.info(f"Attached keyboard device: {dev.name} ({path})")
                else:
                    dev.close()
            except Exception as open_ex:
                logger.debug(f"Could not open device {path}: {open_ex}")

    # ------------------------------------------------------------------
    # evdev event decoding
    # ------------------------------------------------------------------
    def _update_modifier_state(self, code: int, val: int) -> bool:
        """Updates modifier/lock state. Returns True if the key was a modifier."""
        ecodes = self._ecodes
        if ecodes is None:
            return False

        modifier_codes = (
            self._shift_codes | self._ctrl_codes | self._left_alt_codes |
            self._altgr_codes | self._gui_codes
        )
        if code in modifier_codes:
            if val == 0:
                self._pressed_modifiers.discard(code)
            else:
                self._pressed_modifiers.add(code)
            return True

        if code == ecodes.KEY_CAPSLOCK:
            if val == 1:
                self._caps_lock = not self._caps_lock
            return True

        return False

    def _inject_action(self, event: InputEvent) -> None:
        """Injects a non-character action, flushing a pending dead key first."""
        if self._pending_dead:
            if event != InputEvent.BACKSPACE:
                literal = DEAD_LITERALS.get(self._pending_dead, "")
                if literal:
                    self.handler.inject_char(literal)
            self._pending_dead = None
        self.handler.inject_event(event)

    def _process_evdev_event(self, event) -> None:
        """Processes an evdev key event."""
        ecodes = self._ecodes
        if ecodes is None:
            return

        code = event.code
        val = event.value  # 0=up, 1=down, 2=hold

        if self._update_modifier_state(code, val):
            return

        if val not in (1, 2):
            return

        event_map = {
            ecodes.KEY_ENTER: InputEvent.ENTER,
            ecodes.KEY_KPENTER: InputEvent.ENTER,
            ecodes.KEY_BACKSPACE: InputEvent.BACKSPACE,
            ecodes.KEY_DELETE: InputEvent.BACKSPACE,
            ecodes.KEY_ESC: InputEvent.ESCAPE,
            ecodes.KEY_UP: InputEvent.UP,
            ecodes.KEY_DOWN: InputEvent.DOWN,
            ecodes.KEY_LEFT: InputEvent.LEFT,
            ecodes.KEY_RIGHT: InputEvent.RIGHT,
            ecodes.KEY_TAB: InputEvent.KEY2,
        }
        if hasattr(ecodes, "KEY_F1"):
            event_map[ecodes.KEY_F1] = InputEvent.KEY1
            event_map[ecodes.KEY_F2] = InputEvent.KEY2
            event_map[ecodes.KEY_F3] = InputEvent.KEY3

        if code in event_map:
            self._inject_action(event_map[code])
            return

        if self._key_map is None:
            return

        binding = self._key_map.bindings.get(code)
        if binding is None:
            return

        shift_pressed = bool(self._pressed_modifiers & self._shift_codes)
        ctrl_held = bool(self._pressed_modifiers & self._ctrl_codes)
        gui_held = bool(self._pressed_modifiers & self._gui_codes)
        left_alt_held = bool(self._pressed_modifiers & self._left_alt_codes)
        altgr_held = bool(self._pressed_modifiers & self._altgr_codes)

        # Ctrl/Alt/Gui combinations are shortcuts, not password characters.
        if (ctrl_held or gui_held or left_alt_held) and not altgr_held:
            self._pending_dead = None
            return

        if altgr_held:
            dead_name = binding.altgr_shift_dead if shift_pressed else binding.altgr_dead
            char = binding.altgr_shift if shift_pressed else binding.altgr
            if not dead_name and not char:
                return
        else:
            is_letter = (
                len(binding.normal) == 1
                and binding.normal.isalpha()
                and len(binding.shift) == 1
                and binding.shift.isalpha()
            )
            shifted = shift_pressed ^ (self._caps_lock if is_letter else False)
            dead_name = binding.shift_dead if shifted else binding.normal_dead
            char = binding.shift if shifted else binding.normal

        if dead_name:
            # Holding a dead key must not enqueue it repeatedly.
            if val == 2:
                return
            if self._pending_dead:
                literal = DEAD_LITERALS.get(self._pending_dead, "")
                if literal:
                    self.handler.inject_char(literal)
            self._pending_dead = dead_name
            return

        if not char:
            return

        if self._pending_dead:
            composed = compose_dead_key(self._pending_dead, char)
            self._pending_dead = None
            if composed:
                self.handler.inject_char(composed)
            return

        self.handler.inject_char(char)

    def _process_stdin_bytes(self, data: bytes) -> None:
        """Parses raw bytes from stdin."""
        if not data:
            return

        # Special escape sequences (check longest first)
        if data in (b'\r', b'\n'):
            self.handler.inject_event(InputEvent.ENTER)
        elif data in (b'\x7f', b'\x08'):
            self.handler.inject_event(InputEvent.BACKSPACE)
        elif data == b'\x1b[A':
            self.handler.inject_event(InputEvent.UP)
        elif data == b'\x1b[B':
            self.handler.inject_event(InputEvent.DOWN)
        elif data == b'\x1b[C':
            self.handler.inject_event(InputEvent.RIGHT)
        elif data == b'\x1b[D':
            self.handler.inject_event(InputEvent.LEFT)
        elif data == b'\x1b':
            self.handler.inject_event(InputEvent.ESCAPE)
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

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Continuously monitors evdev devices and stdin for key inputs."""
        last_scan = 0.0

        while self.running:
            now = time.monotonic()
            with self._devices_lock:
                device_count = len(self._devices)
            if (now - last_scan) > 2.0 or device_count == 0:
                self._scan_evdev_devices()
                last_scan = now

            with self._devices_lock:
                devices = list(self._devices.items())

            r_fds = []
            device_map = {}

            for path, dev in devices:
                try:
                    fd = dev.fd
                    r_fds.append(fd)
                    device_map[fd] = dev
                except Exception:
                    pass

            # Add stdin to select list ONLY if it is an active interactive TTY
            has_stdin = False
            if self._has_interactive_stdin and sys.stdin and not sys.stdin.closed:
                try:
                    s_fd = sys.stdin.fileno()
                    r_fds.append(s_fd)
                    has_stdin = True
                except Exception:
                    self._has_interactive_stdin = False

            if not r_fds:
                time.sleep(0.05)
                continue

            try:
                r_ready, _, _ = select.select(r_fds, [], [], 0.05)
            except (ValueError, OSError):
                time.sleep(0.05)
                continue

            for fd in r_ready:
                if has_stdin and sys.stdin and fd == sys.stdin.fileno():
                    try:
                        data = os.read(fd, 64)
                        if data:
                            self._process_stdin_bytes(data)
                        else:
                            # EOF on stdin, disable polling it
                            self._has_interactive_stdin = False
                    except Exception:
                        self._has_interactive_stdin = False
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
                        with self._devices_lock:
                            current = self._devices.get(dev.path)
                            if current is dev:
                                self._devices.pop(dev.path, None)

    def close(self) -> None:
        """Cleans up devices and restores terminal settings."""
        self.running = False
        with self._devices_lock:
            devices = list(self._devices.values())
            self._devices.clear()
        for dev in devices:
            try:
                dev.close()
            except Exception:
                pass

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

    def __init__(
        self,
        pin_config: Optional[Dict[str, int]] = None,
        bounce_time: float = 0.08,
        keyboard_layout: Optional[str] = None,
    ):
        self.pins = pin_config or DEFAULT_PINS
        self.bounce_time = bounce_time
        self._event_queue: queue.Queue[Tuple[InputEvent, Optional[str]]] = queue.Queue(maxsize=256)
        self._buttons: Dict[str, Any] = {}
        self._hardware_active = False
        self._last_char: Optional[str] = None

        # 1. Initialize physical GPIO buttons
        self._initialize_gpio()

        # 2. Start non-blocking USB Keyboard Listener
        self._keyboard_listener = USBKeyboardListener(self, layout=keyboard_layout)
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

    def get_event_with_char(self) -> Optional[Tuple[InputEvent, Optional[str]]]:
        """Non-blocking retrieval of the next event together with its character."""
        try:
            return self._event_queue.get_nowait()
        except queue.Empty:
            return None

    def get_event(self) -> Optional[InputEvent]:
        """
        Non-blocking retrieval of the next input event.
        Returns None if queue is empty.
        """
        item = self.get_event_with_char()
        if item is None:
            return None
        event, char = item
        self._last_char = char
        return event

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

    @property
    def has_usb_keyboard(self) -> bool:
        """Returns True when a real USB keyboard is attached."""
        return self._keyboard_listener.has_keyboard

    @property
    def has_text_input(self) -> bool:
        """Returns True when a USB keyboard or an interactive TTY can type text."""
        return self._keyboard_listener.has_any_input

    @property
    def keyboard_names(self) -> Tuple[str, ...]:
        """Names of the attached USB keyboards."""
        return self._keyboard_listener.keyboard_names

    @property
    def keyboard_layout(self) -> str:
        """Active keyboard layout identifier."""
        return self._keyboard_listener.layout

    def close(self) -> None:
        """Releases GPIO and keyboard listener resources."""
        self._keyboard_listener.close()
        for btn in self._buttons.values():
            try:
                btn.close()
            except Exception:
                pass
        self._buttons.clear()
