"""
Automated Test Suite for OmniDiag Hub UI and Core Modules.
"""

import os
import sys
import time
import unittest
from PIL import Image, ImageDraw

# Add workspace to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.interfaces import DiagnosticResult, DiagnosticStatus
from core.manager import DiagnosticManager
from core.plugins import (
    IPAddressPlugin,
    WiFiScanPlugin,
    BatteryStatusPlugin,
    SystemStatusPlugin,
    CiscoSerialPlugin,
    CiscoSSHPlugin,
    SNMPScanPlugin,
    WindowsRNDISPlugin,
    LinuxSSHPlugin,
    VaultPlugin,
)
from ui.display import (
    ScreenManager,
    HeroCardDeckView,
    DetailCardView,
    HeroCard,
    IconRenderer,
    ViewActionType,
)
from ui.input_handler import GPIOInputHandler, InputEvent
from main import OmniDiagApp


class TestIconRenderer(unittest.TestCase):
    """Tests that procedural pixel-art icons render properly into Pillow canvas."""

    def setUp(self):
        self.icons = [
            "INFO", "NETWORK", "ENDPOINT", "VAULT", "IP",
            "WIFI", "BATTERY", "CPU", "SERIAL", "SSH",
            "SNMP", "WINDOWS", "LINUX", "DEFAULT"
        ]

    def test_all_icons_render_without_error(self):
        img = Image.new("1", (128, 64), 0)
        draw = ImageDraw.Draw(img)
        for icon in self.icons:
            img_test = Image.new("1", (128, 64), 0)
            draw_test = ImageDraw.Draw(img_test)
            IconRenderer.draw_icon(draw_test, icon, center_x=64, center_y=24)
            # Verify pixels were drawn inside the 20x20 bounding box (54, 14) to (74, 34)
            bbox = img_test.getbbox()
            self.assertIsNotNone(bbox, f"Icon {icon} did not draw any pixels!")
            # Check bounding box is within limits
            self.assertGreaterEqual(bbox[0], 50, f"Icon {icon} drew too far left: {bbox}")
            self.assertLessEqual(bbox[2], 78, f"Icon {icon} drew too far right: {bbox}")
            self.assertGreaterEqual(bbox[1], 10, f"Icon {icon} drew too high: {bbox}")
            self.assertLessEqual(bbox[3], 38, f"Icon {icon} drew too low: {bbox}")


class TestHeroCardStandard(unittest.TestCase):
    """Tests the Minimalist Hero Card Visual Standard."""

    def test_perimeter_border_and_typography(self):
        deck = HeroCardDeckView("TEST DECK")
        deck.add_card(HeroCard(title="INFO SISTEMA", icon_name="INFO"))
        deck.add_card(HeroCard(title="SWITCHES / RED", icon_name="NETWORK"))

        img = Image.new("1", (128, 64), 0)
        draw = ImageDraw.Draw(img)
        deck.render(draw, 128, 64)

        # Check continuous perimeter border at (1, 1) to (126, 62)
        # Top line
        self.assertEqual(img.getpixel((1, 1)), 1)
        self.assertEqual(img.getpixel((126, 1)), 1)
        # Bottom line
        self.assertEqual(img.getpixel((1, 62)), 1)
        self.assertEqual(img.getpixel((126, 62)), 1)

        # Check micro-dot pagination exists in top right
        # Total width: 2 cards -> dots at 122 - 4 = 118, and 122
        self.assertEqual(img.getpixel((118, 5)), 1)

    def test_navigation_cycling(self):
        deck = HeroCardDeckView("TEST DECK")
        deck.add_card(HeroCard(title="CARD 1", icon_name="INFO"))
        deck.add_card(HeroCard(title="CARD 2", icon_name="NETWORK"))
        deck.add_card(HeroCard(title="CARD 3", icon_name="ENDPOINT"))

        self.assertEqual(deck.active_index, 0)
        # Navigate RIGHT
        deck.handle_input(InputEvent.RIGHT)
        self.assertEqual(deck.active_index, 1)
        deck.handle_input(InputEvent.RIGHT)
        self.assertEqual(deck.active_index, 2)
        # Wrap around
        deck.handle_input(InputEvent.RIGHT)
        self.assertEqual(deck.active_index, 0)

        # Navigate LEFT
        deck.handle_input(InputEvent.LEFT)
        self.assertEqual(deck.active_index, 2)


class TestMenuHierarchyAndApp(unittest.TestCase):
    """Tests the full application hierarchy and event handling."""

    def test_app_initialization_and_menu_tree(self):
        app = OmniDiagApp()
        self.assertIsNotNone(app.screen_manager.current_view)
        root = app.screen_manager.current_view
        self.assertIsInstance(root, HeroCardDeckView)

        # Check Level 0 Cards
        card_titles = [c.title for c in root.cards]
        self.assertEqual(card_titles, ["INFO SISTEMA", "SWITCHES / RED", "ENDPOINTS PC", "BOVEDA / VAULT"])

        # Test entering Level 1 (INFO SISTEMA)
        action = root.handle_input(InputEvent.PRESS)
        self.assertEqual(action.action_type, ViewActionType.PUSH_VIEW)
        self.assertIsNotNone(action.target_view)

        app.screen_manager.push_view(action.target_view)
        lvl1_view = app.screen_manager.current_view
        self.assertIsInstance(lvl1_view, HeroCardDeckView)
        lvl1_titles = [c.title for c in lvl1_view.cards]
        self.assertEqual(lvl1_titles, ["CONEXION DE RED", "ESTADO BATERIA", "ESTADO SISTEMA"])

        # Test entering Level 2 (CONEXION DE RED)
        action_lvl2 = lvl1_view.handle_input(InputEvent.PRESS)
        self.assertEqual(action_lvl2.action_type, ViewActionType.PUSH_VIEW)
        app.screen_manager.push_view(action_lvl2.target_view)
        lvl2_view = app.screen_manager.current_view
        lvl2_titles = [c.title for c in lvl2_view.cards]
        self.assertEqual(lvl2_titles, ["VER DIRECCION IP", "ESCANEAR WI-FI"])

        # Test Back navigation
        pop_action = lvl2_view.handle_input(InputEvent.KEY1)
        self.assertEqual(pop_action.action_type, ViewActionType.POP_VIEW)
        app.screen_manager.pop_view()
        self.assertEqual(app.screen_manager.current_view, lvl1_view)

        pop_action2 = lvl1_view.handle_input(InputEvent.BACK)
        self.assertEqual(pop_action2.action_type, ViewActionType.POP_VIEW)
        app.screen_manager.pop_view()
        self.assertEqual(app.screen_manager.current_view, root)

        app.shutdown()


class TestDiagnosticConcurrency(unittest.TestCase):
    """Tests that background diagnostics run decoupled and non-blocking."""

    def test_async_diagnostic_execution(self):
        manager = DiagnosticManager(max_workers=2)
        plugin = IPAddressPlugin()
        manager.register_plugin(plugin)

        completed_results = []
        def on_done(res):
            completed_results.append(res)

        success = manager.execute_async("diag_ip_address", on_complete=on_done)
        self.assertTrue(success)

        # Wait max 2 seconds for worker thread
        timeout = 2.0
        start = time.time()
        while not completed_results and (time.time() - start) < timeout:
            time.sleep(0.05)

        self.assertEqual(len(completed_results), 1)
        res = completed_results[0]
        self.assertEqual(res.status, DiagnosticStatus.SUCCESS)
        self.assertIn("192.168.1.100", res.details[0] + res.details[2])

        manager.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
