"""
Unit tests for Display Engine, Hero Cards, and DiagnosticResultView.
"""

import os
import unittest
from PIL import Image, ImageDraw

os.environ["REI_DRY_RUN"] = "1"

from ui.display import (
    ScreenManager,
    HeroCardDeckView,
    HeroCard,
    DiagnosticResultView,
    IconRenderer,
    ViewAction,
    ViewActionType,
)
from ui.input_handler import InputEvent
from core.interfaces import Severity, AIAnalysisResult, DiagnosticResult


class TestUIEngine(unittest.TestCase):
    """Verifies rendering, card navigation, and DiagnosticResultView scrolling."""

    def setUp(self):
        self.screen_manager = ScreenManager(width=128, height=64)

    def test_icon_renderer_coverage(self):
        """Verifies icon rendering does not crash for all registered icon keys."""
        img = Image.new("1", (128, 64), 0)
        draw = ImageDraw.Draw(img)
        icons = [
            "DEFAULT", "INFO", "NETWORK", "WIFI", "BATTERY", "SYSTEM", "CPU",
            "SWITCH", "SWITCHES", "ENDPOINT", "PC", "WINDOWS", "LINUX",
            "PC_WINDOWS", "PC_LINUX", "TOOLS", "POWER", "POWEROFF", "REBOOT"
        ]
        for icon in icons:
            try:
                IconRenderer.draw_icon(draw, icon, cx=64, cy=24)
            except Exception as ex:
                self.fail(f"IconRenderer failed for icon '{icon}': {ex}")

    def test_hero_card_deck_navigation(self):
        """Verifies horizontal carousel navigation with LEFT/RIGHT input."""
        deck = HeroCardDeckView("ROOT")
        deck.add_card(HeroCard("CARD 1", "WINDOWS"))
        deck.add_card(HeroCard("CARD 2", "LINUX"))
        deck.add_card(HeroCard("CARD 3", "SWITCH"))

        self.assertEqual(deck.active_index, 0)
        deck.handle_input(InputEvent.RIGHT)
        self.assertEqual(deck.active_index, 1)
        deck.handle_input(InputEvent.RIGHT)
        self.assertEqual(deck.active_index, 2)
        deck.handle_input(InputEvent.RIGHT)
        self.assertEqual(deck.active_index, 0)  # Wrap around
        deck.handle_input(InputEvent.LEFT)
        self.assertEqual(deck.active_index, 2)

    def test_diagnostic_result_view_rendering_and_scrolling(self):
        """Verifies DiagnosticResultView renders and handles vertical joystick scroll."""
        analysis = AIAnalysisResult(
            estado=Severity.CRITICO,
            diagnostico_corto="Falla PSU2\n2 puertos CRC",
            acciones_recomendadas=["1. Reemplazar PSU", "2. Cambiar patch"],
            confianza=0.99
        )
        res = DiagnosticResult(
            plugin_name="SWITCH / RED",
            summary="Critico",
            details=["Host: SW-01", "Ptos: 24 UP"],
            ai_analysis=analysis,
            severity=Severity.CRITICO
        )

        view = DiagnosticResultView(title="SWITCH / RED", result=res)
        img = Image.new("1", (128, 64), 0)
        draw = ImageDraw.Draw(img)
        view.render(draw, width=128, height=64)

        # Scroll test
        initial_offset = view.scroll_offset
        view.handle_input(InputEvent.DOWN)
        self.assertGreaterEqual(view.scroll_offset, initial_offset)
        view.handle_input(InputEvent.UP)
        self.assertEqual(view.scroll_offset, initial_offset)

        # Back exit test
        action = view.handle_input(InputEvent.KEY3)
        self.assertEqual(action.action_type, ViewActionType.POP_VIEW)

    def test_screen_manager_stack(self):
        """Verifies push/pop view stack operations."""
        view1 = HeroCardDeckView("V1")
        view2 = HeroCardDeckView("V2")

        self.screen_manager.set_root_view(view1)
        self.assertEqual(self.screen_manager.current_view, view1)

        self.screen_manager.push_view(view2)
        self.assertEqual(self.screen_manager.current_view, view2)

        popped = self.screen_manager.pop_view()
        self.assertEqual(popped, view2)
        self.assertEqual(self.screen_manager.current_view, view1)


if __name__ == "__main__":
    unittest.main()
