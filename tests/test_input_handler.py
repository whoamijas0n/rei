"""
Tests for ui/input_handler.py: queue API, stdin decoding and keyboard status.
"""

import unittest
from unittest.mock import patch

from ui.input_handler import GPIOInputHandler, InputEvent, USBKeyboardListener


class _StoppedListener:
    """Test double that keeps GPIOInputHandler from spawning a real thread."""

    def __init__(self, handler, layout=None):
        self.handler = handler
        self.layout = layout or "us"
        self.closed = False

    @property
    def has_keyboard(self):
        return False

    @property
    def keyboard_names(self):
        return ()

    @property
    def has_any_input(self):
        return False

    def inject_event(self, event):
        return self.handler.inject_event(event)

    def inject_char(self, char):
        return self.handler.inject_char(char)

    def start(self):
        pass

    def close(self):
        self.closed = True


class TestEventQueue(unittest.TestCase):

    def setUp(self):
        with patch.object(GPIOInputHandler, "_initialize_gpio"), \
                patch("ui.input_handler.USBKeyboardListener", _StoppedListener):
            self.handler = GPIOInputHandler(keyboard_layout="us")

    def tearDown(self):
        self.handler.close()

    def test_get_event_with_char_returns_event_and_char(self):
        self.handler.inject_char("a")
        item = self.handler.get_event_with_char()
        self.assertIsNotNone(item)
        event, char = item
        self.assertEqual(event, InputEvent.CHAR)
        self.assertEqual(char, "a")

    def test_get_event_with_char_empty_queue(self):
        self.assertIsNone(self.handler.get_event_with_char())

    def test_plain_events_have_no_char(self):
        self.handler.inject_event(InputEvent.KEY3)
        event, char = self.handler.get_event_with_char()
        self.assertEqual(event, InputEvent.KEY3)
        self.assertIsNone(char)

    def test_get_event_backwards_compatible(self):
        self.handler.inject_char("z")
        event = self.handler.get_event()
        self.assertEqual(event, InputEvent.CHAR)
        self.assertEqual(self.handler.get_last_char(), "z")

    def test_queue_overflow_does_not_raise(self):
        for index in range(300):
            self.handler.inject_event(InputEvent.CHAR, char=str(index % 10))
        # Draining yields exactly the queue capacity.
        drained = 0
        while self.handler.get_event_with_char() is not None:
            drained += 1
        self.assertEqual(drained, 256)

    def test_clear_flushes_queue(self):
        self.handler.inject_event(InputEvent.UP)
        self.handler.clear()
        self.assertIsNone(self.handler.get_event_with_char())


class TestStdinDecoding(unittest.TestCase):
    """Exercises USBKeyboardListener._process_stdin_bytes without a thread."""

    def setUp(self):
        self.handler = GPIOInputHandler.__new__(GPIOInputHandler)
        self.handler.pins = {}
        self.handler.bounce_time = 0.08
        self.handler._event_queue = __import__("queue").Queue(maxsize=256)
        self.handler._buttons = {}
        self.handler._hardware_active = False
        self.handler._last_char = None
        self.listener = USBKeyboardListener(self.handler, layout="us")

    def tearDown(self):
        self.listener.close()

    def _drain_events(self):
        events = []
        while True:
            item = self.handler.get_event_with_char()
            if item is None:
                break
            events.append(item)
        return events

    def test_printable_characters(self):
        self.listener._process_stdin_bytes(b"ab1")
        events = self._drain_events()
        self.assertEqual([char for _, char in events], ["a", "b", "1"])
        self.assertTrue(all(event == InputEvent.CHAR for event, _ in events))

    def test_enter_and_backspace(self):
        self.listener._process_stdin_bytes(b"\r")
        self.listener._process_stdin_bytes(b"\x7f")
        events = self._drain_events()
        self.assertEqual([event for event, _ in events], [InputEvent.ENTER, InputEvent.BACKSPACE])

    def test_arrow_escape_sequences(self):
        self.listener._process_stdin_bytes(b"\x1b[A")
        self.listener._process_stdin_bytes(b"\x1b[B")
        self.listener._process_stdin_bytes(b"\x1b[C")
        self.listener._process_stdin_bytes(b"\x1b[D")
        events = self._drain_events()
        self.assertEqual(
            [event for event, _ in events],
            [InputEvent.UP, InputEvent.DOWN, InputEvent.RIGHT, InputEvent.LEFT],
        )

    def test_escape_and_tab(self):
        self.listener._process_stdin_bytes(b"\x1b")
        self.listener._process_stdin_bytes(b"\t")
        events = self._drain_events()
        self.assertEqual([event for event, _ in events], [InputEvent.ESCAPE, InputEvent.KEY2])

    def test_utf8_multibyte_character(self):
        self.listener._process_stdin_bytes("ñ".encode("utf-8"))
        events = self._drain_events()
        self.assertEqual(events, [(InputEvent.CHAR, "ñ")])

    def test_empty_data_is_ignored(self):
        self.listener._process_stdin_bytes(b"")
        self.assertEqual(self._drain_events(), [])


class TestListenerStatus(unittest.TestCase):

    def test_fresh_listener_has_no_keyboard(self):
        handler = GPIOInputHandler.__new__(GPIOInputHandler)
        handler._event_queue = __import__("queue").Queue(maxsize=16)
        handler._last_char = None
        listener = USBKeyboardListener(handler, layout="es")
        try:
            self.assertFalse(listener.has_keyboard)
            self.assertEqual(listener.keyboard_names, ())
            self.assertEqual(listener.layout, "es")
        finally:
            listener.close()

    def test_layout_auto_resolves_without_crash(self):
        handler = GPIOInputHandler.__new__(GPIOInputHandler)
        handler._event_queue = __import__("queue").Queue(maxsize=16)
        handler._last_char = None
        listener = USBKeyboardListener(handler, layout="auto")
        try:
            self.assertIn(listener.layout, ("us", "es", "latam"))
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main()
