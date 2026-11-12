"""
Tests for Wi-Fi Manager, OLED text sanitization/wrapping, and Wi-Fi diagnostic plugins.
"""

import os
import subprocess
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

from core.interfaces import DiagnosticStatus
from core.parser import sanitize_oled_text, shorten_oled_label, wrap_oled_lines
from core.plugins import (
    WiFiConnectPlugin,
    WiFiDisconnectPlugin,
    WiFiForgetPlugin,
    WiFiSavedListPlugin,
    WiFiScanPlugin,
)
from core.wifi import WiFiErrorCode, WiFiManager, validate_wifi_password


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestOLEDTextHelpers(unittest.TestCase):

    def test_sanitize_oled_text_replaces_spanish_and_symbols(self):
        self.assertEqual(sanitize_oled_text("Contraseña inválida"), "Contrasena invalida")
        self.assertEqual(sanitize_oled_text("SEÑAL ÑOÑO ¿ÁÉÍÓÚÜ?"), "SENAL NONO AEIOUU?")
        self.assertEqual(sanitize_oled_text("[✓] OK / [✗] ERR • 42°"), "[OK] OK / [X] ERR * 42C")
        for ch in sanitize_oled_text("Café_Peña 🚀\n\t"):
            self.assertTrue(32 <= ord(ch) <= 126)

    def test_wrap_oled_lines_avoids_mid_word_cuts(self):
        wrapped = wrap_oled_lines(["Contrasena incorrecta revise su clave"], max_chars=19)
        self.assertGreaterEqual(len(wrapped), 2)
        for line in wrapped:
            self.assertLessEqual(len(line), 19)
        self.assertEqual(" ".join(wrapped), "Contrasena incorrecta revise su clave")

    def test_shorten_oled_label(self):
        self.assertEqual(shorten_oled_label("Red_Peña_VeryLongSSID", 14), "Red_Pena_Ver..")
        self.assertEqual(shorten_oled_label("Short", 14), "Short")


