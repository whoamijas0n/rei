"""
Tests for ui/wifi_password_view.py.

Pillow is imported at module level by the view, so the whole class is skipped
on machines without it (the same limitation affects tests/test_ui.py).
"""

import unittest

from ui.input_handler import InputEvent

try:
    from PIL import Image, ImageDraw

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    PIL_AVAILABLE = False


if PIL_AVAILABLE:
    from ui.wifi_password_view import WiFiPasswordView
else:  # pragma: no cover
    WiFiPasswordView = None  # type: ignore[assignment]

VIEW_AVAILABLE = PIL_AVAILABLE and WiFiPasswordView is not None


@unittest.skipUnless(VIEW_AVAILABLE, "Pillow and WiFiPasswordView are required")
class TestWiFiPasswordView(unittest.TestCase):

    def _view(self, **kwargs):
        params = {
            "ssid": "REI_Lab_AP",
            "security": "WPA2-PSK",
            "keyboard_status": lambda: True,
        }
        params.update(kwargs)
        return WiFiPasswordView(**params)

    def _render(self, view):
        buffer = Image.new("1", (128, 64), "black")
        draw = ImageDraw.Draw(buffer)
        view.update()
        view.render(draw, 128, 64)
        return buffer

    # ------------------------------------------------------------------
    # Text entry
    # ------------------------------------------------------------------
    def test_typing_appends_characters(self):
        view = self._view()
        for char in "abc123":
            view.handle_input(InputEvent.CHAR, char=char)
        self.assertEqual(view.password, "abc123")

    def test_backspace_removes_last_character(self):
        view = self._view()
        view.set_text("abc")
        view.handle_input(InputEvent.BACKSPACE)
        self.assertEqual(view.password, "ab")

    def test_backspace_on_empty_is_safe(self):
        view = self._view()
        view.handle_input(InputEvent.BACKSPACE)
        self.assertEqual(view.password, "")

    def test_non_printable_characters_are_ignored(self):
        view = self._view()
        view.handle_input(InputEvent.CHAR, char="\x01")
        view.handle_input(InputEvent.CHAR, char="")
        view.handle_input(InputEvent.CHAR, char=None)
        view.handle_input(InputEvent.CHAR, char="\n")
        self.assertEqual(view.password, "")

    def test_maximum_length_is_enforced(self):
        view = self._view()
        view.set_text("x" * 63)
        view.handle_input(InputEvent.CHAR, char="y")
        self.assertEqual(len(view.password), 63)
        self.assertTrue(view.error_message)

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------
    def test_visibility_toggles_with_key2(self):
        view = self._view()
        self.assertFalse(view.is_visible)
        view.handle_input(InputEvent.KEY2)
        self.assertTrue(view.is_visible)
        view.handle_input(InputEvent.KEY2)
        self.assertFalse(view.is_visible)

    def test_password_is_masked_by_default_in_render(self):
        view = self._view()
        view.set_text("secret123")
        # No exception and the view keeps the real value internally.
        self._render(view)
        self.assertEqual(view.password, "secret123")
        self.assertFalse(view.is_visible)

    # ------------------------------------------------------------------
    # Validation and submission
    # ------------------------------------------------------------------
    def test_enter_with_empty_password_does_not_submit(self):
        calls = []
        view = self._view(on_submit=lambda ssid, pwd: calls.append((ssid, pwd)))
        action = view.handle_input(InputEvent.ENTER)
        self.assertEqual(calls, [])
        self.assertTrue(view.error_message)
        self.assertEqual(action.action_type.name, "NONE")

    def test_enter_with_short_password_does_not_submit(self):
        calls = []
        view = self._view(on_submit=lambda ssid, pwd: calls.append((ssid, pwd)))
        view.set_text("short")
        view.handle_input(InputEvent.ENTER)
        self.assertEqual(calls, [])
        self.assertIn("8", view.error_message)

    def test_enter_with_valid_password_submits_once(self):
        calls = []
        view = self._view(on_submit=lambda ssid, pwd: calls.append((ssid, pwd)))
        view.set_text("12345678")
        view.handle_input(InputEvent.ENTER)
        view.handle_input(InputEvent.ENTER)
        self.assertEqual(calls, [("REI_Lab_AP", "12345678")])

    def test_key1_and_press_also_submit(self):
        for event in (InputEvent.KEY1, InputEvent.PRESS):
            calls = []
            view = self._view(on_submit=lambda ssid, pwd: calls.append(pwd))
            view.set_text("12345678")
            view.handle_input(event)
            self.assertEqual(calls, ["12345678"])

    def test_submit_callback_exception_is_contained(self):
        def boom(ssid, pwd):
            raise RuntimeError("network exploded")

        view = self._view(on_submit=boom)
        view.set_text("12345678")
        action = view.handle_input(InputEvent.ENTER)
        self.assertEqual(action.action_type.name, "NONE")
        self.assertTrue(view.error_message)

    def test_open_network_accepts_short_password(self):
        calls = []
        view = self._view(
            security="Open",
            on_submit=lambda ssid, pwd: calls.append(pwd),
        )
        view.set_text("a")
        view.handle_input(InputEvent.ENTER)
        self.assertEqual(calls, ["a"])

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------
    def test_cancel_events_pop_view(self):
        for event in (InputEvent.KEY3, InputEvent.ESCAPE, InputEvent.BACK):
            view = self._view()
            action = view.handle_input(event)
            self.assertEqual(action.action_type.name, "POP_VIEW")

    def test_accepts_text_input_flag(self):
        self.assertTrue(WiFiPasswordView.accepts_text_input)

    # ------------------------------------------------------------------
    # Rendering states
    # ------------------------------------------------------------------
    def test_render_no_keyboard(self):
        view = self._view(keyboard_status=lambda: False)
        buffer = self._render(view)
        self.assertIsNotNone(buffer)

    def test_render_gadget_mode_without_keyboard(self):
        view = self._view(keyboard_status=lambda: False, usb_gadget_active=True)
        self.assertIsNotNone(self._render(view))

    def test_render_keyboard_disconnected_after_typing(self):
        status = {"connected": True}
        view = self._view(keyboard_status=lambda: status["connected"])
        view.set_text("12345678")
        status["connected"] = False
        self.assertIsNotNone(self._render(view))
        # Text is preserved while the keyboard is gone.
        self.assertEqual(view.password, "12345678")

    def test_render_empty_password(self):
        view = self._view()
        self.assertIsNotNone(self._render(view))

    def test_render_visible_password(self):
        view = self._view()
        view.set_text("12345678")
        view.is_visible = True
        self.assertIsNotNone(self._render(view))

    def test_render_error_banner_pixels(self):
        view = self._view()
        view.handle_input(InputEvent.ENTER)  # empty -> error
        buffer = self._render(view)
        # Error banner is an inverted white rectangle from (3,33) to (124,45).
        self.assertEqual(buffer.getpixel((4, 44)), 255)
        self.assertEqual(buffer.getpixel((1, 1)), 255)   # border
        self.assertEqual(buffer.getpixel((0, 0)), 0)     # outside border

    def test_render_long_password_does_not_raise(self):
        view = self._view()
        view.set_text("x" * 63)
        view.is_visible = True
        self.assertIsNotNone(self._render(view))

    def test_keyboard_status_callback_error_is_contained(self):
        def broken():
            raise RuntimeError("status failure")

        view = self._view(keyboard_status=broken)
        self.assertIsNotNone(self._render(view))


if __name__ == "__main__":
    unittest.main()
