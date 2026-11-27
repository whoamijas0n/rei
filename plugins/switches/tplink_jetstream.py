"""
REI - Perfil Base para Switches TP-Link JetStream / Omada (plugins/switches/tplink_jetstream.py)

Esqueleto funcional de solo lectura listo para ampliarse cuando se cuente con
equipos físicos de prueba en laboratorio. Usa baudrate preferido 38400 8-N-1.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    ISwitchProfile,
    SwitchAuditOutcome,
    SwitchCommand,
    get_switch_category_label,
    normalize_switch_category,
    regex_first_group,
)


_TPLINK_GENERAL_COMMANDS: Tuple[SwitchCommand, ...] = (
    SwitchCommand("version", "show system-info", "Leyendo sistema TP-Link..."),
    SwitchCommand("interfaces_status", "show interface status", "Leyendo interfaces..."),
    SwitchCommand("vlans", "show vlan brief", "Leyendo VLANs..."),
    SwitchCommand("stp", "show spanning-tree summary", "Revisando STP..."),
    SwitchCommand("logging", "show logging buffer", "Revisando logs...", requires_privileged=True),
)


class TPLinkJetStreamProfile(ISwitchProfile):
    """Perfil de solo lectura para switches TP-Link JetStream / Omada."""

    @property
    def vendor_id(self) -> str:
        return "tplink_jetstream"

    @property
    def display_name(self) -> str:
        return "TP-LINK"

    @property
    def netmiko_device_type(self) -> str:
        return "tplink_jetstream_serial"

    @property
    def preferred_baudrates(self) -> Tuple[int, ...]:
        return (38400, 115200, 9600)

    @property
    def probe_commands(self) -> Tuple[str, ...]:
        return ("show system-info", "show version")

    def match_passive(self, banner: str, prompt: str = "") -> float:
        text = f"{banner or ''}\n{prompt or ''}"
        if re.search(r"\btp-?link\b|\bjetstream\b|\btl-s[gx]\d+|\bomada\b", text, re.IGNORECASE):
            return 0.90
        return 0.0

    def match_probe_output(self, output: str) -> float:
        text = output or ""
        if re.search(r"TP-?LINK|JetStream|TL-S[GX]\d+", text, re.IGNORECASE):
            return 0.95
        return 0.0

    def get_commands(self, category: str = "GENERAL", is_privileged: bool = True) -> List[SwitchCommand]:
        return list(_TPLINK_GENERAL_COMMANDS)

    def parse_outputs(
        self,
        command_outputs: Dict[str, str],
        context: Optional[Dict[str, Any]] = None,
    ) -> SwitchAuditOutcome:
        ctx = context or {}
        norm_cat = normalize_switch_category(ctx.get("category"))
        sys_out = command_outputs.get("version", "")

        hostname = (
            regex_first_group(r"System\s+Name\s*:\s*(.+)", sys_out, re.IGNORECASE)
            or ctx.get("hostname_hint")
            or "Switch-TPLink"
        )
        model = (
            regex_first_group(r"Hardware\s+Version\s*:\s*(.+)", sys_out, re.IGNORECASE)
            or regex_first_group(r"(TL-S[GX][A-Za-z0-9\-]+)", sys_out, re.IGNORECASE)
            or "TP-Link JetStream"
        )
        fw = regex_first_group(r"Firmware\s+Version\s*:\s*(.+)", sys_out, re.IGNORECASE)
        uptime = regex_first_group(r"Running\s+Time\s*:\s*(.+)", sys_out, re.IGNORECASE)

        port_dev = ctx.get("port_device", "/dev/ttyUSB0")
        baudrate = int(ctx.get("baudrate", 38400))

        switch_audit: Dict[str, Any] = {
            "vendor_id": self.vendor_id,
            "vendor_name": self.display_name,
            "category": norm_cat,
            "category_label": get_switch_category_label(norm_cat),
            "connection": {
                "port": port_dev,
                "port_kind": ctx.get("port_kind", "USB_RJ45"),
                "baudrate": baudrate,
                "privileged": bool(ctx.get("is_privileged", True)),
                "signals": ctx.get("signals", {}),
            },
            "identity": {
                "hostname": hostname,
                "model": model,
                "serial_number": "",
                "os_version": fw,
                "uptime": uptime,
                "reload_reason": "",
                "stack_count": 1,
                "management_ip": "N/D",
                "management_interfaces": [],
            },
            "health": {"cpu": {}, "memory": {}, "environment": {}},
            "poe": {"supported": False},
            "interfaces": {"counts": {}, "uplinks_10g": [], "half_duplex_ports": [], "err_disabled": [], "error_counters": []},
            "switching": {"vlans": {"count": 0, "vlans": []}, "stp": {}, "neighbors": []},
            "logging": {"recent_lines": [], "critical_events": [], "total_captured": 0},
            "findings": [],
            "skipped_privileged": list(ctx.get("skipped_privileged", [])),
            "raw_commands": command_outputs,
        }

        details = [
            f"Host: {hostname[:15]}",
            f"Mod:  {model[:15]}",
            f"Puerto: {port_dev.replace('/dev/', '')} ({baudrate})",
            "Perfil base TP-Link",
        ]
        detail_pages = [
            {
                "title": "CONSOLA SERIAL",
                "hero": {"tag": "SW", "value": hostname[:14]},
                "rows": [
                    {"label": "Marca", "value": "TP-LINK"},
                    {"label": "Baud", "value": f"{baudrate} 8-N-1"},
                ],
            }
        ]

        return SwitchAuditOutcome(
            vendor_id=self.vendor_id,
            vendor_name=self.display_name,
            category=norm_cat,
            hostname=hostname,
            overall_status="OK",
            summary=f"{hostname}: Recoleccion TP-Link OK",
            details=details,
            detail_pages=detail_pages,
            switch_audit=switch_audit,
            raw_commands=command_outputs,
        )

