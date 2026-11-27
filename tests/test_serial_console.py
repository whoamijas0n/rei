"""
Pruebas unitarias para la capa de transporte serial y detección pasiva (core/serial_console.py).
"""

import os
import unittest
from typing import Dict, List

from core.serial_console import (
    ConsolePromptState,
    SerialConsoleManager,
    SerialPortInfo,
    classify_prompt_state,
    clean_console_text,
    detect_vendor_passive,
    extract_hostname_hint,
    is_legible_ascii,
)


class _FakeSerial:
    """Puerto serial simulado en memoria para probar auto-baud y estados de consola."""

    def __init__(self, responses_by_baud: Dict[int, List[bytes]], baudrate: int, **kwargs):
        self.baudrate = baudrate
        self.dsr = True
        self.cts = True
        self.cd = False
        self.written: List[bytes] = []
        self._queue: List[bytes] = list(responses_by_baud.get(baudrate, []))
        self._current_buf: bytes = b""
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._current_buf)

    def reset_input_buffer(self) -> None:
        self._current_buf = b""

    def reset_output_buffer(self) -> None:
        pass

    def flush(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        self.written.append(data)
        if self._queue:
            self._current_buf += self._queue.pop(0)
        return len(data)

    def read(self, size: int = 256) -> bytes:
        if not self._current_buf:
            return b""
        out = self._current_buf[:size]
        self._current_buf = self._current_buf[size:]
        return out

    def close(self) -> None:
        self.closed = True


class TestSerialConsoleHelpers(unittest.TestCase):
    """Valida utilidades puras de limpieza, calidad ASCII, clasificación y huella pasiva."""

    def test_is_legible_ascii_rejects_baud_garbage(self) -> None:
        self.assertFalse(is_legible_ascii(b""))
        self.assertFalse(is_legible_ascii(b"\x00\x00\x00"))
        self.assertFalse(is_legible_ascii(b"\xff\xfe\x80\xf3\xc2\x91\xe7\xba"))
        self.assertTrue(is_legible_ascii(b"\r\nSwitch-2960S#\r\n"))
        self.assertTrue(is_legible_ascii(b"\x1b[2J\x1b[HUser Access Verification\r\nUsername:"))

    def test_clean_console_text_strips_ansi_and_nulls(self) -> None:
        raw = b"\x00\x1b[32mSW-2960S#\x1b[0m\r\n"
        self.assertEqual(clean_console_text(raw), "SW-2960S#\n")

    def test_classify_prompt_states(self) -> None:
        cases = [
            ("", ConsolePromptState.NO_RESPONSE),
            ("Press RETURN to get started!\n", ConsolePromptState.PRESS_RETURN),
            (
                "Would you like to enter the initial configuration dialog? [yes/no]: ",
                ConsolePromptState.INITIAL_CONFIG_DIALOG,
            ),
            ("User Access Verification\n\nUsername: ", ConsolePromptState.LOGIN_USERNAME),
            ("TP-LINK Switch\nLogin:", ConsolePromptState.LOGIN_USERNAME),
            ("User Access Verification\nPassword:", ConsolePromptState.LOGIN_PASSWORD),
            ("SW-2960S-POE>", ConsolePromptState.USER_EXEC),
            ("[admin@MikroTik] >", ConsolePromptState.USER_EXEC),
            ("<Huawei-Core>", ConsolePromptState.USER_EXEC),
            ("SW-2960S-POE#", ConsolePromptState.PRIV_EXEC),
            ("[Huawei-Core]", ConsolePromptState.PRIV_EXEC),
        ]
        for sample, expected in cases:
            state, _ = classify_prompt_state(sample)
            self.assertEqual(state, expected, f"Fallo clasificando: {sample!r}")

    def test_detect_vendor_passive(self) -> None:
        self.assertEqual(detect_vendor_passive("Cisco IOS Software, C2960S", "SW#"), "cisco_ios")
        self.assertEqual(detect_vendor_passive("User Access Verification\nUsername:"), "cisco_ios")
        self.assertEqual(detect_vendor_passive("TP-LINK JetStream TL-SG3428MP", "User:"), "tplink_jetstream")
        self.assertEqual(detect_vendor_passive("HP ProCurve Switch 2910al", "ProCurve#"), "hp_procurve")
        self.assertEqual(detect_vendor_passive("ArubaOS-CX 10.10", "Switch#"), "aruba_cx")
        self.assertEqual(detect_vendor_passive("MikroTik RouterOS", "[admin@Core] >"), "mikrotik")
        self.assertEqual(detect_vendor_passive("HUAWEI VRP CloudEngine", "<Core>"), "huawei")
        self.assertEqual(detect_vendor_passive("Samsung Ubigate Switch", "SW#"), "samsung")
        self.assertIsNone(detect_vendor_passive("Banner Corporativo Privado", "SW01#"))

    def test_extract_hostname_hint(self) -> None:
        self.assertEqual(extract_hostname_hint("SW-2960S-POE#"), "SW-2960S-POE")
        self.assertEqual(extract_hostname_hint("Switch>"), "Switch")
        self.assertEqual(extract_hostname_hint("[admin@Core-MT] >"), "Core-MT")
        self.assertEqual(extract_hostname_hint("<Core-HW>"), "Core-HW")
        self.assertEqual(extract_hostname_hint("Username:"), "")


class TestSerialConsoleManager(unittest.TestCase):
    """Valida auto-baud, manejo de diálogos iniciales y simulación DRY-RUN."""

    def test_probe_port_auto_baud_switches_to_115200_on_garbled_9600(self) -> None:
        instances: List[_FakeSerial] = []
        responses = {
            9600: [b"\xff\xfc\x89\xe0\x91\xba", b"\xfe\x80\x99\xc3"],
            115200: [b"\r\nCisco Catalyst 2960-S\r\nSW-2960S#\r\n"],
        }

        def factory(**kwargs):
            fs = _FakeSerial(responses, **kwargs)
            instances.append(fs)
            return fs

        mgr = SerialConsoleManager(
            baudrates=(9600, 115200, 38400),
            read_timeout_s=0.15,
            serial_factory=factory,
        )
        port = SerialPortInfo(device="/dev/ttyACM0", kind="USB_DIRECT")
        res = mgr.probe_port(port)

        self.assertTrue(res.is_responsive)
        self.assertEqual(res.baudrate, 115200)
        self.assertEqual(res.prompt_state, ConsolePromptState.PRIV_EXEC)
        self.assertEqual(res.detected_vendor, "cisco_ios")
        self.assertEqual(res.hostname_hint, "SW-2960S")
        # Verificar que Fase 1 solo envió '\r' y ningún comando ni contraseña
        for inst in instances:
            self.assertTrue(inst.closed)
            for payload in inst.written:
                self.assertEqual(payload, b"\r")

    def test_probe_port_handles_press_return_and_initial_dialog(self) -> None:
        responses = {
            9600: [
                b"Would you like to enter the initial configuration dialog? [yes/no]: ",
                b"\r\nPress RETURN to get started!\r\n",
                b"\r\nSwitch>\r\n",
            ]
        }

        def factory(**kwargs):
            return _FakeSerial(responses, **kwargs)

        mgr = SerialConsoleManager(baudrates=(9600,), read_timeout_s=0.15, serial_factory=factory)
        res = mgr.probe_port(SerialPortInfo(device="/dev/ttyUSB0", kind="USB_RJ45"))

        self.assertTrue(res.is_responsive)
        self.assertTrue(res.handled_initial_dialog)
        self.assertEqual(res.prompt_state, ConsolePromptState.USER_EXEC)
        self.assertEqual(res.hostname_hint, "Switch")

    def test_probe_port_drains_long_motd_banner_and_uses_usb_descriptor_hint(self) -> None:
        responses = {
            9600: [
                (
                    b"=================================================================\r\n"
                    b"                           WARNING\r\n"
                    b"  THIS IS A PRIVATE SYSTEM. ACCESS IS RESTRICTED.\r\n"
                    b"=================================================================\r\n"
                ),
                b"\r\nSW-CORE>\r\n",
            ]
        }

        def factory(**kwargs):
            return _FakeSerial(responses, **kwargs)

        mgr = SerialConsoleManager(baudrates=(9600,), read_timeout_s=0.15, serial_factory=factory)
        port = SerialPortInfo(
            device="/dev/ttyACM0",
            description="Cisco USB Console",
            manufacturer="Cisco",
            kind="USB_DIRECT",
        )
        res = mgr.probe_port(port)

        self.assertTrue(res.is_responsive)
        self.assertEqual(res.prompt_state, ConsolePromptState.USER_EXEC)
        self.assertEqual(res.hostname_hint, "SW-CORE")
        self.assertEqual(res.detected_vendor, "cisco_ios")

    def test_probe_port_recovers_from_cisco_translating_freeze(self) -> None:
        written_seq: List[bytes] = []
        responses = {
            9600: [
                b"",
                b"",
                b'\r\nTranslating "xyz"...domain server (255.255.255.255)\r\n',
                b"\r\n% Unknown command or computer name, or unable to find computer address\r\nSW-CORE>\r\n",
            ]
        }

        def factory(**kwargs):
            fs = _FakeSerial(responses, **kwargs)
            orig_write = fs.write

            def track_write(data: bytes) -> int:
                written_seq.append(data)
                return orig_write(data)

            fs.write = track_write  # type: ignore
            return fs

        mgr = SerialConsoleManager(baudrates=(9600,), read_timeout_s=0.15, serial_factory=factory)
        port = SerialPortInfo(
            device="/dev/ttyACM0",
            description="Cisco USB Console",
            manufacturer="Cisco",
            kind="USB_DIRECT",
        )
        res = mgr.probe_port(port)

        self.assertTrue(res.is_responsive)
        self.assertEqual(res.prompt_state, ConsolePromptState.USER_EXEC)
        self.assertEqual(res.hostname_hint, "SW-CORE")
        self.assertIn(b"\x1e\r", written_seq)

    def test_mock_scenarios_cover_rj45_minib_login_and_no_port(self) -> None:
        prev_dry = os.environ.get("REI_DRY_RUN")
        prev_scen = os.environ.get("REI_MOCK_SERIAL_SCENARIO")
        os.environ["REI_DRY_RUN"] = "1"
        try:
            mgr = SerialConsoleManager()

            os.environ["REI_MOCK_SERIAL_SCENARIO"] = "cisco_minib"
            ports = mgr.list_console_ports()
            self.assertEqual(len(ports), 1)
            self.assertEqual(ports[0].device, "/dev/ttyACM0")
            self.assertEqual(ports[0].kind, "USB_DIRECT")

            os.environ["REI_MOCK_SERIAL_SCENARIO"] = "cisco_login"
            probe_login = mgr.auto_probe()
            self.assertTrue(probe_login.requires_auth)
            self.assertEqual(probe_login.prompt_state, ConsolePromptState.LOGIN_USERNAME)

            os.environ["REI_MOCK_SERIAL_SCENARIO"] = "no_port"
            probe_empty = mgr.auto_probe()
            self.assertFalse(probe_empty.is_responsive)
            self.assertIsNone(probe_empty.port)
        finally:
            if prev_dry is None:
                os.environ.pop("REI_DRY_RUN", None)
            else:
                os.environ["REI_DRY_RUN"] = prev_dry
            if prev_scen is None:
                os.environ.pop("REI_MOCK_SERIAL_SCENARIO", None)
            else:
                os.environ["REI_MOCK_SERIAL_SCENARIO"] = prev_scen


if __name__ == "__main__":
    unittest.main()

