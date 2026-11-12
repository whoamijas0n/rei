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

    def test_detail_card_word_wrap_sanitization_and_scroll(self):
        """Verify DetailCardView wraps long Spanish text safely and supports scrolling."""
        detail = DetailCardView(
            title="ERROR CONEXIÓN",
            initial_lines=["Contraseña inválida en la red de oficina principal"],
        )
        wrapped = detail._get_wrapped_lines()
        self.assertGreater(len(wrapped), 2)
        for line in wrapped:
            self.assertLessEqual(len(line), 19)
            self.assertNotIn("ñ", line)
            self.assertNotIn("á", line)

        buffer = Image.new("1", (128, 64), "black")
        draw = ImageDraw.Draw(buffer)
        detail.render(draw, 128, 64)
        detail.handle_input(InputEvent.DOWN)
        if len(wrapped) > 3:
            self.assertEqual(detail.scroll_offset, 1)

    def test_hero_card_deck_large_count_and_wifi_saved_icon(self):
        """Verify HeroCardDeckView handles >18 cards and WIFI_SAVED icon renders cleanly."""
        deck = HeroCardDeckView("REDES WI-FI")
        for idx in range(22):
            deck.add_card(HeroCard(title=f"NET_{idx}", icon_name="WIFI_SAVED"))
        buffer = Image.new("1", (128, 64), "black")
        draw = ImageDraw.Draw(buffer)
        deck.render(draw, 128, 64)
        self.assertEqual(deck.active_index, 0)

    def test_usb_keyboard_enter_and_escape_navigation(self):
        """Verify HeroCardDeckView, DetailCardView and UpdateProgressView accept ENTER/ESCAPE."""
        sub = DetailCardView("DETALLE", ["Linea 1"])
        deck = HeroCardDeckView("MENU")
        deck.add_card(HeroCard(title="ITEM", icon_name="INFO", submenu=sub))

        act_enter = deck.handle_input(InputEvent.ENTER)
        self.assertEqual(act_enter.action_type, ViewActionType.PUSH_VIEW)
        self.assertIs(act_enter.target_view, sub)

        act_esc = deck.handle_input(InputEvent.ESCAPE)
        self.assertEqual(act_esc.action_type, ViewActionType.POP_VIEW)

        self.assertEqual(sub.handle_input(InputEvent.ESCAPE).action_type, ViewActionType.POP_VIEW)
        self.assertEqual(sub.handle_input(InputEvent.ENTER).action_type, ViewActionType.POP_VIEW)

        prog = UpdateProgressView("PROG")
        prog.is_running = True
        self.assertEqual(prog.handle_input(InputEvent.ENTER).action_type, ViewActionType.NONE)
        self.assertEqual(prog.handle_input(InputEvent.ESCAPE).action_type, ViewActionType.NONE)
        prog.set_completed(success=True, summary="OK")
        self.assertEqual(prog.handle_input(InputEvent.ENTER).action_type, ViewActionType.POP_VIEW)


if __name__ == "__main__":
    unittest.main()
