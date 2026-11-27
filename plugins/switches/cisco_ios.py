"""
REI - Perfil de Diagnóstico Cisco IOS / IOS-XE (plugins/switches/cisco_ios.py)

Implementación completa de solo lectura para switches Cisco Catalyst (validada
especialmente para Catalyst 2960-S PoE 10G, IOS 12.2 / 15.x e IOS-XE):
- Identidad (modelo, serial, IOS, uptime, motivo último reinicio, stack)
- Salud física (ventiladores, temperatura, PSU)
- CPU y memoria
- PoE (presupuesto, consumo, puertos alimentados y fallas PoE)
- Interfaces (activos, err-disabled, uplinks 10G SFP+, half-duplex, IP gestión)
- Errores L1/L2 (CRC/FCS/Align/Runts/Giants)
- VLANs, Spanning Tree, vecinos CDP/LLDP y eventos críticos de syslog
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


# Comandos de solo lectura para la categoría PROBLEMAS GENERALES (GENERAL)
_CISCO_GENERAL_COMMANDS: Tuple[SwitchCommand, ...] = (
    SwitchCommand("version", "show version", "Leyendo version IOS...", timeout_seconds=20.0),
    SwitchCommand("env", "show env all", "Revisando sensores...", timeout_seconds=15.0),
    SwitchCommand("cpu", "show processes cpu | include CPU", "Consultando CPU...", timeout_seconds=15.0),
    SwitchCommand("memory", "show processes memory | include Processor Pool", "Consultando RAM...", timeout_seconds=15.0),
    SwitchCommand("poe", "show power inline", "Revisando estado PoE...", timeout_seconds=25.0),
    SwitchCommand("interfaces_status", "show interfaces status", "Leyendo interfaces...", timeout_seconds=25.0),
    SwitchCommand("ip_int_brief", "show ip interface brief", "Revisando IP gestion...", timeout_seconds=20.0),
    SwitchCommand("interface_errors", "show interfaces counters errors", "Analizando errores CRC...", timeout_seconds=25.0),
    SwitchCommand("err_disabled", "show interfaces status err-disabled", "Buscando err-disabled...", timeout_seconds=15.0),
    SwitchCommand("vlans", "show vlan brief", "Leyendo VLANs...", timeout_seconds=20.0),
    SwitchCommand("stp", "show spanning-tree summary", "Revisando Spanning-Tree...", timeout_seconds=15.0),
    SwitchCommand("cdp", "show cdp neighbors", "Buscando vecinos CDP...", timeout_seconds=15.0),
    SwitchCommand("logging", "show logging", "Revisando eventos log...", requires_privileged=True, timeout_seconds=25.0),
)

_CRITICAL_LOG_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"%PM-4-ERR_DISABLE|%ERRDISABLE", re.IGNORECASE),
    re.compile(r"%ILPOWER-[0-4]-", re.IGNORECASE),
    re.compile(r"%PLATFORM_ENV-[0-4]-|%ENV_MON-[0-4]-", re.IGNORECASE),
    re.compile(r"%SPANTREE-[0-4]-", re.IGNORECASE),
    re.compile(r"%SYS-[0-3]-", re.IGNORECASE),
    re.compile(r"%LINK-3-UPDOWN", re.IGNORECASE),
    re.compile(r"DUPLEX_MISMATCH", re.IGNORECASE),
    re.compile(r"%PORT_SECURITY-2-", re.IGNORECASE),
)


def _is_ios_cli_error(output: str) -> bool:
    """Detecta si el switch rechazó el comando por sintaxis o privilegios."""
    low = (output or "").lower()
    return (
        "% invalid input detected" in low
        or "% incomplete command" in low
        or "% ambiguous command" in low
        or "command authorization failed" in low
        or low.startswith("[error ejecutando")
    )


def parse_show_version(text: str) -> Dict[str, Any]:
    """Parsea la salida de 'show version' en Cisco IOS / IOS-XE."""
    out = text or ""
    hostname = regex_first_group(r"^([A-Za-z0-9._\-]+)\s+uptime\s+is\b", out, re.MULTILINE)
    uptime = regex_first_group(r"uptime\s+is\s+(.+)", out, re.IGNORECASE)
    ios_version = regex_first_group(r"Version\s+([0-9A-Za-z.()\-]+)", out, re.IGNORECASE)
    reload_reason = regex_first_group(r"System\s+returned\s+to\s+ROM\s+by\s+(.+)", out, re.IGNORECASE)
    if not reload_reason:
        reload_reason = regex_first_group(r"Last\s+reload\s+reason\s*:\s*(.+)", out, re.IGNORECASE)

    model = regex_first_group(r"Model\s+number\s*:\s*([A-Za-z0-9\-_/]+)", out, re.IGNORECASE)
    if not model:
        model = regex_first_group(r"cisco\s+(WS-C[A-Za-z0-9\-]+|C\d{4}[A-Za-z0-9\-]*)", out, re.IGNORECASE)

    serial_number = regex_first_group(r"System\s+serial\s+number\s*:\s*([A-Za-z0-9]+)", out, re.IGNORECASE)
    if not serial_number:
        serial_number = regex_first_group(r"Processor\s+board\s+ID\s+([A-Za-z0-9]+)", out, re.IGNORECASE)

    # Detectar miembros de stack en tabla inferior de show version
    stack_members = re.findall(r"^\*?\s*\d+\s+\d+\s+(WS-C[A-Za-z0-9\-]+|C\d{4}[A-Za-z0-9\-]*)", out, re.MULTILINE)

    return {
        "hostname": hostname,
        "model": model or "Cisco Catalyst",
        "serial_number": serial_number,
        "ios_version": ios_version,
        "uptime": uptime,
        "reload_reason": reload_reason,
        "stack_count": len(stack_members) if stack_members else 1,
    }


def parse_show_env(text: str) -> Dict[str, Any]:
    """Parsea la salida de 'show env all' (ventiladores, temperatura, PSU)."""
    out = text or ""
    if not out or _is_ios_cli_error(out):
        return {
            "fan_status": "N/D",
            "temperature_status": "N/D",
            "temperature_c": None,
            "psu_status": "N/D",
            "issues": [],
        }

    fan_status = regex_first_group(r"FAN(?:\s+\d+)?\s+is\s+([A-Za-z0-9 \-]+)", out, re.IGNORECASE) or "OK"
    temp_status = regex_first_group(r"TEMPERATURE\s+is\s+([A-Za-z0-9 \-]+)", out, re.IGNORECASE) or "OK"
    temp_val_str = regex_first_group(r"Temperature\s+Value\s*:\s*(\d+)\s*Degree\s*Celsius", out, re.IGNORECASE)
    temp_c: Optional[int] = int(temp_val_str) if temp_val_str.isdigit() else None

    psu_matches = re.findall(r"POWER\s+is\s+([A-Za-z0-9 \-]+)", out, re.IGNORECASE)
    if psu_matches:
        psu_status = psu_matches[0].strip()
    else:
        # Formato tabla de Catalyst 2960-S: "1  Built-in  Good"
        table_psu = re.search(
            r"^\s*\d+\s+Built-in\s+.*?\b(Good|OK|Faulty|Fail(?:ed)?|Bad|Not\s+Present)\b",
            out,
            re.IGNORECASE | re.MULTILINE,
        )
        psu_status = table_psu.group(1).strip() if table_psu else "OK"

    issues: List[str] = []
    for label, val in (("Ventilador", fan_status), ("Temperatura", temp_status), ("Fuente PSU", psu_status)):
        up = val.upper()
        if any(bad in up for bad in ("FAULT", "FAIL", "RED", "CRIT", "BAD")):
            issues.append(f"{label}: {val}")
        elif "YELLOW" in up or "WARN" in up:
            issues.append(f"{label}: {val}")

    if temp_c is not None and temp_c >= 65:
        issues.append(f"Temperatura elevada ({temp_c} C)")

    return {
        "fan_status": fan_status.strip(),
        "temperature_status": temp_status.strip(),
        "temperature_c": temp_c,
        "psu_status": psu_status.strip(),
        "issues": issues,
    }


def parse_cpu(text: str) -> Dict[str, Any]:
    """Parsea 'show processes cpu | include CPU'."""
    out = text or ""
    match = re.search(
        r"five\s+seconds:\s*(\d+)%(?:/(\d+)%)?;\s*one\s+minute:\s*(\d+)%;\s*five\s+minutes:\s*(\d+)%",
        out,
        re.IGNORECASE,
    )
    if not match:
        return {"cpu_5s": None, "cpu_1m": None, "cpu_5m": None}
    return {
        "cpu_5s": int(match.group(1)),
        "cpu_1m": int(match.group(3)),
        "cpu_5m": int(match.group(4)),
    }


def parse_memory(text: str) -> Dict[str, Any]:
    """Parsea 'show processes memory summary' o 'show processes memory'."""
    out = text or ""
    match = re.search(
        r"Processor\s+Pool\s+Total:\s*(\d+)\s+Used:\s*(\d+)\s+Free:\s*(\d+)",
        out,
        re.IGNORECASE,
    )
    if not match:
        return {"total_bytes": None, "used_bytes": None, "free_bytes": None, "usage_pct": None}
    total = int(match.group(1))
    used = int(match.group(2))
    free = int(match.group(3))
    pct = round((used / float(total)) * 100.0, 1) if total > 0 else 0.0
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "usage_pct": pct,
    }


def parse_power_inline(text: str) -> Dict[str, Any]:
    """
    Parsea 'show power inline' (crucial en Cisco Catalyst 2960-S PoE).
    Extrae presupuesto total, usado, disponible, puertos alimentados y fallas PoE.
    """
    out = text or ""
    if not out or _is_ios_cli_error(out):
        return {
            "supported": False,
            "budget_watts": None,
            "used_watts": None,
            "remaining_watts": None,
            "usage_pct": None,
            "active_ports": 0,
            "faulty_ports": [],
            "powered_ports": [],
        }

    # Tabla resumen: Module Available Used Remaining (Watts)
    # 1 370.0 42.5 327.5
    budget: Optional[float] = None
    used: Optional[float] = None
    remaining: Optional[float] = None
    summary_match = re.search(
        r"^\s*\d+\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*$",
        out,
        re.MULTILINE,
    )
    if summary_match:
        budget = float(summary_match.group(1))
        used = float(summary_match.group(2))
        remaining = float(summary_match.group(3))

    powered_ports: List[Dict[str, Any]] = []
    faulty_ports: List[Dict[str, Any]] = []

    port_re = re.compile(
        r"^([A-Za-z]{2}\d+(?:/\d+){1,2})\s+(auto|static|off)\s+(on|off|faulty|power-deny|err-disable)\s+(\d+(?:\.\d+)?)\s+(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in port_re.finditer(out):
        port_name = m.group(1)
        admin = m.group(2).lower()
        oper = m.group(3).lower()
        watts = float(m.group(4))
        tail = m.group(5).strip()
        entry = {
            "port": port_name,
            "admin": admin,
            "oper": oper,
            "watts": watts,
            "device": tail[:36],
        }
        if oper == "on":
            powered_ports.append(entry)
        elif oper in ("faulty", "power-deny", "err-disable"):
            faulty_ports.append(entry)

    usage_pct = round((used / budget) * 100.0, 1) if (budget and budget > 0 and used is not None) else None

    return {
        "supported": True,
        "budget_watts": budget,
        "used_watts": used,
        "remaining_watts": remaining,
        "usage_pct": usage_pct,
        "active_ports": len(powered_ports),
        "faulty_ports": faulty_ports,
        "powered_ports": powered_ports,
    }


def parse_interfaces_status(text: str) -> Dict[str, Any]:
    """Parsea 'show interfaces status' para contar estados, uplinks 10G y half-duplex."""
    out = text or ""
    counts = {
        "total": 0,
        "connected": 0,
        "notconnect": 0,
        "disabled": 0,
        "err_disabled": 0,
        "other": 0,
    }
    uplinks_10g: List[Dict[str, str]] = []
    half_duplex_ports: List[Dict[str, str]] = []
    err_disabled_ports: List[Dict[str, str]] = []

    status_re = re.compile(
        r"^((?:Gi|Fa|Te|Tw|Fo|Eth|Po)\d+(?:/\d+)*)\s+.*?\b(connected|notconnect|disabled|err-disabled|sfpAbsent|inactive)\b\s+(\S+)\s+(\S+)\s+(\S+)\s*(.*)$",
        re.IGNORECASE,
    )

    for raw_line in out.splitlines():
        line = raw_line.strip()
        m = status_re.match(line)
        if not m:
            continue
        port = m.group(1)
        st = m.group(2).lower()
        vlan = m.group(3)
        duplex = m.group(4)
        speed = m.group(5)
        media = m.group(6).strip()

        counts["total"] += 1
        if st == "connected":
            counts["connected"] += 1
        elif st == "notconnect":
            counts["notconnect"] += 1
        elif st == "disabled":
            counts["disabled"] += 1
        elif st == "err-disabled":
            counts["err_disabled"] += 1
            err_disabled_ports.append({"port": port, "vlan": vlan, "status": st})
        else:
            counts["other"] += 1

        if port.lower().startswith("te") or "10g" in media.lower() or "10000" in speed:
            uplinks_10g.append(
                {
                    "port": port,
                    "status": st,
                    "vlan": vlan,
                    "speed": speed,
                    "media": media or "SFP+",
                }
            )

        if st == "connected" and "half" in duplex.lower():
            half_duplex_ports.append(
                {
                    "port": port,
                    "duplex": duplex,
                    "speed": speed,
                    "vlan": vlan,
                }
            )

    return {
        "counts": counts,
        "uplinks_10g": uplinks_10g,
        "half_duplex_ports": half_duplex_ports,
        "err_disabled_from_status": err_disabled_ports,
    }


def parse_ip_int_brief(text: str) -> List[Dict[str, str]]:
    """Extrae interfaces L3/SVI con dirección IPv4 asignada desde 'show ip interface brief'."""
    ips: List[Dict[str, str]] = []
    row_re = re.compile(
        r"^(\S+)\s+(\d{1,3}(?:\.\d{1,3}){3})\s+\S+\s+\S+\s+(up|down|administratively\s+down)\s+(up|down)",
        re.IGNORECASE,
    )
    for raw_line in (text or "").splitlines():
        m = row_re.match(raw_line.strip())
        if m:
            ips.append(
                {
                    "interface": m.group(1),
                    "ip": m.group(2),
                    "status": m.group(3).lower(),
                    "protocol": m.group(4).lower(),
                }
            )
    return ips


def parse_interface_errors(text: str) -> List[Dict[str, Any]]:
    """
    Parsea 'show interfaces counters errors' (dos bloques en Catalyst IOS) y devuelve
    los puertos que acumulan errores > 0 ordenados de mayor a menor.
    """
    port_totals: Dict[str, Dict[str, int]] = {}
    row_re = re.compile(r"^((?:Gi|Fa|Te|Tw|Fo|Eth|Po)\d+(?:/\d+)*)\s+([\d\s]+)$")

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        m = row_re.match(line)
        if not m:
            continue
        port = m.group(1)
        nums = [int(tok) for tok in m.group(2).split() if tok.isdigit()]
        if not nums:
            continue
        entry = port_totals.setdefault(
            port,
            {"align": 0, "fcs": 0, "xmit": 0, "rcv": 0, "other": 0, "total": 0},
        )
        # Primer bloque típico: Align-Err FCS-Err Xmit-Err Rcv-Err UnderSize OutDiscards
        if entry["total"] == 0 and len(nums) >= 4:
            entry["align"] += nums[0]
            entry["fcs"] += nums[1]
            entry["xmit"] += nums[2]
            entry["rcv"] += nums[3]
            entry["other"] += sum(nums[4:])
        else:
            entry["other"] += sum(nums)
        entry["total"] = entry["align"] + entry["fcs"] + entry["xmit"] + entry["rcv"] + entry["other"]

    err_ports = [
        {"port": port, **vals}
        for port, vals in port_totals.items()
        if vals["total"] > 0
    ]
    err_ports.sort(key=lambda item: item["total"], reverse=True)
    return err_ports


def parse_err_disabled(text: str) -> List[Dict[str, str]]:
    """Parsea 'show interfaces status err-disabled'."""
    results: List[Dict[str, str]] = []
    row_re = re.compile(
        r"^((?:Gi|Fa|Te|Tw|Fo|Eth|Po)\d+(?:/\d+)*)\s+.*?\berr-disabled\b\s+(\S+)",
        re.IGNORECASE,
    )
    for raw_line in (text or "").splitlines():
        m = row_re.match(raw_line.strip())
        if m:
            results.append(
                {
                    "port": m.group(1),
                    "reason": m.group(2),
                }
            )
    return results


def parse_vlan_brief(text: str) -> Dict[str, Any]:
    """Parsea 'show vlan brief'."""
    vlans: List[Dict[str, str]] = []
    row_re = re.compile(r"^(\d{1,4})\s+(\S+)\s+(active|act/unsup|suspend)\b", re.IGNORECASE)
    for raw_line in (text or "").splitlines():
        m = row_re.match(raw_line.strip())
        if m:
            vid = int(m.group(1))
            if vid in (1002, 1003, 1004, 1005):
                continue  # VLANs legacy FDDI/TokenRing por defecto en IOS
            vlans.append({"id": str(vid), "name": m.group(2), "status": m.group(3).lower()})
    return {
        "count": len(vlans),
        "vlans": vlans[:24],
    }


def parse_stp_summary(text: str) -> Dict[str, Any]:
    """Parsea 'show spanning-tree summary'."""
    out = text or ""
    mode = regex_first_group(r"Switch\s+is\s+in\s+(.+?)\s+mode", out, re.IGNORECASE) or "Desconocido"
    root_vlans = regex_first_group(r"Root\s+bridge\s+for:\s*(.+)", out, re.IGNORECASE)
    total_match = re.search(
        r"^\s*Total\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
        out,
        re.MULTILINE | re.IGNORECASE,
    )
    return {
        "mode": mode.strip(),
        "root_for": root_vlans.strip() if root_vlans else "Ninguna",
        "blocking": int(total_match.group(1)) if total_match else 0,
        "listening": int(total_match.group(2)) if total_match else 0,
        "learning": int(total_match.group(3)) if total_match else 0,
        "forwarding": int(total_match.group(4)) if total_match else 0,
        "active": int(total_match.group(5)) if total_match else 0,
    }


def parse_cdp_neighbors(text: str) -> List[Dict[str, str]]:
    """Parsea 'show cdp neighbors' (incluyendo líneas partidas cuando Device ID es largo)."""
    neighbors: List[Dict[str, str]] = []
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    row_re = re.compile(
        r"^(\S+)?\s*((?:Gig|Fas|Ten|Twe|Eth)\s*\d+(?:/\d+)*)\s+\d+\s+[A-Z ]{1,12}\s+(\S+)\s+((?:Gig|Fas|Ten|Twe|Eth)\s*\d+(?:/\d+)*)",
        re.IGNORECASE,
    )
    pending_device = ""
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("Capability", "Device ID", "Total cdp", "-")):
            continue
        m = row_re.match(stripped)
        if m:
            dev_id = (m.group(1) or pending_device or "Vecino").strip()
            neighbors.append(
                {
                    "device_id": dev_id,
                    "local_intf": m.group(2).replace(" ", ""),
                    "platform": m.group(3),
                    "port_id": m.group(4).replace(" ", ""),
                }
            )
            pending_device = ""
        elif len(stripped.split()) == 1 and not stripped.startswith("%"):
            pending_device = stripped
    return neighbors


def parse_logging(text: str, max_lines: int = 30) -> Dict[str, Any]:
    """Extrae las últimas líneas del buffer de logging y clasifica eventos críticos."""
    raw_lines = [
        ln.strip()
        for ln in (text or "").splitlines()
        if ln.strip() and "%" in ln
    ]
    recent = raw_lines[-max_lines:]
    critical = [
        ln for ln in recent
        if any(pat.search(ln) for pat in _CRITICAL_LOG_PATTERNS)
    ]
    return {
        "recent_lines": recent,
        "critical_events": critical[-15:],
        "total_captured": len(recent),
    }


def get_mock_cisco_2960s_outputs() -> Dict[str, str]:
    """
    Genera salidas realistas de un Cisco Catalyst 2960-S PoE 10G (WS-C2960S-48FPD-L)
    para pruebas offline y modo REI_DRY_RUN=1.
    """
    return {
        "version": (
            "Cisco IOS Software, C2960S Software (C2960S-UNIVERSALK9-M), Version 15.2(2)E8, RELEASE SOFTWARE (fc1)\n"
            "SW-2960S-POE uptime is 4 weeks, 3 days, 12 hours, 18 minutes\n"
            "System returned to ROM by power-on\n"
            "cisco WS-C2960S-48FPD-L (PowerPC) processor (revision J0) with 131072K bytes of memory.\n"
            "Processor board ID FOC1724Y1AB\n"
            "Model number                    : WS-C2960S-48FPD-L\n"
            "System serial number            : FOC1724Y1AB\n"
            "Switch Ports Model              SW Version            SW Image\n"
            "*    1 50    WS-C2960S-48FPD-L  15.2(2)E8             C2960S-UNIVERSALK9-M\n"
        ),
        "env": (
            "FAN is OK\n"
            "TEMPERATURE is OK\n"
            "Temperature Value: 41 Degree Celsius\n"
            "Temperature State: GREEN\n"
            "POWER is OK\n"
            "RPS is NOT PRESENT\n"
        ),
        "cpu": "CPU utilization for five seconds: 9%/1%; one minute: 11%; five minutes: 10%\n",
        "memory": "Processor Pool Total:  98451200 Used:  47256576 Free:  51194624\n",
        "poe": (
            "Module   Available     Used     Remaining\n"
            "          (Watts)     (Watts)    (Watts)\n"
            "------   ---------   --------   ---------\n"
            "1           740.0      118.4       621.6\n"
            "Interface Admin  Oper       Power   Device              Class Max\n"
            "                            (Watts)\n"
            "--------- ------ ---------- ------- ------------------- ----- ----\n"
            "Gi1/0/1   auto   on         15.4    AIR-CAP2702I        3     30.0\n"
            "Gi1/0/2   auto   on         12.8    IP Phone 7945       3     30.0\n"
            "Gi1/0/3   auto   off        0.0     n/a                 n/a   30.0\n"
        ),
        "interfaces_status": (
            "Port      Name               Status       Vlan       Duplex  Speed Type\n"
            "Gi1/0/1   AP-PISO1           connected    10         a-full  a-1000 10/100/1000BaseTX\n"
            "Gi1/0/2   TEL-RECEP          connected    20         a-full  a-100  10/100/1000BaseTX\n"
            "Gi1/0/3   USR-LAB03          err-disabled 10         auto    auto   10/100/1000BaseTX\n"
            "Gi1/0/4   LIBRE              notconnect   10         auto    auto   10/100/1000BaseTX\n"
            "Te1/0/1   UPLINK-CORE        connected    trunk      a-full  a-10G  SFP-10GBase-SR\n"
            "Te1/0/2   UPLINK-BKP         notconnect   trunk      auto    auto   Not Present\n"
        ),
        "ip_int_brief": (
            "Interface              IP-Address      OK? Method Status                Protocol\n"
            "Vlan1                  unassigned      YES NVRAM  administratively down down\n"
            "Vlan10                 192.168.10.2    YES NVRAM  up                    up\n"
            "GigabitEthernet1/0/1   unassigned      YES unset  up                    up\n"
        ),
        "interface_errors": (
            "Port        Align-Err    FCS-Err   Xmit-Err    Rcv-Err  UnderSize OutDiscards\n"
            "Gi1/0/1             0          0          0          0          0           0\n"
            "Gi1/0/2             0         18          0         18          0           0\n"
            "Te1/0/1             0          0          0          0          0           0\n"
        ),
        "err_disabled": (
            "Port      Name               Status       Reason               Err-disabled Vl\n"
            "Gi1/0/3   USR-LAB03          err-disabled bpduguard\n"
        ),
        "vlans": (
            "VLAN Name                             Status    Ports\n"
            "1    default                          active    Gi1/0/4\n"
            "10   DATOS                            active    Gi1/0/1, Gi1/0/3\n"
            "20   VOZ                              active    Gi1/0/2\n"
            "99   GESTION                          active\n"
        ),
        "stp": (
            "Switch is in rapid-pvst mode\n"
            "Root bridge for: none\n"
            "Name                   Blocking Listening Learning Forwarding STP Active\n"
            "Total                         0         0        0          3          3\n"
        ),
        "cdp": (
            "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\n"
            "CORE-3850        Ten 1/0/1         148             R S I  WS-C3850  Ten 1/1/1\n"
            "AP-PISO1         Gig 1/0/1         132             T B I  AIR-CAP27 Gig 0\n"
        ),
        "logging": (
            "Syslog logging: enabled\n"
            "*Mar  1 08:12:44.210: %SPANTREE-2-BLOCK_BPDUGUARD: Received BPDU on port Gi1/0/3 with BPDU Guard enabled. Disabling port.\n"
            "*Mar  1 08:12:44.215: %PM-4-ERR_DISABLE: bpduguard error detected on Gi1/0/3, putting Gi1/0/3 in err-disable state\n"
            "*Mar  1 08:12:45.220: %LINK-3-UPDOWN: Interface GigabitEthernet1/0/3, changed state to down\n"
        ),
    }


class CiscoIOSProfile(ISwitchProfile):
    """Perfil completo de solo lectura para switches Cisco IOS / IOS-XE."""

    @property
    def vendor_id(self) -> str:
        return "cisco_ios"

    @property
    def display_name(self) -> str:
        return "CISCO IOS / XE"

    @property
    def netmiko_device_type(self) -> str:
        return "cisco_ios_serial"

    @property
    def preferred_baudrates(self) -> Tuple[int, ...]:
        return (9600, 115200, 38400)

    def match_passive(self, banner: str, prompt: str = "") -> float:
        text = f"{banner or ''}\n{prompt or ''}"
        score = 0.0
        if re.search(r"\bcisco\b|\bcatalyst\b|\bws-c\d{4}", text, re.IGNORECASE):
            score += 0.75
        if re.search(r"\buser\s+access\s+verification\b|\bcon0\s+is\s+now\s+available\b", text, re.IGNORECASE):
            score += 0.35
        if re.search(r"\bpress\s+return\s+to\s+get\s+started\b", text, re.IGNORECASE):
            score += 0.25
        return min(1.0, score)

    def match_probe_output(self, output: str) -> float:
        text = output or ""
        if re.search(r"Cisco\s+IOS(?:-XE)?\s+Software|WS-C\d{4}|Catalyst", text, re.IGNORECASE):
            return 0.98
        if re.search(r"\bcisco\b", text, re.IGNORECASE):
            return 0.70
        return 0.0

    def get_commands(self, category: str = "GENERAL", is_privileged: bool = True) -> List[SwitchCommand]:
        norm = normalize_switch_category(category)
        # Por ahora todas las categorías futuras sin batería específica caen a GENERAL
        if norm == "GENERAL":
            return list(_CISCO_GENERAL_COMMANDS)
        return list(_CISCO_GENERAL_COMMANDS)

    def prepare_session(self, connection: Any) -> None:
        if hasattr(connection, "send_command"):
            for cmd in ("terminal length 0", "terminal width 0"):
                try:
                    connection.send_command(cmd, read_timeout=4)
                except Exception:
                    pass

    def parse_outputs(
        self,
        command_outputs: Dict[str, str],
        context: Optional[Dict[str, Any]] = None,
    ) -> SwitchAuditOutcome:
        ctx = context or {}
        norm_cat = normalize_switch_category(ctx.get("category"))
        cat_label = get_switch_category_label(norm_cat)
        is_priv = bool(ctx.get("is_privileged", True))

        ver = parse_show_version(command_outputs.get("version", ""))
        env = parse_show_env(command_outputs.get("env", ""))
        cpu = parse_cpu(command_outputs.get("cpu", ""))
        mem = parse_memory(command_outputs.get("memory", ""))
        poe = parse_power_inline(command_outputs.get("poe", ""))
        if_st = parse_interfaces_status(command_outputs.get("interfaces_status", ""))
        mgmt_ips = parse_ip_int_brief(command_outputs.get("ip_int_brief", ""))
        if_errs = parse_interface_errors(command_outputs.get("interface_errors", ""))
        err_dis = parse_err_disabled(command_outputs.get("err_disabled", ""))
        vlans = parse_vlan_brief(command_outputs.get("vlans", ""))
        stp = parse_stp_summary(command_outputs.get("stp", ""))
        cdp = parse_cdp_neighbors(command_outputs.get("cdp", ""))
        logs = parse_logging(command_outputs.get("logging", ""))

        # Combinar err-disabled si el comando dedicado no devolvió filas pero status sí
        if not err_dis and if_st["err_disabled_from_status"]:
            err_dis = [
                {"port": item["port"], "reason": "err-disabled"}
                for item in if_st["err_disabled_from_status"]
            ]

        hostname = ver.get("hostname") or ctx.get("hostname_hint") or "Switch-Cisco"

        # Evaluación de severidad y hallazgos
        findings: List[Dict[str, str]] = []
        status = "OK"

        def add_finding(sev: str, area: str, msg: str) -> None:
            nonlocal status
            findings.append({"severity": sev, "area": area, "message": msg})
            if sev == "CRIT":
                status = "CRIT"
            elif sev == "WARN" and status != "CRIT":
                status = "WARN"

        for env_issue in env.get("issues", []):
            sev = "CRIT" if any(k in env_issue.upper() for k in ("FAIL", "FAULT", "CRIT", "RED")) else "WARN"
            add_finding(sev, "Hardware", env_issue)

        if err_dis:
            ports_txt = ", ".join(f"{e['port']} ({e['reason']})" for e in err_dis[:4])
            add_finding("CRIT", "Interfaces", f"{len(err_dis)} puerto(s) en err-disabled: {ports_txt}")

        if poe.get("faulty_ports"):
            p_txt = ", ".join(p["port"] for p in poe["faulty_ports"][:4])
            add_finding("CRIT", "PoE", f"Falla PoE en puerto(s): {p_txt}")
        elif poe.get("usage_pct") is not None and poe["usage_pct"] >= 85.0:
            add_finding("WARN", "PoE", f"Presupuesto PoE elevado ({poe['usage_pct']}%)")

        if cpu.get("cpu_5m") is not None and cpu["cpu_5m"] >= 85:
            add_finding("CRIT", "CPU", f"Carga crítica de CPU (5 min: {cpu['cpu_5m']}%)")
        elif cpu.get("cpu_5m") is not None and cpu["cpu_5m"] >= 70:
            add_finding("WARN", "CPU", f"Carga elevada de CPU (5 min: {cpu['cpu_5m']}%)")

        if mem.get("usage_pct") is not None and mem["usage_pct"] >= 90.0:
            add_finding("WARN", "Memoria", f"Uso elevado de memoria ({mem['usage_pct']}%)")

        if if_errs:
            top_e = if_errs[0]
            sev = "WARN" if top_e["total"] >= 10 else "INFO"
            add_finding(
                sev,
                "Capa 1/2",
                f"{len(if_errs)} puerto(s) con errores L1/L2 (mayor: {top_e['port']} con {top_e['total']} errores)",
            )

        if if_st.get("half_duplex_ports"):
            hd = ", ".join(p["port"] for p in if_st["half_duplex_ports"][:4])
            add_finding("WARN", "Duplex", f"Puertos activos en half-duplex: {hd}")

        counts = if_st["counts"]
        primary_mgmt_ip = mgmt_ips[0]["ip"] if mgmt_ips else "Sin IP SVI"
        port_dev = ctx.get("port_device", "/dev/ttyUSB0")
        baudrate = int(ctx.get("baudrate", 9600))

        switch_audit: Dict[str, Any] = {
            "vendor_id": self.vendor_id,
            "vendor_name": self.display_name,
            "category": norm_cat,
            "category_label": cat_label,
            "connection": {
                "port": port_dev,
                "port_kind": ctx.get("port_kind", "USB_RJ45"),
                "baudrate": baudrate,
                "privileged": is_priv,
                "signals": ctx.get("signals", {}),
            },
            "identity": {
                "hostname": hostname,
                "model": ver["model"],
                "serial_number": ver["serial_number"],
                "os_version": ver["ios_version"],
                "uptime": ver["uptime"],
                "reload_reason": ver["reload_reason"],
                "stack_count": ver["stack_count"],
                "management_ip": primary_mgmt_ip,
                "management_interfaces": mgmt_ips,
            },
            "health": {
                "cpu": cpu,
                "memory": mem,
                "environment": env,
            },
            "poe": poe,
            "interfaces": {
                "counts": counts,
                "uplinks_10g": if_st["uplinks_10g"],
                "half_duplex_ports": if_st["half_duplex_ports"],
                "err_disabled": err_dis,
                "error_counters": if_errs[:12],
            },
            "switching": {
                "vlans": vlans,
                "stp": stp,
                "neighbors": cdp,
            },
            "logging": logs,
            "findings": findings,
            "skipped_privileged": list(ctx.get("skipped_privileged", [])),
            "raw_commands": command_outputs,
        }

        # Resumen compacto para OLED
        poe_str = (
            f"{poe['used_watts']:.1f}/{poe['budget_watts']:.0f}W"
            if poe.get("supported") and poe.get("budget_watts")
            else "N/A"
        )
        details = [
            f"Host: {hostname[:15]}",
            f"Mod:  {ver['model'][:15]}",
            f"Puertos: {counts['connected']}/{counts['total']} UP",
            f"ErrDis: {len(err_dis)} | PoE: {poe_str}",
        ]
        if not is_priv:
            details.append("Modo lectura (>): sin enable")

        detail_pages: List[Dict[str, Any]] = [
            {
                "title": "CONSOLA SERIAL",
                "hero": {"tag": "SW", "value": hostname[:14]},
                "rows": [
                    {"label": "Modelo", "value": ver["model"][:14]},
                    {"label": "Puerto", "value": f"{port_dev.replace('/dev/', '')} {baudrate}"},
                ],
            },
            {
                "title": "SALUD SWITCH",
                "rows": [
                    {"label": "Up/Tot", "value": f"{counts['connected']}/{counts['total']}"},
                    {"label": "ErrDis", "value": str(len(err_dis))},
                    {"label": "PoE", "value": poe_str},
                ],
            },
        ]

        summary_map = {
            "OK": f"{hostname}: Switch nominal",
            "WARN": f"{hostname}: {len(findings)} alerta(s)",
            "CRIT": f"{hostname}: Fallas criticas",
        }

        return SwitchAuditOutcome(
            vendor_id=self.vendor_id,
            vendor_name=self.display_name,
            category=norm_cat,
            hostname=hostname,
            overall_status=status,
            summary=summary_map.get(status, f"{hostname}: Diagnostico listo"),
            details=details,
            detail_pages=detail_pages,
            switch_audit=switch_audit,
            raw_commands=command_outputs,
        )

