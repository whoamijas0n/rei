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


    def test_network_isolation_configs(self):
        """Verify generated network isolation and dnsmasq config directives."""
        nm_cfg = self.mgr.get_network_manager_config()
        self.assertIn("unmanaged-devices=interface-name:usb*", nm_cfg)
        self.assertIn("interface-name:rndis*", nm_cfg)
        self.assertIn("interface-name:ecm*", nm_cfg)

        dns_cfg = self.mgr.get_dnsmasq_config("10.0.0.1", "10.0.0.2", "10.0.0.10")
        self.assertIn("interface=usb0", dns_cfg)
        self.assertIn("listen-address=10.0.0.1", dns_cfg)
        self.assertIn("except-interface=wlan0", dns_cfg)
        self.assertIn("except-interface=eth0", dns_cfg)
        self.assertIn("dhcp-range=usb0,10.0.0.2,10.0.0.10,255.255.255.0,12h", dns_cfg)
        # Empty dhcp-option=3 is critical to avoid hijacking host internet
        self.assertIn("dhcp-option=3\n", dns_cfg)
        self.assertIn("dhcp-option=6,10.0.0.1", dns_cfg)

    def test_detect_gadget_subnet_and_isolation_dry_run(self):
        """Verify default subnet detection and dry run isolation."""
        ip, mask, start, end = self.mgr.detect_gadget_subnet()
        self.assertEqual(ip, "10.0.0.1")
        self.assertEqual(mask, "255.255.255.0")
        self.assertEqual(start, "10.0.0.2")
        self.assertEqual(end, "10.0.0.10")

        # In dry run mode setup_network_isolation returns True without system modifications
        ok = self.mgr.setup_network_isolation()
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()

