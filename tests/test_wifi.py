"""
Tests for core/wifi.py: password validation, parsers, command hardening and
connection error handling.
"""

import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from core.wifi import WiFiManager, WiFiNetwork, get_interface_ip
from core.wifi_errors import WiFiErrorCode, classify_error


class TestWiFiNetwork(unittest.TestCase):

    def test_is_secured(self):
        self.assertTrue(WiFiNetwork("A", 50, "WPA2-PSK").is_secured)
        self.assertTrue(WiFiNetwork("B", 50, "WPA3-SAE").is_secured)
        self.assertTrue(WiFiNetwork("C", 50, "WEP").is_secured)
        self.assertFalse(WiFiNetwork("D", 50, "Open").is_secured)
        self.assertFalse(WiFiNetwork("E", 50, "").is_secured)
        self.assertFalse(WiFiNetwork("F", 50, "--").is_secured)
        self.assertFalse(WiFiNetwork("G", 50, "NONE").is_secured)

    def test_signal_dbm(self):
        self.assertEqual(WiFiNetwork("A", 100, "Open").signal_dbm, -50)
        self.assertEqual(WiFiNetwork("A", 0, "Open").signal_dbm, -100)


class TestPasswordValidation(unittest.TestCase):

    def test_empty_is_rejected(self):
        ok, msg = WiFiManager.validate_password("", "WPA2")
        self.assertFalse(ok)
        self.assertIn("clave", msg.lower())

    def test_wpa_minimum_length(self):
        ok, msg = WiFiManager.validate_password("short", "WPA2-PSK")
        self.assertFalse(ok)
        self.assertIn("8", msg)

    def test_wpa_valid(self):
        ok, msg = WiFiManager.validate_password("12345678", "WPA2-PSK")
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_too_long_is_rejected(self):
        ok, _ = WiFiManager.validate_password("x" * 64, "WPA2-PSK")
        self.assertFalse(ok)

    def test_exactly_63_is_accepted(self):
        ok, _ = WiFiManager.validate_password("x" * 63, "WPA2-PSK")
        self.assertTrue(ok)

    def test_open_network_only_requires_non_empty(self):
        ok, _ = WiFiManager.validate_password("a", "Open")
        self.assertTrue(ok)


class TestDryRun(unittest.TestCase):

    def test_get_interface_ip_dry_run(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "1"}):
            self.assertEqual(get_interface_ip("wlan0"), "192.168.1.142")
            self.assertEqual(get_interface_ip("usb0"), "10.0.0.1")

    def test_scan_returns_simulated_networks(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "1"}):
            networks = WiFiManager().scan_networks()
        self.assertGreater(len(networks), 0)

    def test_connect_success(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "1"}), \
                patch("core.wifi.time.sleep"):
            success, message, details = WiFiManager().connect_network(
                "REI_Lab_AP", "correct-password"
            )
        self.assertTrue(success)
        self.assertIn("REI_Lab_AP", message)
        self.assertEqual(details["ssid"], "REI_Lab_AP")

    def test_connect_failure(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "1"}), \
                patch("core.wifi.time.sleep"):
            success, message, details = WiFiManager().connect_network(
                "REI_Lab_AP", "WRONG"
            )
        self.assertFalse(success)
        self.assertIn("incorrecta", message.lower())

    def test_empty_ssid_is_rejected(self):
        success, message, _ = WiFiManager().connect_network("   ", "password123")
        self.assertFalse(success)
        self.assertIn("SSID", message)


class TestCommandHardening(unittest.TestCase):

    def test_run_cmd_never_inherits_stdin(self):
        completed = subprocess.CompletedProcess(args=["nmcli"], returncode=0, stdout="", stderr="")
        with patch("core.wifi.subprocess.run", return_value=completed) as run:
            WiFiManager._run_cmd(["nmcli", "dev", "wifi"], timeout=3)
        _args, kwargs = run.call_args
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])


