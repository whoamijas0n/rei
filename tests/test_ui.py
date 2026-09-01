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


if __name__ == "__main__":
    unittest.main()
