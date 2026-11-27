"""
Pruebas unitarias para el registro de perfiles de switches y los parsers de Cisco IOS / IOS-XE
(validados contra salidas de un Cisco Catalyst 2960-S PoE 10G).
"""

import unittest
from typing import Dict

from plugins.switches import (
    CiscoIOSProfile,
    DEFAULT_SWITCH_REGISTRY,
    SWITCH_MENU_CATEGORIES,
    get_mock_cisco_2960s_outputs,
    get_switch_category_label,
    normalize_switch_category,
    sanitize_credentials_from_text,
)
from plugins.switches.cisco_ios import (
    parse_cdp_neighbors,
    parse_cpu,
    parse_err_disabled,
    parse_interface_errors,
    parse_interfaces_status,
    parse_ip_int_brief,
    parse_logging,
    parse_memory,
    parse_power_inline,
    parse_show_env,
    parse_show_version,
    parse_stp_summary,
    parse_vlan_brief,
)


class _FakeNetmikoConn:
    """Conexión Netmiko falsa para validar collect_audit y sanitización."""

    def __init__(self, responses: Dict[str, str]):
        self._responses = responses
        self.sent_commands = []

    def send_command(self, command: str, **kwargs) -> str:
        self.sent_commands.append(command)
        return self._responses.get(command, "")


class TestSwitchCategoriesAndRegistry(unittest.TestCase):
    """Valida normalización de categorías y detección en el registro de perfiles."""

    def test_categories_default_and_menu_list(self) -> None:
        self.assertEqual(len(SWITCH_MENU_CATEGORIES), 1)
        self.assertEqual(SWITCH_MENU_CATEGORIES[0][0], "GENERAL")
        self.assertEqual(SWITCH_MENU_CATEGORIES[0][1], "PROBLEMAS GENERALES")
        self.assertEqual(normalize_switch_category("PROBLEMAS GENERALES"), "GENERAL")
        self.assertEqual(normalize_switch_category("tabla mac"), "MAC_TABLE")
        self.assertEqual(normalize_switch_category("desconocida"), "GENERAL")
        self.assertEqual(get_switch_category_label("GENERAL"), "PROBLEMAS GENERALES")

    def test_registry_passive_and_probe_detection(self) -> None:
        p_cisco = DEFAULT_SWITCH_REGISTRY.detect_passive("Cisco Catalyst 2960-S", "SW#")
        self.assertIsNotNone(p_cisco)
        self.assertEqual(p_cisco.vendor_id, "cisco_ios")

        p_tplink = DEFAULT_SWITCH_REGISTRY.detect_passive("TP-LINK JetStream", "User:")
        self.assertIsNotNone(p_tplink)
        self.assertEqual(p_tplink.vendor_id, "tplink_jetstream")

        p_none = DEFAULT_SWITCH_REGISTRY.detect_passive("Banner Privado", "SW01#")
        self.assertIsNone(p_none)

        p_probe = DEFAULT_SWITCH_REGISTRY.detect_from_probe(
            "Cisco IOS Software, C2960S Software (C2960S-UNIVERSALK9-M), Version 15.2(2)E8"
        )
        self.assertEqual(p_probe.vendor_id, "cisco_ios")

        p_fallback = DEFAULT_SWITCH_REGISTRY.detect_from_probe("Unknown OS 1.0")
        self.assertEqual(p_fallback.vendor_id, "generic")

    def test_sanitize_credentials_scrubs_secrets(self) -> None:
        raw = "Password: MiClaveSecreta123\nEnable: SuperEnable99\nOK"
        cleaned = sanitize_credentials_from_text(raw, ("MiClaveSecreta123", "SuperEnable99"))
        self.assertNotIn("MiClaveSecreta123", cleaned)
        self.assertNotIn("SuperEnable99", cleaned)
        self.assertIn("***", cleaned)


