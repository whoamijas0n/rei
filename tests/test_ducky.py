"""
Tests for USB HID Rubber Ducky Engine (core/ducky.py)
"""

import unittest
from unittest.mock import MagicMock, patch

from core.ducky import (
    HID_KEY_CODES,
    LAYOUT_ES,
    MOD_BITS,
    DuckyInjector,
)


class TestDuckyInjector(unittest.TestCase):

    def setUp(self):
        # Initialize injector in dry-run mode for test safety
        self.injector = DuckyInjector(dry_run=True)

    def test_key_mappings(self):
        """Verify common alphanumeric and control keys are mapped to HID Usage IDs."""
        self.assertEqual(HID_KEY_CODES['a'], 0x04)
        self.assertEqual(HID_KEY_CODES['z'], 0x1d)
        self.assertEqual(HID_KEY_CODES['1'], 0x1e)
        self.assertEqual(HID_KEY_CODES['enter'], 0x28)
        self.assertEqual(HID_KEY_CODES['space'], 0x2c)
        self.assertEqual(HID_KEY_CODES['leftgui'], 0xe3)

    def test_mod_bits(self):
        """Verify modifier bitmasks."""
        self.assertEqual(MOD_BITS['ctrl'], 0x01)
        self.assertEqual(MOD_BITS['shift'], 0x02)
        self.assertEqual(MOD_BITS['alt'], 0x04)
        self.assertEqual(MOD_BITS['gui'], 0x08)
        self.assertEqual(MOD_BITS['altgr'], 0x40)

    def test_spanish_iso_dead_keys(self):
        """Verify dead keys are flagged in Spanish layout."""
        # '´' (tilde), '^' (circunflejo), '~' (virgulilla), '`' (grave)
        self.assertIn('´', LAYOUT_ES)
        mod, key, is_dead = LAYOUT_ES['´']
        self.assertTrue(is_dead, "Acute accent should be flagged as dead key")

        self.assertIn('^', LAYOUT_ES)
        mod, key, is_dead = LAYOUT_ES['^']
        self.assertTrue(is_dead, "Circumflex should be flagged as dead key")

        self.assertIn('~', LAYOUT_ES)
        mod, key, is_dead = LAYOUT_ES['~']
        self.assertTrue(is_dead, "Tilde should be flagged as dead key")

        self.assertIn('`', LAYOUT_ES)
        mod, key, is_dead = LAYOUT_ES['`']
        self.assertTrue(is_dead, "Grave accent should be flagged as dead key")

    def test_dry_run_execution(self):
        """Verify injection methods execute cleanly in dry-run without opening /dev/hidg0."""
        self.injector.press_key("enter")
        self.injector.press_combination("gui", "r")
        self.injector.press_combination("ctrl+alt", "t")
        self.injector.write_text("ping 10.0.0.1\n", layout="es")
        self.injector.write_text("Hello World! ~ ^ ´\n", layout="es")
        self.injector.write_text("echo test\n", layout="us")

    @patch("core.ducky.os.path.exists", return_value=False)
    def test_real_mode_file_not_found(self, mock_exists):
        """Verify FileNotFoundError is raised when device node does not exist in non-dry-run."""
        real_injector = DuckyInjector(hid_device="/dev/nonexistent_hidg0", dry_run=False)
        with self.assertRaises(FileNotFoundError):
            real_injector.send_hid_report(0, 0x04)


if __name__ == "__main__":
    unittest.main()
