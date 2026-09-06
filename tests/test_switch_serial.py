"""
Unit tests for Switch Serial Console Auditor.
"""

import os
import unittest

os.environ["REI_DRY_RUN"] = "1"

from core.switch_serial import SwitchSerialHandler
from core.interfaces import NetworkSwitchDiagnosticData


class TestSwitchSerialHandler(unittest.TestCase):
    """Verifies baudrate negotiation, non-destructive IOS audit, ANSI stripping, and metrics extraction."""

    def setUp(self):
        self.handler = SwitchSerialHandler()

    def test_clean_ansi_escapes(self):
        """Verifies VT100 control codes, colors, and carriage returns are stripped cleanly."""
        vt100_input = "\x1b[32mSwitch-2960#\x1b[0m \x1b[2Jshow version\r\nCisco IOS Software"
        clean = self.handler.clean_ansi_escapes(vt100_input)
        self.assertNotIn("\x1b[", clean)
        self.assertIn("Switch-2960#", clean)
        self.assertIn("Cisco IOS Software", clean)

    def test_parse_switch_outputs(self):
        """Verifies parsing of real Cisco IOS output blocks."""
        raw_outputs = {
            "show version": """
Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.0(2)SE4, RELEASE SOFTWARE (fc1)
cisco WS-C2960-24TT-L (PowerPC405) processor with 65536K bytes of memory.
Switch-2960 uptime is 4 weeks, 2 days, 1 hour, 15 minutes
""",
            "show interfaces status": """
Port      Name               Status       Vlan       Duplex  Speed Type
Fa0/1     Uplink             connected    1          a-full  a-100 10/100BaseTX
Fa0/2     Printer            notconnect   1            auto   auto 10/100BaseTX
Fa0/3     PC-Admin           connected    10         a-full  a-100 10/100BaseTX
Fa0/4     Security-Cam       err-disabled 20           auto   auto 10/100BaseTX
""",
            "show interfaces | include CRC|error": """
Fa0/1: 0 input errors, 0 CRC, 0 frame
Fa0/3: 45 input errors, 28 CRC, 0 frame
""",
            "show environment power": """
SW  PID                 Serial#     Status           Sys Pwr  PoE Pwr  Watts
--  ------------------  ----------  ---------------  -------  -------  -----
1   Internal Power                  OK
""",
            "show logging | include %": """
%LINK-3-UPDOWN: Interface FastEthernet0/4, changed state to down
%SPANTREE-2-BLOCK_BPDUGUARD: Received BPDU on port Fa0/4 with BPDU Guard enabled. Disabling port.
"""
        }

        parsed = self.handler._parse_switch_outputs(raw_outputs)
        self.assertIsInstance(parsed, NetworkSwitchDiagnosticData)
        self.assertEqual(parsed.hostname, "Switch-2960")
        self.assertEqual(parsed.model, "WS-C2960-24TT-L")
        self.assertIn("15.0(2)SE4", parsed.ios_version)
        self.assertEqual(parsed.ports_up, 2)
        self.assertEqual(parsed.ports_down, 2)
        self.assertEqual(parsed.ports_err_disabled, ["Fa0/4"])
        self.assertIn("Fa0/3", parsed.ports_with_crc_errors)
        self.assertEqual(parsed.ports_with_crc_errors["Fa0/3"], 28)
        self.assertEqual(len(parsed.critical_syslog), 2)

    def test_audit_switch_simulation(self):
        """Verifies simulated switch audit completes and returns populated telemetry."""
        result = self.handler.audit_switch(port="/dev/null_mock")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, NetworkSwitchDiagnosticData)
        self.assertEqual(result.hostname, "SW-CORE-PISO2")
        self.assertGreater(result.total_ports, 0)
        self.assertIn("PS1", result.power_supplies)


if __name__ == "__main__":
    unittest.main()
