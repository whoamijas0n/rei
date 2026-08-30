"""
OmniDiag Hub - GPIO Input Handler
Non-blocking GPIO event capture for Waveshare 1.3" OLED HAT (SH1106).
Maps 5-way joystick and 3 physical buttons to a thread-safe event queue using gpiozero.
"""

from enum import Enum, auto
import logging
import queue
from typing import Optional, Dict, Any

logger = logging.getLogger("OmniDiag.UI.Input")


class InputEvent(Enum):
    """Enumeration of hardware input events."""
    UP = auto()
    DOWN = auto()
    LEFT = auto()
    RIGHT = auto()
    PRESS = auto()   # Joystick Center Click
    KEY1 = auto()    # Physical Key 1 (Top / Back)
    KEY2 = auto()    # Physical Key 2 (Middle / Action)
    KEY3 = auto()    # Physical Key 3 (Bottom / Select)
    BACK = auto()    # Aliased convenience for Key 1


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


class GPIOInputHandler:
    """
    Manages physical hardware interrupts via gpiozero and pushes
    sanitized, debounced events to an asynchronous FIFO queue.
    """

    def __init__(self, pin_config: Optional[Dict[str, int]] = None, bounce_time: float = 0.08):
        self.pins = pin_config or DEFAULT_PINS
        self.bounce_time = bounce_time
        self._event_queue: queue.Queue[InputEvent] = queue.Queue(maxsize=32)
        self._buttons: Dict[str, Any] = {}
        self._hardware_active = False

        self._initialize_gpio()

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
                    # Waveshare 1.3" OLED HAT buttons are active LOW (pull_up=True)
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

    def inject_event(self, event: InputEvent) -> bool:
        """
        Pushes an input event into the event queue (thread-safe).
        Used by GPIO callbacks or PC keyboard simulation/testing.
        """
        try:
            self._event_queue.put_nowait(event)
            return True
        except queue.Full:
            # Drop old event to avoid buffer bloat
            try:
                self._event_queue.get_nowait()
                self._event_queue.put_nowait(event)
                return True
            except queue.Empty:
                return False

    def get_event(self) -> Optional[InputEvent]:
        """
        Non-blocking retrieval of the next input event.
        Returns None if queue is empty.
        """
        try:
            return self._event_queue.get_nowait()
        except queue.Empty:
            return None

    def clear(self) -> None:
        """Flushes all pending input events from the queue."""
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except queue.Empty:
                break

    @property
    def is_hardware_active(self) -> bool:
        """Returns True if physical GPIO buttons are active."""
        return self._hardware_active

    def close(self) -> None:
        """Releases GPIO resources."""
        for btn in self._buttons.values():
            try:
                btn.close()
            except Exception:
                pass
        self._buttons.clear()