class TestNmcliParser(unittest.TestCase):

    def test_unescapes_colons(self):
        output = (
            "*:Cafe\\:Net:80:WPA2:AA\\:BB\\:CC\\:DD\\:EE\\:FF:6\n"
            ":Open\\:Guest:40:Open::11\n"
        )
        networks = WiFiManager()._parse_nmcli_output(output)
        by_ssid = {net.ssid: net for net in networks}

        self.assertIn("Cafe:Net", by_ssid)
        self.assertEqual(by_ssid["Cafe:Net"].signal_pct, 80)
        self.assertEqual(by_ssid["Cafe:Net"].security, "WPA2")
        self.assertTrue(by_ssid["Cafe:Net"].in_use)
        self.assertEqual(by_ssid["Cafe:Net"].bssid, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(by_ssid["Cafe:Net"].channel, 6)

        self.assertIn("Open:Guest", by_ssid)
        self.assertFalse(by_ssid["Open:Guest"].in_use)

    def test_skips_hidden_and_dash_ssids(self):
        output = ":--:10:WPA2::1\n:Real:50:WPA2::1\n"
        networks = WiFiManager()._parse_nmcli_output(output)
        self.assertEqual([n.ssid for n in networks], ["Real"])

    def test_deduplicates_keeping_strongest(self):
        output = ":Cafe:30:WPA2::1\n:Cafe:90:WPA2::6\n"
        networks = WiFiManager()._parse_nmcli_output(output)
        self.assertEqual(len(networks), 1)
        self.assertEqual(networks[0].signal_pct, 90)
        self.assertEqual(networks[0].channel, 6)


class TestIwlistParser(unittest.TestCase):

    SAMPLE = """
wlan0     Scan completed :
          Cell 01 - Address: 00:11:22:33:44:55
                    Quality=70/70  Signal level=-39 dBm
                    Encryption key:on
                    ESSID:"Office"
          Cell 02 - Address: 66:77:88:99:AA:BB
                    Quality=30/70  Signal level=-71 dBm
                    Encryption key:off
                    ESSID:"Cafe"
    """

    def test_groups_fields_per_cell(self):
        networks = WiFiManager()._parse_iwlist_output(self.SAMPLE)
        by_ssid = {net.ssid: net for net in networks}

        self.assertEqual(len(by_ssid), 2)
        self.assertEqual(by_ssid["Office"].security, "WPA2")
        self.assertEqual(by_ssid["Office"].signal_pct, 100)
        self.assertEqual(by_ssid["Cafe"].security, "Open")
        self.assertLess(by_ssid["Cafe"].signal_pct, by_ssid["Office"].signal_pct)
        # Descending signal order
        self.assertEqual(networks[0].ssid, "Office")


class TestRealMode(unittest.TestCase):

    def test_scan_without_backends_returns_empty(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value=None):
            networks = WiFiManager().scan_networks()
        self.assertEqual(networks, [])

    def test_connect_without_backends_fails_cleanly(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value=None):
            success, message, details = WiFiManager().connect_network("Office", "password123")
        self.assertFalse(success)
        self.assertEqual(details.get("error_code"), "NO_BACKEND")
        self.assertIn("gestor", message.lower())

    def test_nmcli_error_redacts_password(self):
        completed = subprocess.CompletedProcess(
            args=["nmcli"],
            returncode=1,
            stdout="",
            stderr="Error: 802-11-wireless-security.psk: secret 'hunter2' rejected",
        )
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch.object(WiFiManager, "_run_cmd", return_value=completed):
            success, message, details = WiFiManager().connect_network(
                "Office", "hunter2"
            )
        self.assertFalse(success)
        self.assertNotIn("hunter2", message)
        self.assertNotIn("hunter2", details.get("raw_error", ""))

    def test_nmcli_success_returns_ip(self):
        completed = subprocess.CompletedProcess(args=["nmcli"], returncode=0, stdout="", stderr="")
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch.object(WiFiManager, "_run_cmd", return_value=completed), \
                patch.object(WiFiManager, "_get_interface_ip", return_value="192.168.0.10"):
            success, message, details = WiFiManager().connect_network(
                "Office", "password123"
            )
        self.assertTrue(success)
        self.assertEqual(details["ip_address"], "192.168.0.10")


class TestWpaCliVerification(unittest.TestCase):

    def test_wait_succeeds_on_completed(self):
        responses = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="wpa_state=SCANNING", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="wpa_state=COMPLETED", stderr=""),
        ]
        with patch.object(WiFiManager, "_run_cmd", side_effect=responses), \
                patch("core.wifi.time.sleep"):
            self.assertTrue(WiFiManager()._wait_for_wpa_connection("/usr/bin/wpa_cli", timeout=3))

    def test_wait_fails_on_timeout(self):
        response = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="wpa_state=DISCONNECTED", stderr=""
        )
        with patch.object(WiFiManager, "_run_cmd", return_value=response), \
                patch("core.wifi.time.sleep"), \
                patch("core.wifi.time.monotonic", side_effect=[0, 0, 5]):
            self.assertFalse(WiFiManager()._wait_for_wpa_connection("/usr/bin/wpa_cli", timeout=2))