class TestWiFiManager(unittest.TestCase):

    def setUp(self):
        self._env_patch = patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"})
        self._env_patch.start()
        self.mgr = WiFiManager(interface="wlan0")

    def tearDown(self):
        self._env_patch.stop()

    def test_split_nmcli_terse_line_handles_escaped_colons(self):
        line = r"*:My\:WiFi\\Lab:88:WPA2:AA\:BB\:CC\:DD\:EE\:FF:36"
        parts = self.mgr._split_nmcli_terse_line(line)
        self.assertEqual(parts, ["*", r"My:WiFi\Lab", "88", "WPA2", "AA:BB:CC:DD:EE:FF", "36"])

    def test_parse_nmcli_output_preserves_in_use_across_bssids_and_annotates_saved(self):
        raw = (
            r" :Corp_Mesh:95:WPA2:11\:22\:33\:44\:55\:66:36" + "\n"
            r"*:Corp_Mesh:72:WPA2:AA\:BB\:CC\:DD\:EE\:FF:6" + "\n"
            r" :Saved_Home:60:WPA2:22\:33\:44\:55\:66\:77:11" + "\n"
            r" :Open_Guest:80:--:33\:44\:55\:66\:77\:88:1" + "\n"
        )
        from core.wifi import SavedWiFiProfile
        saved_map = {
            "Saved_Home": SavedWiFiProfile(ssid="Saved_Home", profile_id="uuid-home", name="Saved_Home"),
        }
        nets = self.mgr._parse_nmcli_output(raw, saved_by_ssid=saved_map)
        self.assertEqual(len(nets), 3)
        # Active network sorted first and in_use preserved despite stronger BSSID
        self.assertEqual(nets[0].ssid, "Corp_Mesh")
        self.assertTrue(nets[0].in_use)
        self.assertTrue(nets[0].is_saved)
        self.assertEqual(nets[0].frequency, "2.4GHz")
        self.assertEqual(nets[0].channel, 6)
        # Saved network sorted before stronger open network
        self.assertEqual(nets[1].ssid, "Saved_Home")
        self.assertTrue(nets[1].is_saved)
        self.assertEqual(nets[1].saved_profile_id, "uuid-home")
        self.assertEqual(nets[2].ssid, "Open_Guest")
        self.assertFalse(nets[2].is_secured)

    def test_scan_does_not_return_mock_networks_when_wifi_disabled(self):
        def fake_which(bin_name):
            return "/usr/bin/nmcli" if bin_name == "nmcli" else None

        def fake_run(cmd, **kwargs):
            self.assertEqual(kwargs.get("env", {}).get("LC_ALL"), "C")
            if "connection" in cmd:
                return _cp(0, "")
            return _cp(1, "", "Error: Wi-Fi is disabled.")

        with patch("core.wifi.shutil.which", side_effect=fake_which), \
             patch("core.wifi.subprocess.run", side_effect=fake_run):
            nets, err, code = self.mgr.scan_networks_detailed()
            self.assertEqual(nets, [])
            self.assertEqual(code, WiFiErrorCode.WIFI_DISABLED)
            self.assertEqual(err, "Wi-Fi desactivado")

    def test_connect_already_connected_skips_nmcli_connect(self):
        calls = []

        def fake_which(bin_name):
            return "/usr/bin/nmcli" if bin_name == "nmcli" else None

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "--active" in cmd:
                return _cp(0, "MyNet:uuid-1:802-11-wireless:wlan0\n")
            if "-g" in cmd:
                return _cp(0, "MyNet\nwpa-psk\n")
            return _cp(0, "")

        with patch("core.wifi.shutil.which", side_effect=fake_which), \
             patch("core.wifi.subprocess.run", side_effect=fake_run), \
             patch.object(self.mgr, "_get_interface_ip", return_value="192.168.1.50"):
            ok, msg, details = self.mgr.connect_network("MyNet", password="correct")
            self.assertTrue(ok)
            self.assertTrue(details.get("already_connected"))
            self.assertEqual(details.get("error_code"), WiFiErrorCode.ALREADY_CONNECTED.value)
            # Must NOT run `device wifi connect`
            self.assertFalse(any("connect" in c for c in calls))

    def test_connect_enterprise_rejected_cleanly(self):
        with patch("core.wifi.shutil.which", return_value="/usr/bin/nmcli"), \
             patch.object(self.mgr, "get_active_connection", return_value=None), \
             patch.object(self.mgr, "_find_profiles_for_ssid", return_value=[]):
            ok, msg, details = self.mgr.connect_network("Corp_Net", password="abc", security_hint="WPA2-Enterprise")
            self.assertFalse(ok)
            self.assertEqual(msg, "Requiere 802.1X")
            self.assertEqual(details.get("error_code"), WiFiErrorCode.ENTERPRISE_UNSUPPORTED.value)

    def test_connect_saved_network_uses_nmcli_con_up_and_prompts_on_auth_fail(self):
        from core.wifi import SavedWiFiProfile
        prof = SavedWiFiProfile(ssid="HomeWiFi", profile_id="uuid-home", name="HomeWiFi")

        def fake_run(cmd, **kwargs):
            if "up" in cmd:
                return _cp(4, "", "Error: Connection activation failed: Secrets were required, but not provided.")
            return _cp(0, "")

        with patch("core.wifi.shutil.which", side_effect=lambda x: "/usr/bin/nmcli" if x == "nmcli" else None), \
             patch.object(self.mgr, "get_active_connection", return_value=None), \
             patch.object(self.mgr, "_find_profiles_for_ssid", return_value=[prof]), \
             patch("core.wifi.subprocess.run", side_effect=fake_run):
            ok, msg, details = self.mgr.connect_network("HomeWiFi", password=None)
            self.assertFalse(ok)
            self.assertEqual(details.get("error_code"), WiFiErrorCode.SECRETS_REQUIRED.value)
            self.assertTrue(details.get("needs_password"))
            self.assertEqual(msg, "Clave requerida")

    def test_connect_with_password_cleans_conflicting_profile_and_retries(self):
        from core.wifi import SavedWiFiProfile
        stale = SavedWiFiProfile(ssid="HomeWiFi", profile_id="uuid-stale", name="HomeWiFi")
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if "connect" in cmd:
                connect_attempts = sum(1 for c in commands if "connect" in c)
                if connect_attempts == 1:
                    return _cp(4, "", "Error: Connection activation failed: Secrets were required, but not provided.")
                return _cp(0, "Device 'wlan0' successfully activated.")
            if "delete" in cmd:
                return _cp(0, "")
            return _cp(0, "")

        with patch("core.wifi.shutil.which", side_effect=lambda x: "/usr/bin/nmcli" if x == "nmcli" else None), \
             patch.object(self.mgr, "get_active_connection", return_value=None), \
             patch.object(self.mgr, "_find_profiles_for_ssid", return_value=[stale]), \
             patch.object(self.mgr, "_wait_for_ip", return_value="192.168.1.99"), \
             patch("core.wifi.subprocess.run", side_effect=fake_run):
            ok, msg, details = self.mgr.connect_network("HomeWiFi", password="new-valid-password")
            self.assertTrue(ok)
            self.assertEqual(details.get("ip_address"), "192.168.1.99")
            self.assertTrue(any("delete" in c and "uuid-stale" in c for c in commands))

    def test_error_classification_mappings(self):
        cases = [
            ("Error: No network with SSID 'X' found.", True, WiFiErrorCode.NO_NETWORK_FOUND, "Red no encontrada"),
            ("Error: IP configuration could not be reserved (no available address, timeout, etc.).", True, WiFiErrorCode.DHCP_FAILED, "Sin IP por DHCP"),
            ("Error: Timeout 90 sec expired.", True, WiFiErrorCode.TIMEOUT, "Tiempo agotado"),
            ("Error: Wi-Fi is disabled by hardware switch.", False, WiFiErrorCode.WIFI_DISABLED, "Wi-Fi desactivado"),
            ("Error: Connection activation failed: 802-11-wireless-security.psk invalid", True, WiFiErrorCode.AUTH_FAILED, "Clave incorrecta"),
        ]
        for raw, had_pw, expected_code, expected_msg in cases:
            code, msg, hint = self.mgr._classify_error(raw, had_password=had_pw)
            self.assertEqual(code, expected_code, f"Failed on: {raw}")
            self.assertEqual(msg, expected_msg, f"Failed on: {raw}")
            self.assertLessEqual(len(msg), 19)
            self.assertLessEqual(len(hint), 19)
            self.assertEqual(msg, sanitize_oled_text(msg))
            self.assertEqual(hint, sanitize_oled_text(hint))

    def test_wpa_cli_fallback_verifies_handshake_failure_and_cleans_up(self):
        commands = []
        status_seq = iter([
            "wpa_state=ASSOCIATING\n",
            "wpa_state=4WAY_HANDSHAKE\n",
            "wpa_state=DISCONNECTED\n",
        ])

        def fake_which(bin_name):
            return "/sbin/wpa_cli" if bin_name == "wpa_cli" else None

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if "list_networks" in cmd:
                return _cp(0, "network id / ssid / bssid / flags\n")
            if "add_network" in cmd:
                return _cp(0, "3\n")
            if "set_network" in cmd or "enable_network" in cmd or "select_network" in cmd or "remove_network" in cmd:
                return _cp(0, "OK\n")
            if "status" in cmd:
                return _cp(0, next(status_seq, "wpa_state=DISCONNECTED\n"))
            return _cp(0, "OK\n")

        with patch("core.wifi.shutil.which", side_effect=fake_which), \
             patch("core.wifi.subprocess.run", side_effect=fake_run), \
             patch("core.wifi.time.sleep", return_value=None):
            ok, msg, details = self.mgr.connect_network("WPA_Test", password="badpassword", timeout=5)
            self.assertFalse(ok)
            self.assertEqual(details.get("error_code"), WiFiErrorCode.AUTH_FAILED.value)
            self.assertEqual(msg, "Clave incorrecta")
            self.assertTrue(any("remove_network" in c and "3" in c for c in commands))

    def test_dry_run_lifecycle_active_saved_update_disconnect_forget(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "1"}), \
             patch("core.wifi.time.sleep", return_value=None):
            mgr = WiFiManager()
            nets = mgr.scan_networks()
            self.assertTrue(any(n.ssid == "REI_Lab_AP" and n.in_use for n in nets))
            self.assertTrue(any(n.ssid == "IT_Dep_Secure" and n.is_saved for n in nets))

            # Already connected network returns already_connected
            ok, _, det = mgr.connect_network("REI_Lab_AP")
            self.assertTrue(ok)
            self.assertTrue(det.get("already_connected"))

            # Saved network with matching password connects without password
            ok, _, det = mgr.connect_saved_network("IT_Dep_Secure")
            self.assertTrue(ok)
            self.assertEqual(mgr.get_active_connection()["ssid"], "IT_Dep_Secure")

            # Saved network with changed password fails and requests new password
            ok, _, det = mgr.connect_saved_network("Oficina_Clave")
            self.assertFalse(ok)
            self.assertTrue(det.get("needs_password"))

            # Updating password succeeds and persists
            ok, _, _ = mgr.connect_network("Oficina_Clave", password="new-password", force_update=True)
            self.assertTrue(ok)
            self.assertEqual(mgr.get_active_connection()["ssid"], "Oficina_Clave")

            # Disconnect and forget
            ok, _, _ = mgr.disconnect_network("Oficina_Clave")
            self.assertTrue(ok)
            self.assertIsNone(mgr.get_active_connection())
            ok, _, _ = mgr.forget_network("Oficina_Clave")
            self.assertTrue(ok)
            self.assertFalse(any(p.ssid == "Oficina_Clave" for p in mgr.list_saved_profiles()))


