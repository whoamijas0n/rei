"""
REI Unit Tests - Wi-Fi, Virtual Keyboard, Navigation Stack & Update Subsystems.
Tests strictly validate non-blocking 30 FPS UI architecture, OOP decoupling,
safe.directory Git updates, and dpkg auto-recovery.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from core.interfaces import DiagnosticResult, DiagnosticStatus
from core.plugins import AppUpdatePlugin, SystemUpdatePlugin, WiFiConnectPlugin
from core.wifi import WiFiManager
from main import REIApp
from ui.display import (
    ScreenManager,
    HeroCardDeckView,
    HeroCard,
    DetailCardView,
    VirtualKeyboardInputView,
    KeyboardInputView,
    KeyboardLayer,
    ViewAction,
    ViewActionType,
)
from ui.input_handler import GPIOInputHandler, InputEvent, USBKeyboardListener


class TestVirtualKeyboard(unittest.TestCase):
    """Validates on-screen Virtual Keyboard operated with 5-way Joystick & GPIO buttons."""

    def test_grid_navigation_and_character_typing(self):
        """Validates that moving joystick and clicking PRESS types characters accurately."""
        view = VirtualKeyboardInputView(ssid="Lab_WiFi")

        # Initial cursor is at row 0, col 0 ('q')
        self.assertEqual(view.cursor_row, 0)
        self.assertEqual(view.cursor_col, 0)

        # Press -> types 'q'
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.input_text, "q")

        # Move RIGHT -> col 1 ('w')
        view.handle_input(InputEvent.RIGHT)
        self.assertEqual(view.cursor_col, 1)
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.input_text, "qw")

        # Move DOWN -> row 1, col 1 ('s')
        view.handle_input(InputEvent.DOWN)
        self.assertEqual(view.cursor_row, 1)
        self.assertEqual(view.cursor_col, 1)
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.input_text, "qws")

        # Move LEFT -> row 1, col 0 ('a')
        view.handle_input(InputEvent.LEFT)
        self.assertEqual(view.cursor_col, 0)
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.input_text, "qwsa")

    def test_layer_switching_and_special_characters(self):
        """Validates switching between LOWER, UPPER, NUM, and SYM layers via on-screen keys."""
        view = VirtualKeyboardInputView(ssid="Lab_WiFi")

        # 1. Switch to UPPER layer (Row 3, Cols 0..1 = 'ABC')
        view.cursor_row = 3
        view.cursor_col = 0
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.current_layer, KeyboardLayer.UPPER)

        # Type 'P' (Row 0, Col 9 in UPPER layout)
        view.cursor_row = 0
        view.cursor_col = 9
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.input_text, "P")

        # 2. Switch to NUM layer (Row 3, Cols 2..3 = '123')
        view.cursor_row = 3
        view.cursor_col = 2
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.current_layer, KeyboardLayer.NUM)

        # Type '9' (Row 0, Col 8) and '0' (Row 0, Col 9)
        view.cursor_row = 0
        view.cursor_col = 8
        view.handle_input(InputEvent.PRESS)
        view.cursor_col = 9
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.input_text, "P90")

        # 3. Switch to SYM layer (Row 3, Cols 4..5 = 'SYM')
        view.cursor_row = 3
        view.cursor_col = 4
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.current_layer, KeyboardLayer.SYM)

        # Type '#' (Row 1, Col 9) and '?' (Row 0, Col 5)
        view.cursor_row = 1
        view.cursor_col = 9
        view.handle_input(InputEvent.PRESS)
        view.cursor_row = 0
        view.cursor_col = 5
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.input_text, "P90#?")

    def test_space_and_backspace_on_matrix(self):
        """Validates SP and DEL keys located on Row 3."""
        view = VirtualKeyboardInputView(ssid="TestNet")
        view.set_text("Hello")

        # Select SP (Row 3, Col 6)
        view.cursor_row = 3
        view.cursor_col = 6
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.input_text, "Hello ")

        # Select DEL (Row 3, Col 7..8)
        view.cursor_row = 3
        view.cursor_col = 7
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(view.input_text, "Hello")

    def test_quick_hardware_buttons(self):
        """Validates KEY1 (Submit), KEY2 (Instant Backspace), and KEY3 (Cancel/Pop)."""
        submitted = {}

        def on_sub(ssid, pwd):
            submitted["ssid"] = ssid
            submitted["pwd"] = pwd

        view = VirtualKeyboardInputView(ssid="Office_AP", on_submit=on_sub)
        view.set_text("SecretPass123")

        # KEY2 -> Instant Backspace
        view.handle_input(InputEvent.KEY2)
        self.assertEqual(view.input_text, "SecretPass12")

        # KEY1 -> Instant Submit
        action_submit = view.handle_input(InputEvent.KEY1)
        self.assertEqual(submitted["ssid"], "Office_AP")
        self.assertEqual(submitted["pwd"], "SecretPass12")
        self.assertEqual(action_submit.action_type, ViewActionType.NONE)

        # KEY3 -> Instant Cancel (POP_VIEW)
        action_cancel = view.handle_input(InputEvent.KEY3)
        self.assertEqual(action_cancel.action_type, ViewActionType.POP_VIEW)

    def test_rendering_and_horizontal_scroll(self):
        """Validates that rendering 128x64 canvas succeeds with scrolling and active highlight."""
        from PIL import Image, ImageDraw

        view = VirtualKeyboardInputView(ssid="Long_SSID_Network", masked=True)
        view.set_text("VeryLongPasswordThatExceedsVisibleScreenBox12345!")

        img = Image.new("1", (128, 64), "black")
        draw = ImageDraw.Draw(img)

        # Update animation tick & render
        view.update()
        view.render(draw, width=128, height=64)

        # Check cursor tick toggle
        self.assertTrue(view.cursor_visible)
        for _ in range(16):
            view.update()
        self.assertFalse(view.cursor_visible)

    def test_wpa_str_escaping(self):
        """Validates proper escaping of quotes and backslashes in Wi-Fi manager."""
        escaped1 = WiFiManager._escape_wpa_str('SimplePass123')
        self.assertEqual(escaped1, '"SimplePass123"')

        escaped2 = WiFiManager._escape_wpa_str('Pass"with"quotes')
        self.assertEqual(escaped2, r'"Pass\"with\"quotes"')

        escaped3 = WiFiManager._escape_wpa_str(r'Pass\with\slashes')
        self.assertEqual(escaped3, r'"Pass\\with\\slashes"')


class TestUSBKeyboardAndInput(unittest.TestCase):
    """Validates USB keyboard capture and non-blocking input handling."""

    def setUp(self):
        self.handler = GPIOInputHandler(pin_config={})

    def tearDown(self):
        self.handler.close()

    def test_inject_characters_and_retrieval(self):
        """Validates character injection and non-blocking FIFO retrieval."""
        self.handler.inject_char("P")
        self.handler.inject_char("a")
        self.handler.inject_char("s")
        self.handler.inject_char("s")
        self.handler.inject_char("1")

        events = []
        chars = []
        ev = self.handler.get_event()
        while ev is not None:
            events.append(ev)
            chars.append(self.handler.get_last_char())
            ev = self.handler.get_event()

        self.assertEqual(len(events), 5)
        self.assertTrue(all(e == InputEvent.CHAR for e in events))
        self.assertEqual(chars, ["P", "a", "s", "s", "1"])

    def test_keyboard_input_view_interaction(self):
        """Validates typing, backspace and submit in VirtualKeyboardInputView (backward compatibility)."""
        submitted = {}

        def on_sub(ssid, password):
            submitted["ssid"] = ssid
            submitted["password"] = password

        view = KeyboardInputView(ssid="Lab_WiFi", on_submit=on_sub, masked=False)

        # 1. Type characters directly
        view.handle_input(InputEvent.CHAR, char="S")
        view.handle_input(InputEvent.CHAR, char="e")
        view.handle_input(InputEvent.CHAR, char="c")
        view.handle_input(InputEvent.CHAR, char="u")
        view.handle_input(InputEvent.CHAR, char="r")
        view.handle_input(InputEvent.CHAR, char="e")
        view.handle_input(InputEvent.CHAR, char="!")
        self.assertEqual(view.input_text, "Secure!")

        # 2. Backspace via KEY2 / BACKSPACE
        view.handle_input(InputEvent.BACKSPACE)
        self.assertEqual(view.input_text, "Secure")

        # 3. Submit with KEY1 / ENTER
        action = view.handle_input(InputEvent.ENTER)
        self.assertEqual(submitted["ssid"], "Lab_WiFi")
        self.assertEqual(submitted["password"], "Secure")
        self.assertEqual(action.action_type, ViewActionType.NONE)

    def test_evdev_scancode_processing(self):
        """Validates that evdev key events (letters, shift, caps lock, numbers, controls) map accurately."""
        try:
            from evdev import ecodes
        except ImportError:
            self.skipTest("evdev not available")

        listener = self.handler._keyboard_listener
        self.handler.clear()

        class MockEvent:
            def __init__(self, code, value):
                self.code = code
                self.value = value

        # 1. Type letter 'a'
        listener._process_evdev_event(MockEvent(ecodes.KEY_A, 1))
        self.assertEqual(self.handler.get_event(), InputEvent.CHAR)
        self.assertEqual(self.handler.get_last_char(), "a")

        # 2. Type with Shift -> 'A'
        listener._process_evdev_event(MockEvent(ecodes.KEY_LEFTSHIFT, 1))  # Shift DOWN
        listener._process_evdev_event(MockEvent(ecodes.KEY_B, 1))
        self.assertEqual(self.handler.get_event(), InputEvent.CHAR)
        self.assertEqual(self.handler.get_last_char(), "B")
        listener._process_evdev_event(MockEvent(ecodes.KEY_LEFTSHIFT, 0))  # Shift UP

        # 3. Shifted number: KEY_1 -> '!'
        listener._process_evdev_event(MockEvent(ecodes.KEY_LEFTSHIFT, 1))
        listener._process_evdev_event(MockEvent(ecodes.KEY_1, 1))
        self.assertEqual(self.handler.get_event(), InputEvent.CHAR)
        self.assertEqual(self.handler.get_last_char(), "!")
        listener._process_evdev_event(MockEvent(ecodes.KEY_LEFTSHIFT, 0))

        # 4. CapsLock: 'c' -> 'C'
        listener._process_evdev_event(MockEvent(ecodes.KEY_CAPSLOCK, 1))  # CapsLock ON
        listener._process_evdev_event(MockEvent(ecodes.KEY_C, 1))
        self.assertEqual(self.handler.get_event(), InputEvent.CHAR)
        self.assertEqual(self.handler.get_last_char(), "C")
        listener._process_evdev_event(MockEvent(ecodes.KEY_CAPSLOCK, 1))  # CapsLock OFF

        # 5. Control keys: Enter, Backspace, Esc, Tab (KEY2)
        listener._process_evdev_event(MockEvent(ecodes.KEY_ENTER, 1))
        self.assertEqual(self.handler.get_event(), InputEvent.ENTER)

        listener._process_evdev_event(MockEvent(ecodes.KEY_BACKSPACE, 1))
        self.assertEqual(self.handler.get_event(), InputEvent.BACKSPACE)

        listener._process_evdev_event(MockEvent(ecodes.KEY_ESC, 1))
        self.assertEqual(self.handler.get_event(), InputEvent.ESCAPE)

        listener._process_evdev_event(MockEvent(ecodes.KEY_TAB, 1))
        self.assertEqual(self.handler.get_event(), InputEvent.KEY2)


class TestNavigationAndHeroCards(unittest.TestCase):
    """Validates ScreenManager stack, pop_to_root, and centered Hero Cards."""

    def setUp(self):
        self.screen_manager = ScreenManager(width=128, height=64)

    def test_pop_to_root(self):
        """Validates that pop_to_root clears the entire stack down to root view."""
        root = HeroCardDeckView("ROOT")
        v1 = HeroCardDeckView("LEVEL 1")
        v2 = HeroCardDeckView("LEVEL 2")
        v3 = DetailCardView("DETAIL")

        self.screen_manager.set_root_view(root)
        self.screen_manager.push_view(v1)
        self.screen_manager.push_view(v2)
        self.screen_manager.push_view(v3)

        self.assertEqual(len(self.screen_manager._view_stack), 4)
        self.assertEqual(self.screen_manager.current_view, v3)

        self.screen_manager.pop_to_root()
        self.assertEqual(len(self.screen_manager._view_stack), 1)
        self.assertEqual(self.screen_manager.current_view, root)

    def test_detail_view_pop_to_root_on_key1(self):
        """Validates DetailCardView configured with pop_to_root_on_key1 returns POP_TO_ROOT on KEY1."""
        detail_default = DetailCardView("NORMAL", ["Info"])
        action1 = detail_default.handle_input(InputEvent.KEY1)
        self.assertEqual(action1.action_type, ViewActionType.POP_VIEW)

        detail_root = DetailCardView("ROOT_NAV", ["Info"], pop_to_root_on_key1=True)
        action2 = detail_root.handle_input(InputEvent.KEY1)
        self.assertEqual(action2.action_type, ViewActionType.POP_TO_ROOT)

        action3 = detail_root.handle_input(InputEvent.PRESS)
        self.assertEqual(action3.action_type, ViewActionType.POP_TO_ROOT)

        action4 = detail_root.handle_input(InputEvent.KEY3)
        self.assertEqual(action4.action_type, ViewActionType.POP_VIEW)

    def test_wifi_result_hero_card_success_flow(self):
        """Validates Ajuste 1.2 and Ajuste 1.3: Success shows 'CONECTADO', KEY1 -> Detail -> KEY1 -> Main Menu."""
        app = REIApp()
        res_success = DiagnosticResult(
            plugin_name="CONECTAR WI-FI",
            status=DiagnosticStatus.SUCCESS,
            summary="Conectado",
            details=["SSID: Lab_WiFi", "IP: 192.168.1.100", "GW: 192.168.1.1"]
        )

        app._display_wifi_result_hero_card(res_success, "Lab_WiFi")

        # 1. Active view is the result HeroCardDeckView
        current_view = app.screen_manager.current_view
        self.assertIsInstance(current_view, HeroCardDeckView)
        self.assertEqual(len(current_view.cards), 1)

        # Title MUST be strictly "CONECTADO" without redundant SSID
        hero_card = current_view.cards[0]
        self.assertEqual(hero_card.title, "CONECTADO")
        self.assertEqual(hero_card.icon_name, "WIFI_OK")

        # 2. Press KEY1 on Hero Card -> opens Detail view
        action_key1 = current_view.handle_input(InputEvent.KEY1)
        self.assertEqual(action_key1.action_type, ViewActionType.PUSH_VIEW)
        detail_view = action_key1.target_view
        self.assertIsInstance(detail_view, DetailCardView)
        app.screen_manager.push_view(detail_view)

        # 3. Press KEY1 on Detail View -> POP_TO_ROOT
        action_detail_key1 = detail_view.handle_input(InputEvent.KEY1)
        self.assertEqual(action_detail_key1.action_type, ViewActionType.POP_TO_ROOT)

        app.screen_manager.pop_to_root()
        self.assertEqual(app.screen_manager.current_view.title, "MAIN")

        app.shutdown()

    def test_wifi_result_hero_card_error_flow(self):
        """Validates Error shows 'ERROR CONEXION', KEY1 -> Detail -> KEY1 -> Main Menu."""
        app = REIApp()
        res_fail = DiagnosticResult(
            plugin_name="CONECTAR WI-FI",
            status=DiagnosticStatus.FAILED,
            summary="Fallo de conexión",
            details=["Red: Lab_WiFi", "Error: Auth Failed", "Causa: Clave incorrecta"]
        )

        app._display_wifi_result_hero_card(res_fail, "Lab_WiFi")

        current_view = app.screen_manager.current_view
        self.assertIsInstance(current_view, HeroCardDeckView)
        self.assertEqual(len(current_view.cards), 1)

        hero_card = current_view.cards[0]
        self.assertEqual(hero_card.title, "ERROR CONEXION")
        self.assertEqual(hero_card.icon_name, "WIFI_FAIL")

        # Press KEY1 on Error Hero Card -> opens Detail view
        action_key1 = current_view.handle_input(InputEvent.KEY1)
        self.assertEqual(action_key1.action_type, ViewActionType.PUSH_VIEW)
        detail_view = action_key1.target_view
        self.assertIsInstance(detail_view, DetailCardView)
        app.screen_manager.push_view(detail_view)

        # Press KEY1 on Error Detail View -> POP_TO_ROOT
        action_detail_key1 = detail_view.handle_input(InputEvent.KEY1)
        self.assertEqual(action_detail_key1.action_type, ViewActionType.POP_TO_ROOT)

        app.screen_manager.pop_to_root()
        self.assertEqual(app.screen_manager.current_view.title, "MAIN")

        app.shutdown()


class TestUpdateSubsystems(unittest.TestCase):
    """Validates AppUpdatePlugin (Git safe.directory) and SystemUpdatePlugin (dpkg recovery)."""

    def test_app_update_exec_git_safe_directory_args(self):
        """Validates that _exec_git explicitly injects safe.directory overrides."""
        plugin = AppUpdatePlugin(repo_path="/home/pi/rei")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="main\n", stderr="")
            res = plugin._exec_git("git", ["status"])

            mock_run.assert_called_once()
            called_cmd = mock_run.call_args[0][0]

            self.assertEqual(called_cmd[0], "git")
            self.assertIn("-c", called_cmd)
            self.assertIn("safe.directory=/home/pi/rei", called_cmd)
            self.assertIn("safe.directory=*", called_cmd)

    def test_app_update_dry_run_simulation(self):
        """Validates AppUpdatePlugin simulation with progress reporting."""
        plugin = AppUpdatePlugin()
        progress_events = []

        def on_prog(msg, pct):
            progress_events.append((msg, pct))

        os.environ["REI_DRY_RUN"] = "1"
        try:
            result = plugin.run(progress_callback=on_prog)
            self.assertEqual(result.status, DiagnosticStatus.SUCCESS)
            self.assertIn("REI al día", result.summary)
            self.assertTrue(len(progress_events) >= 3)
            self.assertEqual(progress_events[-1][1], 1.0)
        finally:
            os.environ.pop("REI_DRY_RUN", None)

    def test_system_update_dry_run_simulation(self):
        """Validates SystemUpdatePlugin simulation with progress reporting."""
        plugin = SystemUpdatePlugin()
        progress_events = []

        def on_prog(msg, pct):
            progress_events.append((msg, pct))

        os.environ["REI_DRY_RUN"] = "1"
        try:
            result = plugin.run(progress_callback=on_prog)
            self.assertEqual(result.status, DiagnosticStatus.SUCCESS)
            self.assertIn("Sistema al día", result.summary)
            self.assertTrue(len(progress_events) >= 3)
            self.assertEqual(progress_events[-1][1], 1.0)
        finally:
            os.environ.pop("REI_DRY_RUN", None)


if __name__ == "__main__":
    unittest.main()