class TestErrorCatalog(unittest.TestCase):

    def test_catalog_messages_fit_oled_and_are_ascii(self):
        from core.wifi_errors import ERROR_CATALOG, WiFiErrorCode

        for code, info in ERROR_CATALOG.items():
            self.assertTrue(info.message.isascii(), f"{code}: {info.message!r}")
            self.assertTrue(info.hint.isascii(), f"{code}: {info.hint!r}")
            self.assertLessEqual(len(info.message), 20, f"{code}: {info.message!r}")
            self.assertLessEqual(len(info.hint), 20, f"{code}: {info.hint!r}")

    def test_profile_update_failure_is_not_wrong_password(self):
        # Real NetworkManager audit line from the deployment target: updating a
        # netplan-managed profile fails before the password is ever checked.
        raw = (
            "Error: Failed to modify connection 'netplan-wlan0-X': "
            "802-11-wireless-security.key-mgmt: property is missing"
        )
        info = classify_error(raw)
        self.assertEqual(info.code, WiFiErrorCode.PROFILE_ERROR)
        self.assertNotEqual(info.message, "CLAVE INCORRECTA")
        self.assertFalse(info.needs_password)

    def test_saved_secret_failure_requests_new_password(self):
        raw = "Error: Connection activation failed: (7) Secrets were required, but not provided."
        info = classify_error(raw)
        self.assertEqual(info.code, WiFiErrorCode.SAVED_SECRET_INVALID)
        self.assertTrue(info.needs_password)
        self.assertTrue(info.retryable)

    def test_handshake_failure_is_auth(self):
        info = classify_error("device (wlan0): supplicant state: 4way_handshake -> disconnected")
        self.assertEqual(info.code, WiFiErrorCode.AUTH_FAILED)
        self.assertTrue(info.needs_password)

    def test_not_found_and_timeout(self):
        self.assertEqual(
            classify_error("No network with SSID 'Cafe' found.").code,
            WiFiErrorCode.NOT_FOUND,
        )
        self.assertEqual(
            classify_error("Error: Timeout was reached").code,
            WiFiErrorCode.TIMEOUT,
        )

    def test_read_only_and_unknown(self):
        self.assertEqual(
            classify_error("Failed to add connection: Read-only file system").code,
            WiFiErrorCode.READ_ONLY_FS,
        )
        self.assertEqual(
            classify_error("something inexplicable happened").code,
            WiFiErrorCode.UNKNOWN,
        )


class TestActiveConnection(unittest.TestCase):

    ACTIVE_SHOW = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:netplan-wlan0-Home\n",
        stderr="",
    )
    SSID_SHOW = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="802-11-wireless.ssid:Home\n", stderr=""
    )

    def test_parses_active_state_and_ssid(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch.object(WiFiManager, "_run_cmd",
                             side_effect=[self.ACTIVE_SHOW, self.SSID_SHOW]), \
                patch.object(WiFiManager, "_get_interface_ip", return_value="192.168.1.50"):
            active = WiFiManager().get_active_connection()
        self.assertIsNotNone(active)
        self.assertEqual(active.ssid, "Home")
        self.assertEqual(active.profile_name, "netplan-wlan0-Home")
        self.assertEqual(active.ip, "192.168.1.50")

    def test_disconnected_state_returns_none(self):
        response = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n",
            stderr="",
        )
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch.object(WiFiManager, "_run_cmd", return_value=response):
            self.assertIsNone(WiFiManager().get_active_connection())

    def test_connect_to_active_network_short_circuits(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "1"}), \
                patch("core.wifi.time.sleep"), \
                patch.object(WiFiManager, "_run_cmd") as run:
            success, message, details = WiFiManager().connect_network("REI_Lab_AP")
        self.assertTrue(success)
        self.assertTrue(details.get("already_connected"))
        self.assertEqual(details.get("error_code"), "ALREADY_CONNECTED")
        run.assert_not_called()

    def test_active_network_with_typed_password_does_not_short_circuit(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "1"}), \
                patch("core.wifi.time.sleep"):
            success, _, details = WiFiManager().connect_network("REI_Lab_AP", "WRONG")
        self.assertFalse(success)
        self.assertNotIn("already_connected", details)