class TestWiFiPlugins(unittest.TestCase):

    def test_wifi_plugins_produce_ascii_safe_concise_details(self):
        with patch.dict(os.environ, {"REI_DRY_RUN": "1"}), \
             patch("core.wifi.time.sleep", return_value=None):
            mgr = WiFiManager()
            scan_p = WiFiScanPlugin(wifi_mgr=mgr)
            conn_p = WiFiConnectPlugin(wifi_mgr=mgr)
            saved_p = WiFiSavedListPlugin(wifi_mgr=mgr)
            disc_p = WiFiDisconnectPlugin(wifi_mgr=mgr)
            forg_p = WiFiForgetPlugin(wifi_mgr=mgr)

            scan_res = scan_p.run()
            self.assertEqual(scan_res.status, DiagnosticStatus.SUCCESS)
            self.assertTrue(any(n["in_use"] for n in scan_res.metrics["networks"]))

            # Wrong password error details must be clean ASCII <=19 chars
            fail_res = conn_p.run(ssid="Switch_Mgmt", password="wrong")
            self.assertEqual(fail_res.status, DiagnosticStatus.FAILED)
            for line in fail_res.details:
                self.assertLessEqual(len(line), 19)
                self.assertEqual(line, sanitize_oled_text(line))
            self.assertIn("Clave incorrecta", fail_res.details)

            saved_res = saved_p.run()
            self.assertEqual(saved_res.status, DiagnosticStatus.SUCCESS)
            self.assertGreaterEqual(saved_res.metrics["count"], 2)

            disc_res = disc_p.run(ssid="REI_Lab_AP")
            self.assertEqual(disc_res.status, DiagnosticStatus.SUCCESS)

            forg_res = forg_p.run(ssid="IT_Dep_Secure")
            self.assertEqual(forg_res.status, DiagnosticStatus.SUCCESS)


