"""
Unit tests for USB HID Keyboard Injector.
"""

import os
import unittest

os.environ["REI_DRY_RUN"] = "1"

from core.hid_injector import (
    USBHIDInjector,
    MOD_NONE,
    MOD_LEFT_SHIFT,
    MOD_LEFT_GUI,
    KEY_ENTER,
    ASCII_TO_HID,
)


class TestUSBHIDInjector(unittest.TestCase):
    """Verifies HID report generation, keystroke scancode mapping, and Base64 injection."""

    def setUp(self):
        self.injector = USBHIDInjector(device_node="/dev/null_mock")

    def test_simulation_mode_active_off_pi(self):
        """Ensures injector detects lack of /dev/hidg0 or REI_DRY_RUN."""
        self.assertTrue(self.injector.is_simulated)

    def test_ascii_mapping_coverage(self):
        """Verifies essential command characters are mapped to valid HID keycodes."""
        for char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_+=/.:;$\"\'":
            self.assertIn(char, ASCII_TO_HID, f"Missing mapping for character '{char}'")
            mod, keycode = ASCII_TO_HID[char]
            self.assertIsInstance(mod, int)
            self.assertIsInstance(keycode, int)
            self.assertGreater(keycode, 0)

    def test_single_keystroke_simulation(self):
        """Verifies keystroke transmission returns success in dry-run mode."""
        res = self.injector.send_keystroke(KEY_ENTER, MOD_NONE)
        self.assertTrue(res)

    def test_key_combination_simulation(self):
        """Verifies multi-key shortcuts (e.g. Win+R, Ctrl+Alt+T)."""
        res = self.injector.send_key_combination([0x15], modifier=MOD_LEFT_GUI)
        self.assertTrue(res)

    def test_type_text_simulation(self):
        """Verifies character streaming with delay simulation."""
        res = self.injector.type_text("echo hello\n", char_delay_min=0.001, char_delay_max=0.002)
        self.assertTrue(res)

    def test_inject_windows_powershell_base64(self):
        """Verifies Base64 encoding and command construction for Windows."""
        sample_ps = "Get-Process | Out-String"
        res = self.injector.inject_windows_powershell(sample_ps, execution_delay=0.01)
        self.assertTrue(res)

    def test_inject_linux_bash_base64(self):
        """Verifies Base64 encoding and terminal injection for Linux."""
        sample_bash = "uname -a && free -m"
        res = self.injector.inject_linux_bash(sample_bash, execution_delay=0.01)
        self.assertTrue(res)


if __name__ == "__main__":
    unittest.main()
