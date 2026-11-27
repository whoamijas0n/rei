"""
Pruebas de integración del flujo completo de SWITCHES -> CONSOLA SERIAL:
- Jerarquía de menús (SWITCHES -> CONSOLA SERIAL -> AUTO/MANUAL -> TIPO PROBLEMA -> PROBLEMAS GENERALES)
- Plugin CiscoSerialPlugin (Fases 1, 2 y 3 con simulación y Netmiko simulado)
- Vista ConsoleCredentialView (teclado USB, enable opcional vacío, render 128x64)
- Análisis IA / heurística local para switches (GeminiDiagnosticAnalyzer)
- Almacenamiento y renderizado HTML móvil del informe (REIWebServer + report_renderer)
"""

import os
import unittest
from PIL import Image, ImageDraw

from core.gemini_analyzer import GeminiDiagnosticAnalyzer
from core.interfaces import DiagnosticStatus
from core.plugins import CiscoSerialPlugin
from core.report_renderer import render_report_html
from core.serial_console import ConsolePromptState, SerialConsoleManager, SerialPortInfo, SerialProbeResult
from core.web_server import REIWebServer
from plugins.switches import get_mock_cisco_2960s_outputs
from ui.console_auth_view import ConsoleCredentialView
from ui.display import ViewActionType
from ui.input_handler import InputEvent


class _FakeSwitchConn:
    """Conexión Netmiko falsa para validar Fase 3 con o sin enable."""

    def __init__(self, outputs, enable_fails: bool = False, in_enable: bool = True):
        self._outputs = outputs
        self._enable_fails = enable_fails
        self._in_enable = in_enable
        self.disconnected = False
        self.sent = []

    def check_enable_mode(self) -> bool:
        return self._in_enable

    def enable(self) -> None:
        if self._enable_fails:
            raise ValueError("Failed to enter enable mode")
        self._in_enable = True

    def send_command(self, cmd: str, **kwargs) -> str:
        self.sent.append(cmd)
        return self._outputs.get(cmd, "")

    def disconnect(self) -> None:
        self.disconnected = True


class TestConsoleCredentialView(unittest.TestCase):
    """Valida interacción de teclado USB y renderizado 128x64 de ConsoleCredentialView."""

    def test_typing_mask_toggle_and_submit(self) -> None:
        submitted = []
        view = ConsoleCredentialView(
            title="CONSOLA: CLAVE",
            field_label="Clave",
            masked=True,
            allow_empty=False,
            on_submit=lambda val: submitted.append(val),
        )
        # Rechazar envío vacío si allow_empty=False
        view.handle_input(InputEvent.ENTER)
        self.assertEqual(submitted, [])
        self.assertTrue(bool(view.error_message))

        for ch in "cisco123":
            view.handle_input(InputEvent.CHAR, char=ch)
        view.handle_input(InputEvent.BACKSPACE)
        self.assertEqual(view.input_text, "cisco12")

        self.assertFalse(view.is_visible)
        view.handle_input(InputEvent.KEY2)
        self.assertTrue(view.is_visible)

        view.handle_input(InputEvent.ENTER)
        self.assertEqual(submitted, ["cisco12"])

        img = Image.new("1", (128, 64), 0)
        draw = ImageDraw.Draw(img)
        view.render(draw, 128, 64)
        self.assertTrue(any(b != 0 for b in img.tobytes()))

    def test_optional_enable_allows_empty_enter_and_cancel_pops(self) -> None:
        submitted = []
        view = ConsoleCredentialView(
            title="CLAVE ENABLE",
            field_label="Enable",
            masked=True,
            allow_empty=True,
            on_submit=lambda val: submitted.append(val),
        )
        view.handle_input(InputEvent.ENTER)
        self.assertEqual(submitted, [""])

        view2 = ConsoleCredentialView(title="USUARIO", masked=False)
        act = view2.handle_input(InputEvent.KEY3)
        self.assertEqual(act.action_type, ViewActionType.POP_VIEW)