class TestREIAppWiFiFlows(unittest.TestCase):
    """End-to-end Wi-Fi navigation flow tests against main.REIApp in Dry-Run."""

    @classmethod
    def setUpClass(cls):
        cls._injected_pil = False
        cls._prev_modules = {}
        try:
            import PIL.Image  # noqa: F401
        except ImportError:
            cls._injected_pil = True
            pil_mod = types.ModuleType("PIL")
            img_mod = types.ModuleType("PIL.Image")
            draw_mod = types.ModuleType("PIL.ImageDraw")
            font_mod = types.ModuleType("PIL.ImageFont")
            img_mod.Image = MagicMock
            img_mod.new = MagicMock(return_value=MagicMock())
            draw_mod.ImageDraw = MagicMock
            draw_mod.Draw = MagicMock(return_value=MagicMock())
            font_mod.ImageFont = MagicMock
            font_mod.load_default = MagicMock(return_value=MagicMock())
            pil_mod.Image = img_mod
            pil_mod.ImageDraw = draw_mod
            pil_mod.ImageFont = font_mod
            for name, mod in (
                ("PIL", pil_mod),
                ("PIL.Image", img_mod),
                ("PIL.ImageDraw", draw_mod),
                ("PIL.ImageFont", font_mod),
            ):
                cls._prev_modules[name] = sys.modules.get(name)
                sys.modules[name] = mod

        for name in ("ui", "ui.display", "ui.keyboard_view", "main"):
            cls._prev_modules.setdefault(name, sys.modules.get(name))

        import main as rei_main
        cls.rei_main = rei_main

    @classmethod
    def tearDownClass(cls):
        if cls._injected_pil:
            for name, prev in cls._prev_modules.items():
                if prev is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = prev

    def setUp(self):
        self._env_patch = patch.dict(os.environ, {"REI_DRY_RUN": "1"})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def _create_app(self):
        with patch.object(self.rei_main, "GPIOInputHandler", return_value=MagicMock()), \
             patch.object(self.rei_main, "REIWebServer", return_value=MagicMock()), \
             patch.object(self.rei_main.signal, "signal"):
            return self.rei_main.REIApp()

    def test_menu_has_redes_guardadas_and_active_network_opens_menu_without_password(self):
        app = self._create_app()
        try:
            root = app.screen_manager.current_view
            util_deck = root.cards[0].submenu
            net_deck = util_deck.cards[0].submenu
            net_titles = [c.title for c in net_deck.cards]
            self.assertIn("ESCANEAR WI-FI", net_titles)
            self.assertIn("REDES GUARDADAS", net_titles)

            scan_res = app.diag_manager.get_plugin("diag_wifi_scan").run()
            app._display_scanned_wifi_networks(scan_res)
            scan_deck = app.screen_manager.current_view
            self.assertEqual(scan_deck.title, "REDES WI-FI")

            # First card is active REI_Lab_AP -> selecting pushes active menu, NOT keyboard
            first_card = scan_deck.cards[0]
            self.assertEqual(first_card.icon_name, "WIFI_OK")
            self.assertTrue(first_card.title.startswith("* "))
            first_card.on_select()
            active_menu = app.screen_manager.current_view
            self.assertEqual([c.title for c in active_menu.cards], ["ESTADO RED", "DESCONECTAR", "OLVIDAR RED"])

            # Saved network failure with stale key offers NUEVA CLAVE first
            from core.interfaces import DiagnosticResult
            fail_saved = DiagnosticResult(
                plugin_name="CONECTAR WI-FI",
                status=DiagnosticStatus.FAILED,
                summary="Clave incorrecta",
                details=["Clave incorrecta", "Red: Oficina_Clave", "Ingrese nueva clave"],
                metrics={"error_code": "AUTH_FAILED", "needs_password": True},
            )
            app._display_wifi_result_hero_card(
                fail_saved, "Oficina_Clave", used_saved=True, source_deck=scan_deck
            )
            err_deck = app.screen_manager.current_view
            self.assertEqual(err_deck.cards[0].title, "NUEVA CLAVE")
            self.assertEqual(err_deck.cards[1].title, "CLAVE INVALIDA")

            # Clicking NUEVA CLAVE pops error deck and pushes WiFiPasswordView (NOT virtual matrix keyboard)
            err_deck.cards[0].on_select()
            pw_view = app.screen_manager.current_view
            self.assertIsInstance(pw_view, self.rei_main.WiFiPasswordView)
            self.assertTrue(getattr(pw_view, "accepts_text_input", False))

            # Simulate USB keyboard typing + submit via ENTER
            for ch in "new-password":
                pw_view.handle_input(self.rei_main.InputEvent.CHAR, char=ch)
            pw_view.handle_input(self.rei_main.InputEvent.ENTER)
            # WiFiPasswordView must be popped and replaced by UpdateProgressView
            self.assertIsInstance(app.screen_manager.current_view, self.rei_main.UpdateProgressView)
        finally:
            app.shutdown()

    def test_validate_wifi_password_and_rescan_retry_on_missing_ssid(self):
        ok, msg, _ = validate_wifi_password("1234567", security="WPA2-PSK")
        self.assertFalse(ok)
        self.assertEqual(msg, "Min. 8 caracteres")

        ok, msg, _ = validate_wifi_password("g" * 64, security="WPA2-PSK")
        self.assertFalse(ok)
        self.assertEqual(msg, "Max. 63 caracteres")

        ok, _, _ = validate_wifi_password("f" * 64, security="WPA2-PSK")
        self.assertTrue(ok)

        ok, msg, _ = validate_wifi_password("abc\ndef", security="WPA2-PSK")
        self.assertFalse(ok)
        self.assertEqual(msg, "Caracter no valido")

        ok, _, _ = validate_wifi_password("clave con espacio", security="WPA2-PSK")
        self.assertTrue(ok)

        # NO_NETWORK_FOUND on first nmcli connect triggers rescan + 1 retry
        mgr = WiFiManager(interface="wlan0")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "connect" in cmd:
                connect_count = sum(1 for c in calls if "connect" in c)
                if connect_count == 1:
                    return _cp(10, "", "Error: No network with SSID 'LateAP' found.")
                return _cp(0, "Device 'wlan0' successfully activated.")
            return _cp(0, "")

        with patch.dict(os.environ, {"REI_DRY_RUN": "0", "REI_MOCK_WIFI": "0"}), \
             patch("core.wifi.shutil.which", side_effect=lambda x: "/usr/bin/nmcli" if x == "nmcli" else None), \
             patch.object(mgr, "get_active_connection", return_value=None), \
             patch.object(mgr, "_find_profiles_for_ssid", return_value=[]), \
             patch.object(mgr, "_wait_for_ip", return_value="192.168.1.77"), \
             patch("core.wifi.time.sleep", return_value=None), \
             patch("core.wifi.subprocess.run", side_effect=fake_run):
            ok, _, details = mgr.connect_network("LateAP", password="valid-password-1")
            self.assertTrue(ok)
            self.assertEqual(details.get("ip_address"), "192.168.1.77")
            self.assertTrue(any("rescan" in c for c in calls))

    def test_ui_callbacks_drain_on_main_thread(self):
        app = self._create_app()
        try:
            ran = []
            app._schedule_ui(lambda: ran.append("ok"))
            self.assertEqual(ran, [])
            app._process_background_results()
            self.assertEqual(ran, ["ok"])
        finally:
            app.shutdown()


if __name__ == "__main__":
    unittest.main()
