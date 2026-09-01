"""
Tests for USB Mode Manager (core/usb_modes.py)
"""

import unittest
from core.usb_modes import USBMode, USBModeManager


class TestUSBModeManager(unittest.TestCase):

    def setUp(self):
        self.mgr = USBModeManager(dry_run=True)

    def test_default_mode_and_switching(self):
        """Verify dry-run mode switching transitions properly."""
        current = self.mgr.get_current_mode()
        self.assertEqual(current, USBMode.NORMAL)

        # Switch to HID Keyboard
        ok, msg = self.mgr.set_mode(USBMode.HID_KEYBOARD)
        self.assertTrue(ok)
        self.assertEqual(self.mgr.get_current_mode(), USBMode.HID_KEYBOARD)

        # Switch back to Normal
        ok, msg = self.mgr.set_mode(USBMode.NORMAL)
        self.assertTrue(ok)
        self.assertEqual(self.mgr.get_current_mode(), USBMode.NORMAL)


if __name__ == "__main__":
    unittest.main()