class TestSwitchSerialEndToEnd(unittest.TestCase):
    """Valida CiscoSerialPlugin + WebServer + Gemini heurístico + HTML móvil."""

    def setUp(self) -> None:
        self.prev_dry = os.environ.get("REI_DRY_RUN")
        self.prev_scen = os.environ.get("REI_MOCK_SERIAL_SCENARIO")
        os.environ["REI_DRY_RUN"] = "1"
        os.environ["REI_MOCK_SERIAL_SCENARIO"] = "cisco_priv"

    def tearDown(self) -> None:
        if self.prev_dry is None:
            os.environ.pop("REI_DRY_RUN", None)
        else:
            os.environ["REI_DRY_RUN"] = self.prev_dry
        if self.prev_scen is None:
            os.environ.pop("REI_MOCK_SERIAL_SCENARIO", None)
        else:
            os.environ["REI_MOCK_SERIAL_SCENARIO"] = self.prev_scen

    def test_full_auto_detect_report_ai_and_html(self) -> None:
        web_server = REIWebServer(port=0)
        plugin = CiscoSerialPlugin(web_server=web_server)

        progress_log = []
        res = plugin.run(
            progress_callback=lambda m, p: progress_log.append((m, p)),
            profile_id=None,
            category="GENERAL",
        )

        self.assertEqual(res.status, DiagnosticStatus.SUCCESS)
        self.assertEqual(res.plugin_name, "CONSOLA SERIAL")
        self.assertEqual(res.metadata["device_kind"], "SWITCH")
        self.assertEqual(res.metadata["vendor_id"], "cisco_ios")
        self.assertEqual(res.metadata["category"], "GENERAL")
        self.assertGreater(len(progress_log), 0)

        report_id = res.metadata["report_id"]
        stored = web_server.get_report(report_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.hostname, "SW-2960S-POE")
        self.assertIn("poe", stored.switch_audit)

        # Análisis heurístico local de Gemini para switches (sin API key)
        analyzer = GeminiDiagnosticAnalyzer(api_key=None)
        ai_res = analyzer.analyze_diagnostic(
            {
                "device_kind": "SWITCH",
                "os_type": stored.os_type,
                "category": stored.category,
                "hostname": stored.hostname,
                "switch_audit": stored.switch_audit,
            }
        )
        self.assertEqual(ai_res["overall_status"], "CRIT")
        self.assertTrue(any("err-disabled" in c.lower() for c in ai_res["root_causes"]))
        web_server.attach_ai_analysis(report_id, ai_res, overall_status=ai_res["overall_status"])

        html = render_report_html(web_server.get_report(report_id))
        self.assertIn("SW-2960S-POE", html)
        self.assertIn("WS-C2960S-48FPD-L", html)
        self.assertIn("Energía PoE", html)
        self.assertIn("bpduguard", html)
        self.assertIn("CORE-3850", html)

    def test_phase2_auth_required_and_optional_enable_fallback(self) -> None:
        os.environ["REI_MOCK_SERIAL_SCENARIO"] = "cisco_login"
        plugin = CiscoSerialPlugin()

        r1 = plugin.run(category="GENERAL")
        self.assertEqual(r1.status, DiagnosticStatus.WARNING)
        self.assertTrue(r1.metadata.get("auth_required"))
        self.assertEqual(r1.metadata.get("auth_stage"), "username")

        # Reanudar con credenciales
        r2 = plugin.run(
            category="GENERAL",
            credentials={"username": "admin", "password": "secret"},
            probe_hint=r1.metadata["probe"],
        )
        self.assertEqual(r2.status, DiagnosticStatus.SUCCESS)

        # Escenario enable opcional: cuando enable falla, solicita enable; con skip_enable=True completa en '>'
        mock_out = get_mock_cisco_2960s_outputs()
        cmd_map = {
            "show version": mock_out["version"],
            "show env all": mock_out["env"],
            "show processes cpu | include CPU": mock_out["cpu"],
            "show processes memory | include Processor Pool": mock_out["memory"],
            "show power inline": mock_out["poe"],
            "show interfaces status": mock_out["interfaces_status"],
            "show ip interface brief": mock_out["ip_int_brief"],
            "show interfaces counters errors": mock_out["interface_errors"],
            "show interfaces status err-disabled": mock_out["err_disabled"],
            "show vlan brief": mock_out["vlans"],
            "show spanning-tree summary": mock_out["stp"],
            "show cdp neighbors": mock_out["cdp"],
        }

        probe_hint = {
            "device": "/dev/ttyUSB0",
            "port_kind": "USB_RJ45",
            "baudrate": 9600,
            "prompt_state": ConsolePromptState.USER_EXEC.value,
            "banner_text": "Banner privado sin marca",
            "prompt_line": "SW-2960S-POE>",
            "detected_vendor": None,
            "hostname_hint": "SW-2960S-POE",
            "signals": {"dsr": True, "cts": True},
        }

        # 1) enable() falla -> pide clave enable opcional
        conn_en_fail = _FakeSwitchConn(cmd_map, enable_fails=True, in_enable=False)
        p_real = CiscoSerialPlugin(connection_factory=lambda **kw: conn_en_fail)
        r_en = p_real.run(category="GENERAL", probe_hint=probe_hint)
        self.assertTrue(r_en.metadata.get("auth_required"))
        self.assertEqual(r_en.metadata.get("auth_stage"), "enable")
        self.assertTrue(conn_en_fail.disconnected)

        # 2) skip_enable=True -> identifica marca por sonda show version y recolecta en modo '>'
        conn_ro = _FakeSwitchConn(cmd_map, enable_fails=True, in_enable=False)
        p_real2 = CiscoSerialPlugin(connection_factory=lambda **kw: conn_ro)
        r_ro = p_real2.run(category="GENERAL", probe_hint=probe_hint, skip_enable=True)
        self.assertEqual(r_ro.status, DiagnosticStatus.SUCCESS)
        self.assertEqual(r_ro.metadata["vendor_id"], "cisco_ios")
        self.assertFalse(r_ro.metrics["switch_audit"]["connection"]["privileged"])
        self.assertTrue(conn_ro.disconnected)

    def test_netmiko_auth_error_without_credentials_prompts_user_instead_of_failing(self) -> None:
        from core import plugins as core_plugins

        class _AuthFail(core_plugins._NetmikoAuthError):
            pass

        def fail_factory(**kw):
            raise _AuthFail("Authentication failed: Password required")

        probe_hint = {
            "device": "/dev/ttyACM0",
            "port_kind": "USB_DIRECT",
            "baudrate": 9600,
            "prompt_state": ConsolePromptState.UNKNOWN_PROMPT.value,
            "banner_text": "Banner privado",
            "prompt_line": "Banner privado",
            "detected_vendor": "cisco_ios",
            "hostname_hint": "",
            "signals": {"dsr": False, "cts": True},
        }
        plugin = CiscoSerialPlugin(connection_factory=fail_factory)

        # Sin credenciales previas -> debe pedirlas por teclado USB (auth_required=True)
        r_ask = plugin.run(category="GENERAL", probe_hint=probe_hint)
        self.assertEqual(r_ask.status, DiagnosticStatus.WARNING)
        self.assertTrue(r_ask.metadata.get("auth_required"))
        self.assertEqual(r_ask.metadata.get("auth_stage"), "password")

        # Con credenciales ya ingresadas y rechazadas -> AUTH FALLIDA
        r_fail = plugin.run(
            category="GENERAL",
            probe_hint=probe_hint,
            credentials={"password": "wrong"},
        )
        self.assertEqual(r_fail.status, DiagnosticStatus.FAILED)
        self.assertEqual(r_fail.summary, "AUTH FALLIDA")