class TestCisco2960SParsers(unittest.TestCase):
    """Valida todos los parsers de Cisco IOS con datos de un Catalyst 2960-S PoE 10G."""

    def setUp(self) -> None:
        self.outputs = get_mock_cisco_2960s_outputs()
        self.profile = CiscoIOSProfile()

    def test_individual_parsers(self) -> None:
        ver = parse_show_version(self.outputs["version"])
        self.assertEqual(ver["hostname"], "SW-2960S-POE")
        self.assertEqual(ver["model"], "WS-C2960S-48FPD-L")
        self.assertEqual(ver["serial_number"], "FOC1724Y1AB")
        self.assertEqual(ver["ios_version"], "15.2(2)E8")

        env = parse_show_env(self.outputs["env"])
        self.assertEqual(env["fan_status"], "OK")
        self.assertEqual(env["temperature_c"], 41)
        self.assertEqual(env["psu_status"], "OK")
        self.assertEqual(env["issues"], [])

        env_2960s_table = parse_show_env(
            "FAN is OK\nTEMPERATURE is OK\nTemperature Value: 35 Degree Celsius\n"
            "SW  PID  Serial#  Status\n 1  Built-in  Good\n"
        )
        self.assertEqual(env_2960s_table["psu_status"], "Good")
        self.assertEqual(env_2960s_table["issues"], [])

        cpu = parse_cpu(self.outputs["cpu"])
        self.assertEqual(cpu["cpu_5s"], 9)
        self.assertEqual(cpu["cpu_1m"], 11)
        self.assertEqual(cpu["cpu_5m"], 10)

        mem = parse_memory(self.outputs["memory"])
        self.assertAlmostEqual(mem["usage_pct"], 48.0, places=1)

        poe = parse_power_inline(self.outputs["poe"])
        self.assertTrue(poe["supported"])
        self.assertEqual(poe["budget_watts"], 740.0)
        self.assertEqual(poe["used_watts"], 118.4)
        self.assertEqual(poe["active_ports"], 2)
        self.assertEqual(poe["faulty_ports"], [])

        if_st = parse_interfaces_status(self.outputs["interfaces_status"])
        self.assertEqual(if_st["counts"]["connected"], 3)
        self.assertEqual(if_st["counts"]["err_disabled"], 1)
        self.assertEqual(len(if_st["uplinks_10g"]), 2)

        ips = parse_ip_int_brief(self.outputs["ip_int_brief"])
        self.assertEqual(len(ips), 1)
        self.assertEqual(ips[0]["ip"], "192.168.10.2")

        errs = parse_interface_errors(self.outputs["interface_errors"])
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["port"], "Gi1/0/2")
        self.assertEqual(errs[0]["fcs"], 18)

        err_dis = parse_err_disabled(self.outputs["err_disabled"])
        self.assertEqual(err_dis, [{"port": "Gi1/0/3", "reason": "bpduguard"}])

        vlans = parse_vlan_brief(self.outputs["vlans"])
        self.assertEqual(vlans["count"], 4)

        stp = parse_stp_summary(self.outputs["stp"])
        self.assertEqual(stp["mode"], "rapid-pvst")
        self.assertEqual(stp["forwarding"], 3)

        cdp = parse_cdp_neighbors(self.outputs["cdp"])
        self.assertEqual(len(cdp), 2)
        self.assertEqual(cdp[0]["device_id"], "CORE-3850")

        logs = parse_logging(self.outputs["logging"])
        self.assertGreaterEqual(len(logs["critical_events"]), 2)

    def test_collect_audit_read_only_user_mode_skips_privileged_commands(self) -> None:
        cmd_map = {
            "show version": self.outputs["version"],
            "show env all": self.outputs["env"],
            "show processes cpu | include CPU": self.outputs["cpu"],
            "show processes memory | include Processor Pool": self.outputs["memory"],
            "show power inline": self.outputs["poe"],
            "show interfaces status": self.outputs["interfaces_status"],
            "show ip interface brief": self.outputs["ip_int_brief"],
            "show interfaces counters errors": self.outputs["interface_errors"],
            "show interfaces status err-disabled": self.outputs["err_disabled"],
            "show vlan brief": self.outputs["vlans"],
            "show spanning-tree summary": self.outputs["stp"],
            "show cdp neighbors": self.outputs["cdp"],
            "show logging": "SECRET_LEAK_123 " + self.outputs["logging"],
        }
        conn = _FakeNetmikoConn(cmd_map)
        outcome = self.profile.collect_audit(
            connection=conn,
            category="GENERAL",
            is_privileged=False,
            context={"port_device": "/dev/ttyACM0", "baudrate": 9600},
            secrets_to_scrub=("SECRET_LEAK_123",),
        )

        # Debe haber desactivado paginación y omitido show logging (requires_privileged=True)
        self.assertIn("terminal length 0", conn.sent_commands)
        self.assertNotIn("show logging", conn.sent_commands)
        self.assertEqual(outcome.overall_status, "CRIT")  # Gi1/0/3 está en err-disabled
        self.assertIn("show logging", outcome.switch_audit["skipped_privileged"])
        self.assertGreaterEqual(len(outcome.detail_pages), 2)


if __name__ == "__main__":
    unittest.main()

