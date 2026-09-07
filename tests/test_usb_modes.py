"""
Tests for USB Mode Manager (core/usb_modes.py)
"""

import unittest
from core.usb_modes import USBMode, USBModeManager


class TestUSBModeManager(unittest.TestCase):

    def setUp(self):
        self.mgr = USBModeManager(dry_run=True)

    def test_default_mode_and_switching(self):
        """Verify dry-run mode switching transitions properly between all modes."""
        current = self.mgr.get_current_mode()
        self.assertEqual(current, USBMode.NORMAL)

        # Switch to HID Linux (and test alias compatibility)
        ok, msg = self.mgr.set_mode(USBMode.HID_LINUX)
        self.assertTrue(ok)
        self.assertEqual(self.mgr.get_current_mode(), USBMode.HID_LINUX)
        self.assertEqual(self.mgr.get_current_mode(), USBMode.HID_KEYBOARD)

        # Switch to HID Windows
        ok, msg = self.mgr.set_mode(USBMode.HID_WINDOWS)
        self.assertTrue(ok)
        self.assertEqual(self.mgr.get_current_mode(), USBMode.HID_WINDOWS)

        # Switch back to Normal
        ok, msg = self.mgr.set_mode(USBMode.NORMAL)
        self.assertTrue(ok)
        self.assertEqual(self.mgr.get_current_mode(), USBMode.NORMAL)

    def test_generate_gadget_script_windows(self):
        """Verify Windows profile generates composite script with RNDIS linked first (Interfaces 0/1) and HID second (Interface 2)."""
        script = self.mgr.generate_gadget_script(USBMode.HID_WINDOWS, gadget_ip="10.0.0.1")
        self.assertIn("# REI_MODE=MODO_TECLADO_HID_WIN", script)
        self.assertIn("echo 0xEF > bDeviceClass", script)
        self.assertIn("echo 0x02 > bDeviceSubClass", script)

        # Functions present for Windows
        self.assertIn("functions/rndis.usb0", script)
        self.assertIn("functions/hid.usb0", script)
        self.assertNotIn("functions/ecm.usb0", script)

        # Static MAC addresses for RNDIS
        self.assertIn('echo "02:11:22:33:44:57" > functions/rndis.usb0/host_addr', script)
        self.assertIn('echo "02:11:22:33:44:58" > functions/rndis.usb0/dev_addr', script)

        # Order of linking in configs/c.1: RNDIS (Interface 0 & 1) MUST be linked before HID (Interface 2)
        idx_rndis = script.find("ln -s functions/rndis.usb0 configs/c.1/")
        idx_hid = script.find("ln -s functions/hid.usb0 configs/c.1/")
        self.assertGreater(idx_rndis, 0)
        self.assertGreater(idx_hid, 0)
        self.assertLess(idx_rndis, idx_hid)

        # Microsoft OS 1.0 descriptors for automatic Windows driver binding
        self.assertIn("echo MSFT100 > os_desc/qw_sign", script)
        self.assertIn("echo RNDIS > functions/rndis.usb0/os_desc/interface.rndis/compatible_id", script)
        self.assertIn("echo 5162001 > functions/rndis.usb0/os_desc/interface.rndis/sub_compatible_id", script)
        self.assertIn("ln -s configs/c.1 os_desc", script)

    def test_generate_gadget_script_linux(self):
        """Verify Linux profile generates composite script with native ECM and HID."""
        script = self.mgr.generate_gadget_script(USBMode.HID_LINUX, gadget_ip="10.0.0.1")
        self.assertIn("# REI_MODE=MODO_TECLADO_HID_LINUX", script)
        self.assertIn("echo 0xEF > bDeviceClass", script)
        self.assertIn("echo 0x02 > bDeviceSubClass", script)

        # Functions present for Linux
        self.assertIn("functions/hid.usb0", script)
        self.assertIn("functions/ecm.usb0", script)
        self.assertNotIn("functions/rndis.usb0", script)

        # Static MAC addresses for ECM
        self.assertIn('echo "02:11:22:33:44:55" > functions/ecm.usb0/host_addr', script)
        self.assertIn('echo "02:11:22:33:44:56" > functions/ecm.usb0/dev_addr', script)

    def test_network_isolation_configs(self):
        """Verify generated network isolation and dnsmasq config directives."""
        nm_cfg = self.mgr.get_network_manager_config()
        self.assertIn("unmanaged-devices=interface-name:usb*", nm_cfg)
        self.assertIn("interface-name:rndis*", nm_cfg)
        self.assertIn("interface-name:ecm*", nm_cfg)

        dns_cfg = self.mgr.get_dnsmasq_config("10.0.0.1", "10.0.0.2", "10.0.0.10")
        self.assertIn("port=0", dns_cfg)
        self.assertIn("interface=usb0", dns_cfg)
        self.assertIn("bind-dynamic", dns_cfg)
        self.assertIn("dhcp-authoritative", dns_cfg)
        self.assertIn("except-interface=wlan0", dns_cfg)
        self.assertIn("except-interface=eth0", dns_cfg)
        self.assertIn("dhcp-range=10.0.0.2,10.0.0.10,255.255.255.0,12h", dns_cfg)
        # Empty dhcp-option=3 avoids hijacking host default route
        self.assertIn("dhcp-option=3\n", dns_cfg)
        # Empty dhcp-option=6 avoids redirecting host DNS queries to Pi
        self.assertIn("dhcp-option=6\n", dns_cfg)
        self.assertNotIn("listen-address", dns_cfg)

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

