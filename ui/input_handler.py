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
from typing import Optional, Dict, Any, Tuple, List

from .keyboard_layouts import (
    KeyboardMap,
    build_key_map,
    detect_system_layout,
    next_layout_name,
    normalize_layout_name,
    resolve_key_binding,
    is_real_keyboard_capabilities,
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
    Supports physical USB keyboards, 2.4GHz wireless dongles, hotplug,
    LATAM/ES/US layouts with AltGr and dead keys, and console terminal input.
    """

    SCAN_INTERVAL_DISCONNECTED_S = 0.5
    SCAN_INTERVAL_CONNECTED_S = 2.0

    def __init__(
        self,
        handler: 'GPIOInputHandler',
        layout: Optional[str] = None,
        ecodes_mod: Any = None,
        evdev_mod: Any = None,
    ):
        super().__init__(name="REIKeyboardListener", daemon=True)
        self.handler = handler
        self.running = True
        self._ecodes_override = ecodes_mod
        self._evdev_override = evdev_mod

        # Modifier & dead-key state
        self._left_shift_pressed = False
        self._right_shift_pressed = False
        self._left_ctrl_pressed = False
        self._right_ctrl_pressed = False
        self._left_alt_pressed = False
        self._right_alt_pressed = False
        self._caps_lock = False
        self._pending_dead_key: Optional[str] = None

        self._devices: Dict[str, Any] = {}
        self._devices_lock = threading.Lock()
        self._layout_lock = threading.Lock()
        self._rescan_event = threading.Event()
        self._old_termios = None
        self._has_interactive_stdin = False

        # Resolve initial keyboard layout and build scancode maps
        self._layout: str = detect_system_layout(configured_layout=layout)
        self._keyboard_map: KeyboardMap = KeyboardMap(layout=self._layout)
        self._scancode_event_map: Dict[int, InputEvent] = {}
        self._repeat_allowed_events = {
            InputEvent.BACKSPACE,
            InputEvent.UP,
            InputEvent.DOWN,
            InputEvent.LEFT,
            InputEvent.RIGHT,
        }
        self._init_scancode_maps()
        self._init_terminal_mode()

    def _get_evdev_modules(self) -> Tuple[Any, Any]:
        """Resolves evdev and ecodes modules lazily (supports dependency injection in tests)."""
        evdev_mod = self._evdev_override
        ecodes_mod = self._ecodes_override
        if evdev_mod is None:
            try:
                import evdev as real_evdev
                evdev_mod = real_evdev
            except ImportError:
                evdev_mod = None
        if ecodes_mod is None:
            if evdev_mod is not None and hasattr(evdev_mod, "ecodes"):
                ecodes_mod = evdev_mod.ecodes
            else:
                try:
                    from evdev import ecodes as real_ecodes
                    ecodes_mod = real_ecodes
                except ImportError:
                    ecodes_mod = None
        return evdev_mod, ecodes_mod

    @property
    def layout(self) -> str:
        """Returns currently active keyboard layout ('latam', 'es', or 'us')."""
        with self._layout_lock:
            return self._layout

    def set_layout(self, layout: str) -> str:
        """Switches physical keyboard scancode translation layout at runtime."""
        normalized = normalize_layout_name(layout)
        _, ecodes_mod = self._get_evdev_modules()
        new_map = build_key_map(normalized, ecodes_mod=ecodes_mod)
        with self._layout_lock:
            self._layout = normalized
            self._keyboard_map = new_map
            self._pending_dead_key = None
        logger.info(f"Keyboard layout set to: {normalized.upper()}")
        return normalized

    def cycle_layout(self) -> str:
        """Cycles active keyboard layout (latam -> es -> us)."""
        nxt = next_layout_name(self.layout)
        return self.set_layout(nxt)

    def request_rescan(self) -> None:
        """Requests an immediate evdev scan on the next listener tick."""
        self._rescan_event.set()

    def has_connected_keyboard(self) -> bool:
        """Thread-safe O(1) check indicating if at least one real USB keyboard is attached."""
        with self._devices_lock:
            return len(self._devices) > 0

    def has_typing_input(self) -> bool:
        """Returns True if a real USB keyboard or interactive terminal stdin is available."""
        return self.has_connected_keyboard() or self._has_interactive_stdin

    def get_connected_keyboard_names(self) -> List[str]:
        """Returns names of currently attached physical keyboards."""
        with self._devices_lock:
            return [getattr(dev, "name", path) for path, dev in self._devices.items()]

    def _reset_modifier_state(self) -> None:
        """Clears modifier and dead-key states (e.g. when a keyboard is unplugged)."""
        self._left_shift_pressed = False
        self._right_shift_pressed = False
        self._left_ctrl_pressed = False
        self._right_ctrl_pressed = False
        self._left_alt_pressed = False
        self._right_alt_pressed = False
        self._pending_dead_key = None

    def _init_scancode_maps(self) -> None:
        """Populates layout keymap and control/navigation event mappings."""
        _, ecodes = self._get_evdev_modules()
        if ecodes is None:
            return

        with self._layout_lock:
            self._keyboard_map = build_key_map(self._layout, ecodes_mod=ecodes)

        control_pairs = [
            ("KEY_ENTER", InputEvent.ENTER),
            ("KEY_KPENTER", InputEvent.ENTER),
            ("KEY_BACKSPACE", InputEvent.BACKSPACE),
            ("KEY_DELETE", InputEvent.BACKSPACE),
            ("KEY_ESC", InputEvent.ESCAPE),
            ("KEY_UP", InputEvent.UP),
            ("KEY_DOWN", InputEvent.DOWN),
            ("KEY_LEFT", InputEvent.LEFT),
            ("KEY_RIGHT", InputEvent.RIGHT),
            ("KEY_TAB", InputEvent.KEY2),
            ("KEY_F1", InputEvent.KEY1),
            ("KEY_F2", InputEvent.KEY2),
            ("KEY_F3", InputEvent.KEY3),
        ]
        for key_name, ev in control_pairs:
            code = getattr(ecodes, key_name, None)
            if code is not None:
                self._scancode_event_map[code] = ev

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

    def _scan_evdev_devices(self) -> None:
        """Discovers real physical keyboard devices and cleans up removed ones."""
        evdev_mod, ecodes_mod = self._get_evdev_modules()
        if evdev_mod is None or ecodes_mod is None:
            return

        try:
            current_paths = set(evdev_mod.list_devices())
        except Exception as scan_err:
            logger.debug(f"Error listing evdev devices: {scan_err}")
            return

        # Close removed devices
        removed_any = False
        with self._devices_lock:
            for path in list(self._devices.keys()):
                if path not in current_paths:
                    try:
                        self._devices[path].close()
                    except Exception:
                        pass
                    del self._devices[path]
                    removed_any = True
                    logger.info(f"Keyboard disconnected: {path}")
            if removed_any and not self._devices:
                self._reset_modifier_state()

        # Snapshot existing paths to avoid holding lock while probing new devices
        with self._devices_lock:
            known_paths = set(self._devices.keys())

        # Scan newly appeared devices
        for path in sorted(current_paths):
            if path in known_paths:
                continue
            dev = None
            keep_open = False
            try:
                dev = evdev_mod.InputDevice(path)
                caps = dev.capabilities()
                dev_name = getattr(dev, "name", path)
                if is_real_keyboard_capabilities(caps, device_name=dev_name, ecodes_mod=ecodes_mod):
                    with self._devices_lock:
                        self._devices[path] = dev
                    keep_open = True
                    logger.info(f"Attached keyboard device: {dev_name} ({path})")
            except Exception as open_ex:
                logger.debug(f"Could not open device {path}: {open_ex}")
            finally:
                if dev is not None and not keep_open:
                    try:
                        dev.close()
                    except Exception:
                        pass

    def _process_evdev_event(self, event: Any) -> None:
        """Processes a single evdev EV_KEY event using active layout and modifiers."""
        _, ecodes = self._get_evdev_modules()
        if ecodes is None:
            return

        code = event.code
        val = event.value  # 0=up, 1=down, 2=hold

        # Modifier tracking
        if code == getattr(ecodes, "KEY_LEFTSHIFT", -1):
            self._left_shift_pressed = (val > 0)
            return
        if code == getattr(ecodes, "KEY_RIGHTSHIFT", -1):
            self._right_shift_pressed = (val > 0)
            return
        if code == getattr(ecodes, "KEY_LEFTCTRL", -1):
            self._left_ctrl_pressed = (val > 0)
            return
        if code == getattr(ecodes, "KEY_RIGHTCTRL", -1):
            self._right_ctrl_pressed = (val > 0)
            return
        if code == getattr(ecodes, "KEY_LEFTALT", -1):
            self._left_alt_pressed = (val > 0)
            return
        if code == getattr(ecodes, "KEY_RIGHTALT", -1):
            self._right_alt_pressed = (val > 0)
            return
        if code == getattr(ecodes, "KEY_CAPSLOCK", -1):
            if val == 1:
                self._caps_lock = not self._caps_lock
            return

        if val not in (1, 2):
            return

        ctrl_active = self._left_ctrl_pressed or self._right_ctrl_pressed
        shift_active = self._left_shift_pressed or self._right_shift_pressed
        altgr_active = self._right_alt_pressed or (ctrl_active and self._left_alt_pressed)

        # Layout cycling shortcut: F4 or Ctrl+Space (keydown only)
        key_f4 = getattr(ecodes, "KEY_F4", -1)
        key_space = getattr(ecodes, "KEY_SPACE", -1)
        if val == 1 and (code == key_f4 or (ctrl_active and code == key_space)):
            self.cycle_layout()
            return

        # Control / navigation event map
        if code in self._scancode_event_map:
            mapped_ev = self._scancode_event_map[code]
            if mapped_ev in (InputEvent.BACKSPACE, InputEvent.ESCAPE, InputEvent.ENTER):
                self._pending_dead_key = None
            if val == 1 or mapped_ev in self._repeat_allowed_events:
                self.handler.inject_event(mapped_ev)
            return

        # Ignore raw Ctrl+key combos (when not Ctrl+Alt = AltGr) so shortcuts don't leak chars
        if ctrl_active and not self._left_alt_pressed:
            return

        with self._layout_lock:
            binding = self._keyboard_map.bindings.get(code)
            pending_dead = self._pending_dead_key

        if binding is None:
            return

        emitted, next_dead = resolve_key_binding(
            binding=binding,
            shift=shift_active,
            caps_lock=self._caps_lock,
            altgr=altgr_active,
            pending_dead=pending_dead,
        )
        with self._layout_lock:
            self._pending_dead_key = next_dead

        for ch in emitted:
            self.handler.inject_char(ch)

    def _process_stdin_bytes(self, data: bytes) -> None:
        """Parses raw bytes from interactive stdin."""
        if not data:
            return

        if data in (b'\r', b'\n'):
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
                    elif ch in ('\x7f', '\x08'):
                        self.handler.inject_event(InputEvent.BACKSPACE)
                    elif ch == '\t':
                        self.handler.inject_event(InputEvent.KEY2)
                    elif ch == '\x1b':
                        self.handler.inject_event(InputEvent.ESCAPE)
                    elif ch.isprintable() and ord(ch) >= 32:
                        self.handler.inject_char(ch)
            except Exception:
                pass

    def run(self) -> None:
        """Continuously monitors evdev devices and stdin for key inputs."""
        last_scan = 0.0

        while self.running:
            now = time.monotonic()
            has_devs = self.has_connected_keyboard()
            scan_interval = self.SCAN_INTERVAL_CONNECTED_S if has_devs else self.SCAN_INTERVAL_DISCONNECTED_S

            if self._rescan_event.is_set() or (now - last_scan) >= scan_interval:
                self._rescan_event.clear()
                self._scan_evdev_devices()
                last_scan = now

            r_fds = []
            device_map: Dict[int, Any] = {}

            # Snapshot evdev FDs safely
            with self._devices_lock:
                dev_items = list(self._devices.items())

            for path, dev in dev_items:
                try:
                    fd = dev.fd
                    if fd is not None and fd >= 0:
                        r_fds.append(fd)
                        device_map[fd] = dev
                except Exception:
                    pass

            # Add stdin ONLY if it is an active interactive TTY
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
                        dev_path = getattr(dev, "path", None)
                        logger.debug(f"Device read error on {dev_path}: {dev_ex}")
                        try:
                            dev.close()
                        except Exception:
                            pass
                        with self._devices_lock:
                            if dev_path and dev_path in self._devices:
                                del self._devices[dev_path]
                            else:
                                for k, v in list(self._devices.items()):
                                    if v is dev:
                                        del self._devices[k]
                            if not self._devices:
                                self._reset_modifier_state()

    def close(self) -> None:
        """Cleans up devices and restores terminal settings."""
        self.running = False
        with self._devices_lock:
            for dev in list(self._devices.values()):
                try:
                    dev.close()
                except Exception:
                    pass
            self._devices.clear()
            self._reset_modifier_state()

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
        self._event_queue: queue.Queue[Tuple[InputEvent, Optional[str]]] = queue.Queue(maxsize=64)
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
        """Helper to push a typed character from USB keyboard (thread-safe)."""
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

    def has_usb_keyboard(self) -> bool:
        """Returns True when at least one real physical USB keyboard is connected."""
        return self._keyboard_listener.has_connected_keyboard()

    def has_typing_input(self) -> bool:
        """Returns True when physical USB keyboard or interactive TTY stdin is available."""
        return self._keyboard_listener.has_typing_input()

    def get_connected_keyboards(self) -> List[str]:
        """Returns names of attached physical keyboards."""
        return self._keyboard_listener.get_connected_keyboard_names()

    @property
    def keyboard_layout(self) -> str:
        """Returns active physical keyboard layout ('latam', 'es', 'us')."""
        return self._keyboard_listener.layout

    def set_keyboard_layout(self, layout: str) -> str:
        """Updates active physical keyboard layout."""
        return self._keyboard_listener.set_layout(layout)

    def cycle_keyboard_layout(self) -> str:
        """Cycles active physical keyboard layout."""
        return self._keyboard_listener.cycle_layout()

    def request_keyboard_rescan(self) -> None:
        """Triggers an immediate evdev scan for newly attached keyboards."""
        self._keyboard_listener.request_rescan()

    def close(self) -> None:
        """Releases GPIO and keyboard listener resources."""
        self._keyboard_listener.close()
        for btn in self._buttons.values():
            try:
                btn.close()
            except Exception:
                pass
        self._buttons.clear()
