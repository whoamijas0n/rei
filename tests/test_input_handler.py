"""
Pruebas para USBKeyboardListener y GPIOInputHandler:
- Detección real y hotplug sin bloquear
- Supresión de autorepeat en teclas de acción (ENTER/ESC/TAB/F1..F4)
- Seguridad de hilos en inject_char / get_event / get_last_char
- Parseo de stdin y cambio de distribución
"""

import types
import unittest

from tests.test_keyboard_layouts import make_fake_ecodes
from ui.input_handler import GPIOInputHandler, InputEvent, USBKeyboardListener


class _FakeDevice:
    def __init__(self, path, name, caps, fd=10):
        self.path = path
        self.name = name
        self._caps = caps
        self.fd = fd
        self.closed = False

    def capabilities(self):
        return self._caps

    def close(self):
        self.closed = True


class _FakeEvdevModule:
    def __init__(self, devices):
        self._devices = {d.path: d for d in devices}
        self.ecodes = make_fake_ecodes()

    def list_devices(self):
        return list(self._devices.keys())

    def InputDevice(self, path):
        return self._devices[path]


def _make_handler_no_hw():
    """Crea GPIOInputHandler sin hardware real ni hilo daemon activo."""
    handler = GPIOInputHandler.__new__(GPIOInputHandler)
    import queue
    handler.pins = {}
    handler.bounce_time = 0.08
    handler._event_queue = queue.Queue(maxsize=64)
    handler._buttons = {}
    handler._hardware_active = False
    handler._last_char = None
    return handler


class TestInputHandler(unittest.TestCase):

    def test_scan_evdev_attaches_only_real_keyboard_and_handles_disconnect(self):
        ecodes = make_fake_ecodes()
        full_keys = [getattr(ecodes, f"KEY_{c}") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"] + [
            ecodes.KEY_ENTER,
            ecodes.KEY_SPACE,
            ecodes.KEY_BACKSPACE,
            ecodes.KEY_LEFTSHIFT,
        ]
        dev_hdmi = _FakeDevice("/dev/input/event0", "vc4-hdmi", {ecodes.EV_KEY: [ecodes.KEY_ENTER]})
        dev_cons = _FakeDevice(
            "/dev/input/event4",
            "BJX 2.4G Receiver Consumer Control",
            {ecodes.EV_KEY: [ecodes.KEY_ENTER, ecodes.KEY_ESC]},
        )
        dev_kbd = _FakeDevice("/dev/input/event5", "BJX 2.4G Receiver", {ecodes.EV_KEY: full_keys})

        fake_evdev = _FakeEvdevModule([dev_hdmi, dev_cons, dev_kbd])
        handler = _make_handler_no_hw()
        listener = USBKeyboardListener(
            handler,
            layout="latam",
            ecodes_mod=ecodes,
            evdev_mod=fake_evdev,
        )
        handler._keyboard_listener = listener

        listener._scan_evdev_devices()
        self.assertTrue(handler.has_usb_keyboard())
        self.assertEqual(handler.get_connected_keyboards(), ["BJX 2.4G Receiver"])
        self.assertTrue(dev_hdmi.closed)
        self.assertTrue(dev_cons.closed)
        self.assertFalse(dev_kbd.closed)

        # Desconectar teclado en caliente
        fake_evdev._devices.pop("/dev/input/event5")
        listener._scan_evdev_devices()
        self.assertFalse(handler.has_usb_keyboard())
        self.assertTrue(dev_kbd.closed)

    def test_evdev_autorepeat_and_latam_typing(self):
        ecodes = make_fake_ecodes()
        handler = _make_handler_no_hw()
        listener = USBKeyboardListener(handler, layout="latam", ecodes_mod=ecodes)
        handler._keyboard_listener = listener

        def ev(code, val):
            return types.SimpleNamespace(type=1, code=code, value=val)

        # Escribir "a@-" en LATAM: 'a', AltGr+'q' ('@'), '/' ('-')
        listener._process_evdev_event(ev(ecodes.KEY_A, 1))
        listener._process_evdev_event(ev(ecodes.KEY_RIGHTALT, 1))
        listener._process_evdev_event(ev(ecodes.KEY_Q, 1))
        listener._process_evdev_event(ev(ecodes.KEY_RIGHTALT, 0))
        listener._process_evdev_event(ev(ecodes.KEY_SLASH, 1))

        # Backspace permite autorepeat (val=1 y val=2)
        listener._process_evdev_event(ev(ecodes.KEY_BACKSPACE, 1))
        listener._process_evdev_event(ev(ecodes.KEY_BACKSPACE, 2))

        # ENTER / TAB / ESC suprimen autorepeat (val=2 ignorado)
        listener._process_evdev_event(ev(ecodes.KEY_TAB, 1))
        listener._process_evdev_event(ev(ecodes.KEY_TAB, 2))
        listener._process_evdev_event(ev(ecodes.KEY_ENTER, 1))
        listener._process_evdev_event(ev(ecodes.KEY_ENTER, 2))
        listener._process_evdev_event(ev(ecodes.KEY_ESC, 1))
        listener._process_evdev_event(ev(ecodes.KEY_ESC, 2))

        collected = []
        while True:
            e = handler.get_event()
            if e is None:
                break
            collected.append((e, handler.get_last_char() if e == InputEvent.CHAR else None))

        self.assertEqual(
            collected,
            [
                (InputEvent.CHAR, "a"),
                (InputEvent.CHAR, "@"),
                (InputEvent.CHAR, "-"),
                (InputEvent.BACKSPACE, None),
                (InputEvent.BACKSPACE, None),
                (InputEvent.KEY2, None),
                (InputEvent.ENTER, None),
                (InputEvent.ESCAPE, None),
            ],
        )

    def test_inject_char_does_not_overwrite_consumed_last_char(self):
        handler = _make_handler_no_hw()
        handler.inject_char("a")
        handler.inject_char("b")

        ev1 = handler.get_event()
        self.assertEqual(ev1, InputEvent.CHAR)
        # Un nuevo inject_char en el hilo productor NO debe pisar el _last_char recién leído
        handler.inject_char("c")
        self.assertEqual(handler.get_last_char(), "a")

        self.assertEqual(handler.get_event(), InputEvent.CHAR)
        self.assertEqual(handler.get_last_char(), "b")
        self.assertEqual(handler.get_event(), InputEvent.CHAR)
        self.assertEqual(handler.get_last_char(), "c")

    def test_cycle_layout_via_f4_and_stdin_parsing(self):
        ecodes = make_fake_ecodes()
        handler = _make_handler_no_hw()
        listener = USBKeyboardListener(handler, layout="latam", ecodes_mod=ecodes)
        handler._keyboard_listener = listener

        self.assertEqual(handler.keyboard_layout, "latam")
        listener._process_evdev_event(types.SimpleNamespace(type=1, code=ecodes.KEY_F4, value=1))
        self.assertEqual(handler.keyboard_layout, "es")
        listener._process_evdev_event(types.SimpleNamespace(type=1, code=ecodes.KEY_F4, value=1))
        self.assertEqual(handler.keyboard_layout, "us")

        listener._process_stdin_bytes(b"xy\t\r")
        events = []
        while True:
            e = handler.get_event()
            if e is None:
                break
            events.append((e, handler.get_last_char()))
        self.assertEqual(
            events,
            [
                (InputEvent.CHAR, "x"),
                (InputEvent.CHAR, "y"),
                (InputEvent.KEY2, None),
                (InputEvent.ENTER, None),
            ],
        )


if __name__ == "__main__":
    unittest.main()

