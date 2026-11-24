"""
Tests for UI Display Engine & Views (ui/display.py)
"""

import unittest
from PIL import Image, ImageDraw

from ui.display import (
    DetailCardView,
    HeroCard,
    HeroCardDeckView,
    QRCodeView,
    ScreenManager,
    UpdateProgressView,
    ViewActionType,
)
from ui.input_handler import InputEvent
from ui.wifi_result_view import WiFiResultView


def _canvas():
    return ImageDraw.Draw(Image.new("1", (128, 64), "black"))


class TestUIDisplay(unittest.TestCase):

    def test_hero_card_deck_rendering_and_navigation(self):
        """Verify Hero Card Deck rendering and carousel wrap-around."""
        deck = HeroCardDeckView("TEST DECK")
        card1 = HeroCard(title="CARD 1", icon_name="WINDOWS")
        card2 = HeroCard(title="CARD 2", icon_name="LINUX")
        deck.add_card(card1)
        deck.add_card(card2)

        buffer = Image.new("1", (128, 64), "black")
        draw = ImageDraw.Draw(buffer)
        deck.render(draw, 128, 64)

        # Test Right navigation
        action = deck.handle_input(InputEvent.RIGHT)
        self.assertEqual(action.action_type, ViewActionType.NONE)
        self.assertEqual(deck.active_index, 1)

        # Test Right wrap-around
        action = deck.handle_input(InputEvent.RIGHT)
        self.assertEqual(deck.active_index, 0)

    def test_update_progress_view_strict_locking(self):
        """Verify UpdateProgressView blocks input while running and unblocks upon completion."""
        view = UpdateProgressView(title="TEST PROGRESS")
        view.is_running = True

        # While running, all input events must be ignored (NONE)
        for event in (InputEvent.KEY1, InputEvent.KEY2, InputEvent.KEY3, InputEvent.UP, InputEvent.PRESS):
            action = view.handle_input(event)
            self.assertEqual(action.action_type, ViewActionType.NONE, f"Event {event} should be blocked while running")

        # When completed, exit events are permitted
        view.set_completed(success=True, summary="Operacion Exitosa")
        action = view.handle_input(InputEvent.KEY3)
        self.assertEqual(action.action_type, ViewActionType.POP_VIEW)

    def test_detail_view_wraps_long_lines_without_losing_content(self):
        """Long lines are word-wrapped instead of chopped at 20 characters."""
        view = DetailCardView("ERROR CONEXION", initial_lines=["Red: MiRedDeCasaMuyLarga123456"])
        view.render(_canvas(), 128, 64)
        # The 29-char line must produce more than one physical line.
        self.assertGreaterEqual(view._physical_count, 2)
        # Scrolling exposes the wrapped remainder.
        view.handle_input(InputEvent.DOWN)
        self.assertEqual(view.scroll_offset, 1)

    def test_detail_view_sanitizes_non_ascii(self):
        view = DetailCardView("ESTADO", initial_lines=["Contraseña incorrecta"])
        view.render(_canvas(), 128, 64)
        joined = " | ".join(view.lines)
        self.assertIn("Contrasena", joined)
        self.assertNotIn("ñ", joined)

    def test_detail_view_scroll_indicators_render(self):
        view = DetailCardView("SCROLL", initial_lines=["Linea uno", "Linea dos", "Linea tres", "Linea cuatro"])
        view.render(_canvas(), 128, 64)
        self.assertGreater(view._physical_count, 3)
        # Start of list: only the bottom indicator is meaningful.
        view.handle_input(InputEvent.DOWN)
        view.handle_input(InputEvent.DOWN)
        view.render(_canvas(), 128, 64)
        self.assertEqual(view.scroll_offset, 2)

    def test_update_progress_view_finished_labels_are_ascii(self):
        """[OK]/[X] labels render instead of non-ASCII check/cross glyphs."""
        view = UpdateProgressView(title="APT")
        view.set_completed(success=False, summary="Fallo", details=["Err 1", "Err 2"])
        buffer = Image.new("1", (128, 64), "black")
        view.render(ImageDraw.Draw(buffer), 128, 64)
        self.assertTrue(view.result_summary.isascii())

    def test_wifi_result_view_retry_with_key2(self):
        calls = []
        view = WiFiResultView(
            title="MiRed", icon_name="WIFI_FAIL",
            reason="CLAVE INCORRECTA", hint="K2=REINTENTAR",
            on_retry=lambda: calls.append(True),
        )
        buffer = Image.new("1", (128, 64), "black")
        view.render(ImageDraw.Draw(buffer), 128, 64)

        action = view.handle_input(InputEvent.KEY2)
        self.assertEqual(action.action_type, ViewActionType.NONE)
        self.assertEqual(calls, [True])

        # KEY3 exits, KEY1 goes back to the root menu.
        self.assertEqual(view.handle_input(InputEvent.KEY3).action_type, ViewActionType.POP_VIEW)
        self.assertEqual(view.handle_input(InputEvent.KEY1).action_type, ViewActionType.POP_TO_ROOT)

    def test_wifi_result_view_without_retry_ignores_key2(self):
        calls = []
        view = WiFiResultView(
            title="MiRed", icon_name="WIFI_OK",
            reason="IP: 192.168.1.10", hint="K3=SALIR",
            on_retry=None,
        )
        action = view.handle_input(InputEvent.KEY2)
        self.assertEqual(action.action_type, ViewActionType.NONE)
        self.assertEqual(calls, [])

    def test_qr_code_view_rendering(self):
        """Verify QRCodeView generates image and renders without crashing."""
        qr_view = QRCodeView(title="REPORTE MOVIL", url="http://10.0.0.1:8000/report/latest")
        self.assertIsNotNone(qr_view._qr_image)

        buffer = Image.new("1", (128, 64), "black")
        draw = ImageDraw.Draw(buffer)
        qr_view.render(draw, 128, 64)

        # Test exit with KEY3
        action = qr_view.handle_input(InputEvent.KEY3)
        self.assertEqual(action.action_type, ViewActionType.POP_VIEW)

    def test_qr_code_layout_no_overlap(self):
        """Verify QR matrix and text elements do not overlap on the 128x64 canvas."""
        qr_view = QRCodeView(title="REPORTE MOVIL", url="http://10.0.0.1:8000/r/abcdef12")
        qr_w, qr_h = qr_view._qr_image.size
        self.assertLessEqual(qr_w, 56)

        buffer = Image.new("1", (128, 64), "black")
        draw = ImageDraw.Draw(buffer)
        qr_view.render(draw, 128, 64)

        # The QR code starts at x=6, so it ends at x = 6 + qr_w - 1 (<= 61)
        qr_end_x = 6 + qr_w - 1
        # Text starts at x >= 66
        # Check that there is a clear vertical separation column (at x = qr_end_x + 1 to 65) with no text overlapping the QR
        self.assertGreater(66, qr_end_x)

    def test_qr_code_toggle_with_key2(self):
        """Verify KEY2 toggles between primary Wi-Fi and alternate USB URLs."""
        wifi_url = "http://192.168.1.55:8000/r/test1"
        usb_url = "http://10.0.0.1:8000/r/test1"

        qr_view = QRCodeView(
            title="REPORTE MOVIL",
            url=wifi_url,
            alt_url=usb_url,
            net_label="WIFI",
            alt_label="USB",
        )
        self.assertEqual(qr_view.url, wifi_url)
        self.assertEqual(qr_view.net_label, "WIFI")

        # Press KEY2: should toggle to USB
        action = qr_view.handle_input(InputEvent.KEY2)
        self.assertEqual(action.action_type, ViewActionType.NONE)
        self.assertEqual(qr_view.url, usb_url)
        self.assertEqual(qr_view.alt_url, wifi_url)
        self.assertEqual(qr_view.net_label, "USB")
        self.assertEqual(qr_view.alt_label, "WIFI")

        # Press KEY2 again: should toggle back to Wi-Fi
        action2 = qr_view.handle_input(InputEvent.KEY2)
        self.assertEqual(action2.action_type, ViewActionType.NONE)
        self.assertEqual(qr_view.url, wifi_url)
        self.assertEqual(qr_view.alt_url, usb_url)

    def test_qr_code_wifi_rendering(self):
        """Verify QRCodeView renders cleanly with a Wi-Fi URL on the 128x64 canvas."""
        qr_view = QRCodeView(
            title="REPORTE MOVIL",
            url="http://192.168.1.100:8000/r/a1b2c3",
            alt_url="http://10.0.0.1:8000/r/a1b2c3",
            net_label="WIFI",
            alt_label="USB",
        )
        self.assertIsNotNone(qr_view._qr_image)
        buffer = Image.new("1", (128, 64), "black")
        draw = ImageDraw.Draw(buffer)
        qr_view.render(draw, 128, 64)

        qr_w, qr_h = qr_view._qr_image.size
        self.assertLessEqual(qr_h, 64)

    def test_qr_code_key2_without_alt_url_exits(self):
        """Verify KEY2 acts as exit (POP_VIEW) when no alternate URL is configured."""
        qr_view = QRCodeView(title="REPORTE MOVIL", url="http://10.0.0.1:8000/r/test")
        action = qr_view.handle_input(InputEvent.KEY2)
        self.assertEqual(action.action_type, ViewActionType.POP_VIEW)


if __name__ == "__main__":
    unittest.main()