class TestSwitchesMenuHierarchy(unittest.TestCase):
    """Valida el árbol definitivo SWITCHES -> CONSOLA SERIAL -> AUTO/MANUAL -> TIPO PROBLEMA."""

    def test_main_menu_structure_and_hid_guard(self) -> None:
        prev_dry = os.environ.get("REI_DRY_RUN")
        os.environ["REI_DRY_RUN"] = "1"
        try:
            from main import REIApp

            app = REIApp()
            try:
                root = app.screen_manager.current_view
                root_titles = [c.title for c in root.cards]
                self.assertIn("SWITCHES", root_titles)
                self.assertNotIn("SWITCHES / RED", root_titles)

                sw_card = next(c for c in root.cards if c.title == "SWITCHES")
                sw_titles = [c.title for c in sw_card.submenu.cards]
                self.assertEqual(sw_titles[0], "CONSOLA SERIAL")

                serial_deck = sw_card.submenu.cards[0].submenu
                serial_titles = [c.title for c in serial_deck.cards]
                self.assertEqual(serial_titles, ["AUTO-DETECTAR", "PERFIL MANUAL"])

                # AUTO-DETECTAR -> TIPO PROBLEMA -> PROBLEMAS GENERALES
                cat_deck = serial_deck.cards[0].submenu
                self.assertEqual(cat_deck.title, "TIPO PROBLEMA")
                self.assertEqual([c.title for c in cat_deck.cards], ["PROBLEMAS GENERALES"])

                # PERFIL MANUAL -> CISCO IOS / XE -> TIPO PROBLEMA -> PROBLEMAS GENERALES
                manual_deck = serial_deck.cards[1].submenu
                manual_titles = [c.title for c in manual_deck.cards]
                self.assertIn("CISCO IOS / XE", manual_titles)
                self.assertIn("TP-LINK", manual_titles)
                self.assertIn("GENERICO", manual_titles)
                cisco_cat = manual_deck.cards[0].submenu
                self.assertEqual(cisco_cat.title, "TIPO PROBLEMA")
                self.assertEqual([c.title for c in cisco_cat.cards], ["PROBLEMAS GENERALES"])

                # Guardia USB HID bloqueando puerto host
                app._is_usb_gadget_blocking_host = lambda: True  # type: ignore
                app._start_switch_serial_diagnostic()
                self.assertEqual(app.screen_manager.current_view.title, "MODO HID ACTIVO")
            finally:
                app.shutdown()
        finally:
            if prev_dry is None:
                os.environ.pop("REI_DRY_RUN", None)
            else:
                os.environ["REI_DRY_RUN"] = prev_dry


if __name__ == "__main__":
    unittest.main()