class TestSavedNetworks(unittest.TestCase):

    def test_lists_wifi_profiles_with_their_ssid(self):
        listing = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=(
                "netplan-wlan0-Home:uuid-1:802-11-wireless\n"
                "lo:uuid-2:loopback\n"
                "Yami:uuid-3:802-11-wireless\n"
            ),
            stderr="",
        )
        ssid_home = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="802-11-wireless.ssid:Home\n", stderr=""
        )
        ssid_yami = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="802-11-wireless.ssid:Yami\n", stderr=""
        )
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch.object(WiFiManager, "_run_cmd",
                             side_effect=[listing, ssid_home, ssid_yami]):
            saved = WiFiManager().get_saved_networks()
        self.assertEqual(saved, {"Home": "uuid-1", "Yami": "uuid-3"})

    def test_dry_run_saved_networks(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "1"}):
            saved = WiFiManager().get_saved_networks()
        self.assertIn("REI_Lab_AP", saved)
        self.assertIn("IT_Dep_Secure", saved)


class TestSavedProfileActivation(unittest.TestCase):
    """Selecting a known network must reuse the stored secret, without prompts."""

    def test_saved_profile_activates_without_password(self):
        listing = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Yami:uuid-9:802-11-wireless\n", stderr=""
        )
        ssid = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="802-11-wireless.ssid:Yami\n", stderr=""
        )
        up_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        active_show = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n",
            stderr="",
        )
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch("core.wifi.os.geteuid", return_value=0), \
                patch.object(WiFiManager, "_get_interface_ip", return_value="192.168.1.10"), \
                patch.object(WiFiManager, "_run_cmd", side_effect=[
                    active_show, listing, ssid, up_ok,
                ]) as run:
            success, message, details = WiFiManager().connect_network("Yami")
        self.assertTrue(success)
        self.assertTrue(details.get("saved_profile"))
        invoked = [call.args[0] for call in run.call_args_list]
        up_cmds = [cmd for cmd in invoked if "up" in cmd]
        self.assertTrue(any("uuid-9" in cmd for cmd in up_cmds))

    def test_stale_saved_secret_asks_for_new_password(self):
        listing = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Yami:uuid-9:802-11-wireless\n", stderr=""
        )
        ssid = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="802-11-wireless.ssid:Yami\n", stderr=""
        )
        up_fail = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: Connection activation failed: (7) Secrets were required, but not provided."
        )
        active_show = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n",
            stderr="",
        )
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch.object(WiFiManager, "_run_cmd", side_effect=[
                    active_show, listing, ssid, up_fail,
                ]):
            success, message, details = WiFiManager().connect_network("Yami")
        self.assertFalse(success)
        self.assertEqual(details.get("error_code"), "SAVED_SECRET_INVALID")
        self.assertTrue(details.get("needs_password"))
        self.assertIn("CAMBIADA", message)


class TestReiProfileLifecycle(unittest.TestCase):

    def _nmcli_responses(self, connect_rc=0, connect_stderr=""):
        return [
            # get_active_connection
            subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n", stderr="",
            ),
            # get_saved_networks
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # _find_rei_profile
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # connection add
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # connection up
            subprocess.CompletedProcess(
                args=[], returncode=connect_rc, stdout="", stderr=connect_stderr
            ),
        ]

    def test_creates_rei_profile_and_never_uses_device_connect(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch("core.wifi.os.geteuid", return_value=0), \
                patch.object(WiFiManager, "_get_interface_ip", return_value="192.168.1.77"), \
                patch.object(WiFiManager, "_run_cmd",
                             side_effect=self._nmcli_responses()) as run:
            success, _, details = WiFiManager().connect_network("Office", "secret123")
        self.assertTrue(success)
        self.assertEqual(details.get("ip_address"), "192.168.1.77")
        invoked = [call.args[0] for call in run.call_args_list]
        flat = " ".join(" ".join(cmd) for cmd in invoked)
        self.assertNotIn("device wifi connect", flat)
        self.assertIn("connection add", flat)
        self.assertIn("rei-Office", flat)
        self.assertIn("secret123", flat)
        # The password must be typed on the command line only at creation; it
        # must never be echoed back in a message.
        self.assertNotIn("secret123", details.get("raw_error", ""))

    def test_failed_activation_deletes_created_profile(self):
        responses = [
            # get_active_connection
            subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n", stderr="",
            ),
            # _find_rei_profile
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # connection add
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # connection up
            subprocess.CompletedProcess(
                args=[], returncode=1, stdout="",
                stderr="Error: Connection activation failed: (7) Secrets were required, but not provided.",
            ),
            # connection delete of the just-created profile
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch("core.wifi.os.geteuid", return_value=0), \
                patch.object(WiFiManager, "_run_cmd", side_effect=responses) as run:
            success, message, details = WiFiManager().connect_network("Office", "secret123")
        self.assertFalse(success)
        self.assertEqual(details.get("error_code"), "AUTH_FAILED")
        self.assertTrue(details.get("needs_password"))
        self.assertIn("INCORRECTA", message)
        invoked = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any("delete" in cmd and "rei-Office" in cmd for cmd in invoked))

    def test_foreign_profiles_are_never_modified(self):
        active_show = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n", stderr="",
        )
        # _find_rei_profile: no REI-owned profile for this SSID; the listing
        # contains a foreign (netplan) profile that must stay untouched.
        listing = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="netplan-wlan0-Home:uuid-net:802-11-wireless\n", stderr=""
        )
        add_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        up_fail = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="Error: Connection activation failed: (7) Secrets were required, but not provided.",
        )
        delete_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch("core.wifi.os.geteuid", return_value=0), \
                patch.object(WiFiManager, "_run_cmd", side_effect=[
                    active_show, listing, add_ok, up_fail, delete_ok,
                ]) as run:
            WiFiManager().connect_network("Home", "new-secret")
        invoked = [call.args[0] for call in run.call_args_list]
        # Foreign profiles are only ever activated read-only, never modified.
        self.assertFalse(any("modify" in cmd for cmd in invoked))
        # Only REI-owned profiles may be cleaned up after a failed attempt.
        delete_cmds = [cmd for cmd in invoked if "delete" in cmd]
        self.assertTrue(delete_cmds)
        self.assertTrue(all("rei-Home" in cmd for cmd in delete_cmds))
        self.assertFalse(any("uuid-net" in cmd for cmd in invoked))


class TestConnectOutcomeDetails(unittest.TestCase):

    def test_success_without_ip_reports_warning(self):
        responses = [
            # get_active_connection
            subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n", stderr="",
            ),
            # get_saved_networks
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # _find_rei_profile
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # connection add
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # connection up
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
                patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
                patch("core.wifi.os.geteuid", return_value=0), \
                patch.object(WiFiManager, "_get_interface_ip", return_value=None), \
                patch("core.wifi.time.monotonic", side_effect=[0, 0, 100]), \
                patch("core.wifi.time.sleep"), \
                patch.object(WiFiManager, "_run_cmd", side_effect=responses):
            success, _, details = WiFiManager().connect_network(
                "Office", "secret123"
            )
        self.assertTrue(success)
        self.assertEqual(details.get("ip_address"), "Sin IP (DHCP pendiente)")
        self.assertEqual(details.get("error_code"), "NO_IP")


class TestScanDedupe(unittest.TestCase):

    def test_dedupe_keeps_in_use_bssid_even_if_weaker(self):
        output = (
            "*:Cafe:30:WPA2:AA\\:BB:6\n"
            ":Cafe:90:WPA2:CC\\:DD:11\n"
        )
        networks = WiFiManager()._parse_nmcli_output(output)
        self.assertEqual(len(networks), 1)
        self.assertTrue(networks[0].in_use)
        self.assertEqual(networks[0].signal_pct, 90)
        self.assertEqual(networks[0].bssid, "AA:BB")


if __name__ == "__main__":
    unittest.main()
