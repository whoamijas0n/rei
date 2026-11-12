"""
REI - Wi-Fi Manager and Scanner
Handles network interface discovery, wireless access point scanning,
saved connection profile management, and connection lifecycle using
nmcli, iwlist, and wpa_supplicant.
Provides stateful simulation support for safe development and testing.
"""

from dataclasses import dataclass
from enum import Enum
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from typing import Dict, List, Optional, Tuple, Any

from .parser import sanitize_oled_text, shorten_oled_label

logger = logging.getLogger("REI.Core.WiFi")


class WiFiErrorCode(str, Enum):
    """Structured Wi-Fi failure and state codes for deterministic UI handling."""
    NONE = "NONE"
    ALREADY_CONNECTED = "ALREADY_CONNECTED"
    AUTH_FAILED = "AUTH_FAILED"
    SECRETS_REQUIRED = "SECRETS_REQUIRED"
    NO_NETWORK_FOUND = "NO_NETWORK_FOUND"
    DHCP_FAILED = "DHCP_FAILED"
    TIMEOUT = "TIMEOUT"
    WIFI_DISABLED = "WIFI_DISABLED"
    INTERFACE_UNAVAILABLE = "INTERFACE_UNAVAILABLE"
    ENTERPRISE_UNSUPPORTED = "ENTERPRISE_UNSUPPORTED"
    PROFILE_CONFLICT = "PROFILE_CONFLICT"
    MANAGER_UNAVAILABLE = "MANAGER_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def validate_wifi_password(
    password: Optional[str],
    security: Optional[str] = None,
) -> Tuple[bool, str, WiFiErrorCode]:
    """
    Validates Wi-Fi passphrase rules prior to invoking NetworkManager/wpa_cli.
    Returns (is_valid, short_oled_error_19, error_code).
    Preserves internal/edge whitespace since spaces can be part of a real PSK.
    """
    if password is None or password == "":
        return False, "Ingrese una clave", WiFiErrorCode.AUTH_FAILED

    if any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
        return False, "Caracter no valido", WiFiErrorCode.AUTH_FAILED

    sec = (security or "").upper()
    is_hex = all(ch in _HEX_CHARS for ch in password)

    if "WEP" in sec:
        if len(password) in (5, 13) or (len(password) in (10, 26) and is_hex):
            return True, "", WiFiErrorCode.NONE
        return False, "WEP: 5/13 o hex", WiFiErrorCode.AUTH_FAILED

    if any(tok in sec for tok in ("WPA", "SAE", "PSK")):
        if len(password) < 8:
            return False, "Min. 8 caracteres", WiFiErrorCode.AUTH_FAILED
        if len(password) > 64 or (len(password) == 64 and not is_hex):
            return False, "Max. 63 caracteres", WiFiErrorCode.AUTH_FAILED
        return True, "", WiFiErrorCode.NONE

    if len(password) > 64 or (len(password) == 64 and not is_hex):
        return False, "Max. 63 caracteres", WiFiErrorCode.AUTH_FAILED

    return True, "", WiFiErrorCode.NONE


def get_interface_ip(interface: str = "wlan0") -> Optional[str]:
    """
    Reads active IPv4 address assigned to a specific network interface.
    1. Supports simulation mode (REI_DRY_RUN=1 or REI_MOCK_WIFI=1).
    2. Uses native POSIX fcntl.ioctl SIOCGIFADDR (efficient, zero-subprocess).
    3. Falls back to `ip -4 addr show <interface>` via subprocess.
    """
    if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_WIFI") == "1":
        if interface.startswith("wlan"):
            return "192.168.1.142"
        elif interface.startswith("eth"):
            return "192.168.1.150"
        elif interface.startswith("usb"):
            return "10.0.0.1"
        return "192.168.1.142"

    # 1. Direct Linux ioctl SIOCGIFADDR
    try:
        import fcntl
        import struct
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            ip_bytes = fcntl.ioctl(
                s.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack("256s", interface[:15].encode("utf-8")),
            )[20:24]
            ip = socket.inet_ntoa(ip_bytes)
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass

    # 2. Subprocess fallback: `ip -4 addr show <interface>`
    try:
        res = subprocess.run(
            ["ip", "-4", "addr", "show", interface],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    ip = line.split()[1].split("/")[0]
                    if ip and not ip.startswith("127."):
                        return ip
    except Exception:
        pass

    return None


@dataclass
class SavedWiFiProfile:
    """Represents a stored Wi-Fi connection profile."""
    ssid: str
    profile_id: str
    name: str
    security: str = ""
    autoconnect: bool = True
    in_use: bool = False
    in_range: bool = False
    signal_pct: int = 0


@dataclass
class WiFiNetwork:
    """Represents a discovered wireless access point."""
    ssid: str
    signal_pct: int
    security: str
    in_use: bool = False
    bssid: str = ""
    frequency: str = "2.4GHz"
    channel: int = 1
    is_saved: bool = False
    saved_profile_id: Optional[str] = None
    saved_profile_name: Optional[str] = None

    @property
    def is_secured(self) -> bool:
        """Returns True if network requires a password."""
        sec = self.security.upper()
        return not (sec in ("", "--", "OPEN", "NONE") or "OPEN" in sec)

    @property
    def is_enterprise(self) -> bool:
        """Returns True if network uses 802.1X / EAP authentication."""
        sec = self.security.upper()
        return any(token in sec for token in ("802.1X", "EAP", "ENTERPRISE"))

    @property
    def signal_dbm(self) -> int:
        """Approximate dBm from percentage."""
        return int((self.signal_pct / 2.0) - 100)


class WiFiManager:
    """
    Manages scanning, saved profiles, and connecting to Wi-Fi networks on Linux/Raspberry Pi.
    Gracefully handles:
    1. nmcli (NetworkManager, forced LC_ALL=C for deterministic output)
    2. iwlist / iw scan
    3. wpa_cli / wpa_supplicant
    4. Stateful simulated access points & profiles (Dry-Run / Mock).
    """

    def __init__(self, interface: str = "wlan0"):
        self.interface = interface
        self._last_scan_error: Optional[str] = None
        self._last_scan_error_code: WiFiErrorCode = WiFiErrorCode.NONE

        # Stateful mock storage for REI_DRY_RUN=1 / REI_MOCK_WIFI=1
        self._mock_active_ssid: Optional[str] = "REI_Lab_AP"
        self._mock_saved_profiles: Dict[str, Dict[str, Any]] = {
            "REI_Lab_AP": {
                "profile_id": "mock-uuid-1",
                "name": "REI_Lab_AP",
                "password": "rei-password",
                "server_password": "rei-password",
                "security": "WPA2-PSK",
                "autoconnect": True,
            },
            "IT_Dep_Secure": {
                "profile_id": "mock-uuid-2",
                "name": "IT_Dep_Secure",
                "password": "it-password",
                "server_password": "it-password",
                "security": "WPA2-PSK",
                "autoconnect": True,
            },
            "Oficina_Clave": {
                "profile_id": "mock-uuid-3",
                "name": "Oficina_Clave",
                "password": "old-password",
                "server_password": "new-password",
                "security": "WPA2-PSK",
                "autoconnect": True,
            },
        }
        self._mock_catalog: List[Dict[str, Any]] = [
            {"ssid": "REI_Lab_AP", "signal_pct": 88, "security": "WPA2-PSK", "frequency": "2.4GHz", "channel": 6},
            {"ssid": "Corp_Net_5G", "signal_pct": 92, "security": "WPA2-Enterprise", "frequency": "5GHz", "channel": 36},
            {"ssid": "Guest_WiFi", "signal_pct": 68, "security": "Open", "frequency": "2.4GHz", "channel": 1},
            {"ssid": "Switch_Mgmt", "signal_pct": 55, "security": "WPA3-SAE", "frequency": "5GHz", "channel": 44},
            {"ssid": "IT_Dep_Secure", "signal_pct": 48, "security": "WPA2-PSK", "frequency": "2.4GHz", "channel": 11},
            {"ssid": "Oficina_Clave", "signal_pct": 62, "security": "WPA2-PSK", "frequency": "2.4GHz", "channel": 3},
        ]

    @staticmethod
    def _is_dry_run() -> bool:
        return os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_WIFI") == "1"

    @staticmethod
    def _nmcli_env() -> Dict[str, str]:
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        return env

    def _run_nmcli(self, nmcli_bin: str, args: List[str], timeout: int = 10) -> subprocess.CompletedProcess:
        return subprocess.run(
            [nmcli_bin, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=self._nmcli_env(),
        )

    @staticmethod
    def _split_nmcli_terse_line(line: str) -> List[str]:
        """
        Splits an `nmcli -t` colon-delimited line while respecting backslash escapes
        (`\\:` inside SSIDs/BSSIDs and `\\\\` for literal backslashes).
        """
        fields: List[str] = []
        buf: List[str] = []
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            if ch == "\\" and i + 1 < n:
                nxt = line[i + 1]
                if nxt in (":", "\\"):
                    buf.append(nxt)
                    i += 2
                    continue
            if ch == ":":
                fields.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
            i += 1
        fields.append("".join(buf))
        return fields

    def scan_networks(self) -> List[WiFiNetwork]:
        """
        Scans for available wireless networks and returns a deduplicated list
        annotated with active and saved profile state.
        """
        networks, _, _ = self.scan_networks_detailed()
        return networks

    def scan_networks_detailed(self) -> Tuple[List[WiFiNetwork], Optional[str], WiFiErrorCode]:
        """
        Scans for available wireless networks and returns:
        (networks, error_message, error_code).
        Does not fall back to simulated APs when real wireless tools report an error.
        """
        self._last_scan_error = None
        self._last_scan_error_code = WiFiErrorCode.NONE

        if self._is_dry_run():
            return self._get_simulated_networks(), None, WiFiErrorCode.NONE

        nmcli_bin = shutil.which("nmcli")
        iwlist_bin = shutil.which("iwlist")
        wpa_cli_bin = shutil.which("wpa_cli")

        # 1. Try NetworkManager CLI (nmcli)
        if nmcli_bin:
            saved_by_ssid = self._get_saved_profiles_map()
            active_info = self.get_active_connection()
            active_ssid = active_info.get("ssid") if active_info else None

            try:
                res = self._run_nmcli(
                    nmcli_bin,
                    ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,BSSID,CHAN", "dev", "wifi", "list", "--rescan", "yes"],
                    timeout=10,
                )
                if res.returncode != 0:
                    err_txt = (res.stderr or res.stdout or "").lower()
                    if any(k in err_txt for k in ("scanning not allowed", "already scanning", "rescan")):
                        res = self._run_nmcli(
                            nmcli_bin,
                            ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,BSSID,CHAN", "dev", "wifi", "list"],
                            timeout=8,
                        )

                if res.returncode == 0:
                    networks = self._parse_nmcli_output(
                        res.stdout,
                        saved_by_ssid=saved_by_ssid,
                        active_ssid=active_ssid,
                    )
                    return networks, None, WiFiErrorCode.NONE

                raw_err = (res.stderr or res.stdout or "Error al escanear").strip()
                err_code, short_msg, _ = self._classify_error(raw_err, had_password=False)
                logger.warning(f"nmcli scan failed ({err_code.value}): {raw_err}")
                self._last_scan_error = short_msg
                self._last_scan_error_code = err_code
                # If nmcli itself is unavailable as daemon, try iwlist; otherwise return real error
                if "networkmanager is not running" not in raw_err.lower():
                    return [], short_msg, err_code
            except subprocess.TimeoutExpired:
                self._last_scan_error = "Tiempo agotado"
                self._last_scan_error_code = WiFiErrorCode.TIMEOUT
                return [], "Tiempo agotado", WiFiErrorCode.TIMEOUT
            except Exception as ex:
                logger.debug(f"nmcli scan exception: {ex}")
                self._last_scan_error = "Error al escanear"
                self._last_scan_error_code = WiFiErrorCode.UNKNOWN
                return [], "Error al escanear", WiFiErrorCode.UNKNOWN

        # 2. Try iwlist scan
        if iwlist_bin:
            saved_by_ssid = self._get_saved_profiles_map()
            active_info = self.get_active_connection()
            active_ssid = active_info.get("ssid") if active_info else None
            try:
                cmd = [iwlist_bin, self.interface, "scan"]
                if os.geteuid() != 0:
                    cmd.insert(0, "sudo")
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    networks = self._parse_iwlist_output(
                        res.stdout,
                        saved_by_ssid=saved_by_ssid,
                        active_ssid=active_ssid,
                    )
                    return networks, None, WiFiErrorCode.NONE
                raw_err = (res.stderr or res.stdout or "Error iwlist").strip()
                err_code, short_msg, _ = self._classify_error(raw_err, had_password=False)
                logger.warning(f"iwlist scan failed: {raw_err}")
                self._last_scan_error = short_msg
                self._last_scan_error_code = err_code
                return [], short_msg, err_code
            except Exception as ex:
                logger.debug(f"iwlist scan failed: {ex}")
                self._last_scan_error = "Error al escanear"
                self._last_scan_error_code = WiFiErrorCode.UNKNOWN
                return [], "Error al escanear", WiFiErrorCode.UNKNOWN

        if wpa_cli_bin or os.path.exists(f"/sys/class/net/{self.interface}"):
            return [], "Escaner no disponible", WiFiErrorCode.MANAGER_UNAVAILABLE

        # Development environment without wireless tools or physical interface
        return self._get_simulated_networks(), None, WiFiErrorCode.NONE

    def get_active_connection(self) -> Optional[Dict[str, Any]]:
        """Returns details of the currently active Wi-Fi connection on self.interface, if any."""
        if self._is_dry_run():
            if not self._mock_active_ssid:
                return None
            prof = self._mock_saved_profiles.get(self._mock_active_ssid, {})
            cat = next((c for c in self._mock_catalog if c["ssid"] == self._mock_active_ssid), {})
            return {
                "ssid": self._mock_active_ssid,
                "profile_id": prof.get("profile_id"),
                "profile_name": prof.get("name") or self._mock_active_ssid,
                "ip_address": self._get_interface_ip() or "192.168.1.142",
                "signal_pct": cat.get("signal_pct", 88),
                "security": cat.get("security", "WPA2-PSK"),
                "frequency": cat.get("frequency", "2.4GHz"),
                "interface": self.interface,
            }

        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            try:
                res = self._run_nmcli(
                    nmcli_bin,
                    ["-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
                    timeout=5,
                )
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        parts = self._split_nmcli_terse_line(line.strip())
                        if len(parts) < 4:
                            continue
                        name, uuid, conn_type, device = parts[0], parts[1], parts[2], parts[3]
                        if conn_type in ("802-11-wireless", "wifi") and (
                            device == self.interface or not device
                        ):
                            ssid = self._get_nmcli_profile_ssid(nmcli_bin, uuid) or name
                            return {
                                "ssid": ssid,
                                "profile_id": uuid,
                                "profile_name": name,
                                "ip_address": self._get_interface_ip(),
                                "interface": self.interface,
                            }
            except Exception as ex:
                logger.debug(f"get_active_connection nmcli error: {ex}")

        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            try:
                status = self._get_wpa_status(wpa_cli_bin)
                if status.get("wpa_state") == "COMPLETED" and status.get("ssid"):
                    return {
                        "ssid": status["ssid"],
                        "profile_id": status.get("id"),
                        "profile_name": status["ssid"],
                        "ip_address": self._get_interface_ip(),
                        "interface": self.interface,
                    }
            except Exception as ex:
                logger.debug(f"get_active_connection wpa_cli error: {ex}")

        return None

    def list_saved_profiles(self, include_scan: bool = False) -> List[SavedWiFiProfile]:
        """Lists stored Wi-Fi profiles from NetworkManager, wpa_cli, or mock state."""
        scan_map: Dict[str, WiFiNetwork] = {}
        if include_scan:
            for net in self.scan_networks():
                scan_map[net.ssid] = net

        if self._is_dry_run():
            profiles: List[SavedWiFiProfile] = []
            for ssid, data in self._mock_saved_profiles.items():
                net = scan_map.get(ssid)
                cat = next((c for c in self._mock_catalog if c["ssid"] == ssid), None)
                in_use = (ssid == self._mock_active_ssid)
                in_range = (net is not None) or (cat is not None)
                signal = net.signal_pct if net else (cat["signal_pct"] if cat else 0)
                profiles.append(
                    SavedWiFiProfile(
                        ssid=ssid,
                        profile_id=str(data.get("profile_id", ssid)),
                        name=str(data.get("name", ssid)),
                        security=str(data.get("security", "WPA2-PSK")),
                        autoconnect=bool(data.get("autoconnect", True)),
                        in_use=in_use,
                        in_range=in_range,
                        signal_pct=signal,
                    )
                )
            profiles.sort(key=lambda p: (not p.in_use, not p.in_range, -p.signal_pct, p.ssid.lower()))
            return profiles

        raw_profiles = self._query_saved_profiles_raw()
        active_info = self.get_active_connection()
        active_ssid = active_info.get("ssid") if active_info else None
        active_uuid = active_info.get("profile_id") if active_info else None

        by_ssid: Dict[str, SavedWiFiProfile] = {}
        for prof in raw_profiles:
            is_active = bool(
                (active_uuid and prof.profile_id == active_uuid)
                or (active_ssid and prof.ssid == active_ssid)
            )
            prof.in_use = prof.in_use or is_active
            net = scan_map.get(prof.ssid)
            if net:
                prof.in_range = True
                prof.signal_pct = net.signal_pct
                if not prof.security:
                    prof.security = net.security

            existing = by_ssid.get(prof.ssid)
            if existing is None:
                by_ssid[prof.ssid] = prof
            elif prof.in_use and not existing.in_use:
                by_ssid[prof.ssid] = prof

        result = list(by_ssid.values())
        result.sort(key=lambda p: (not p.in_use, not p.in_range, -p.signal_pct, p.ssid.lower()))
        return result

    def _get_saved_profiles_map(self) -> Dict[str, SavedWiFiProfile]:
        try:
            profiles = self.list_saved_profiles(include_scan=False)
            return {p.ssid: p for p in profiles}
        except Exception as ex:
            logger.debug(f"Failed loading saved profiles map: {ex}")
            return {}

    def _query_saved_profiles_raw(self) -> List[SavedWiFiProfile]:
        """Queries all saved Wi-Fi connection profiles without deduplicating."""
        profiles: List[SavedWiFiProfile] = []

        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            try:
                res = self._run_nmcli(
                    nmcli_bin,
                    ["-t", "-f", "UUID,NAME,TYPE,DEVICE,AUTOCONNECT", "connection", "show"],
                    timeout=5,
                )
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        parts = self._split_nmcli_terse_line(line.strip())
                        if len(parts) < 3:
                            continue
                        uuid, name, conn_type = parts[0], parts[1], parts[2]
                        device = parts[3] if len(parts) > 3 else ""
                        autoconnect_raw = parts[4].lower() if len(parts) > 4 else "yes"
                        if conn_type not in ("802-11-wireless", "wifi"):
                            continue
                        ssid, key_mgmt = self._get_nmcli_profile_details(nmcli_bin, uuid)
                        resolved_ssid = ssid or name
                        if not resolved_ssid:
                            continue
                        profiles.append(
                            SavedWiFiProfile(
                                ssid=resolved_ssid,
                                profile_id=uuid,
                                name=name,
                                security=key_mgmt or "",
                                autoconnect=autoconnect_raw in ("yes", "true", "1"),
                                in_use=(device == self.interface),
                            )
                        )
                    return profiles
            except Exception as ex:
                logger.debug(f"nmcli list saved profiles failed: {ex}")

        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            try:
                res = subprocess.run(
                    [wpa_cli_bin, "-i", self.interface, "list_networks"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0:
                    for line in res.stdout.splitlines()[1:]:
                        cols = line.split("\t")
                        if len(cols) >= 2:
                            net_id = cols[0].strip()
                            ssid = cols[1].strip()
                            flags = cols[3].strip() if len(cols) > 3 else ""
                            if ssid:
                                profiles.append(
                                    SavedWiFiProfile(
                                        ssid=ssid,
                                        profile_id=net_id,
                                        name=ssid,
                                        in_use=("[CURRENT]" in flags),
                                    )
                                )
            except Exception as ex:
                logger.debug(f"wpa_cli list_networks failed: {ex}")

        return profiles

    def _get_nmcli_profile_ssid(self, nmcli_bin: str, uuid: str) -> Optional[str]:
        ssid, _ = self._get_nmcli_profile_details(nmcli_bin, uuid)
        return ssid

    def _get_nmcli_profile_details(self, nmcli_bin: str, uuid: str) -> Tuple[Optional[str], Optional[str]]:
        try:
            res = self._run_nmcli(
                nmcli_bin,
                ["-g", "802-11-wireless.ssid,802-11-wireless-security.key-mgmt", "connection", "show", "uuid", uuid],
                timeout=4,
            )
            if res.returncode == 0:
                lines = [ln.strip() for ln in res.stdout.splitlines()]
                ssid = lines[0] if len(lines) > 0 and lines[0] else None
                key_mgmt = lines[1] if len(lines) > 1 and lines[1] else None
                return ssid, key_mgmt
        except Exception:
            pass
        return None, None

    def _find_profiles_for_ssid(self, ssid: str) -> List[SavedWiFiProfile]:
        return [p for p in self._query_saved_profiles_raw() if p.ssid == ssid]

    def _already_connected_response(
        self,
        ssid: str,
        active: Dict[str, Any],
        profile_id: Optional[str] = None,
        security_hint: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Builds a uniform success payload when the interface is already linked to ssid."""
        ip_addr = active.get("ip_address") or self._get_interface_ip() or "Asignada"
        return True, sanitize_oled_text(f"Ya conectado a {ssid}"), {
            "ssid": ssid,
            "already_connected": True,
            "ip_address": ip_addr,
            "signal": f"{active.get('signal_pct')}%" if active.get("signal_pct") else "",
            "security": active.get("security") or security_hint or "",
            "interface": self.interface,
            "profile_id": active.get("profile_id") or profile_id,
            "error_code": WiFiErrorCode.ALREADY_CONNECTED.value,
        }

    def connect_saved_network(
        self,
        ssid: str,
        profile_id: Optional[str] = None,
        timeout: int = 18,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Connects to a previously saved Wi-Fi network using stored credentials.
        Returns needs_password=True in details if stored secrets fail authentication.
        """
        ssid = ssid.strip()
        if not ssid:
            return False, "SSID no valido", {"error_code": WiFiErrorCode.NO_NETWORK_FOUND.value}

        active = self.get_active_connection()
        if active and active.get("ssid") == ssid:
            return self._already_connected_response(ssid, active, profile_id=profile_id)

        if self._is_dry_run():
            time.sleep(0.5)
            prof = self._mock_saved_profiles.get(ssid)
            if not prof:
                return False, "Falta la clave", {
                    "ssid": ssid,
                    "reason": "Sin perfil guardado",
                    "error_code": WiFiErrorCode.SECRETS_REQUIRED.value,
                    "needs_password": True,
                }
            if prof.get("password") != prof.get("server_password"):
                return False, "Clave incorrecta", {
                    "ssid": ssid,
                    "reason": "La clave guardada fallo",
                    "error_code": WiFiErrorCode.AUTH_FAILED.value,
                    "needs_password": True,
                }
            self._mock_active_ssid = ssid
            cat = next((c for c in self._mock_catalog if c["ssid"] == ssid), {})
            return True, sanitize_oled_text(f"Conectado a {ssid}"), {
                "ssid": ssid,
                "ip_address": "192.168.1.142",
                "gateway": "192.168.1.1",
                "signal": f"{cat.get('signal_pct', 84)}%",
                "security": prof.get("security", "WPA2-PSK"),
                "interface": self.interface,
                "profile_id": prof.get("profile_id"),
                "used_saved": True,
            }

        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            target_uuid = profile_id
            if not target_uuid:
                matches = self._find_profiles_for_ssid(ssid)
                target_uuid = matches[0].profile_id if matches else None

            if not target_uuid:
                return False, "Falta la clave", {
                    "ssid": ssid,
                    "reason": "Ingrese nueva clave",
                    "error_code": WiFiErrorCode.SECRETS_REQUIRED.value,
                    "needs_password": True,
                }

            try:
                res = self._run_nmcli(
                    nmcli_bin,
                    ["--wait", str(timeout), "connection", "up", "uuid", target_uuid, "ifname", self.interface],
                    timeout=timeout + 5,
                )
                if res.returncode == 0:
                    ip_addr = self._wait_for_ip(max_wait=4.0)
                    return True, sanitize_oled_text(f"Conectado a {ssid}"), {
                        "ssid": ssid,
                        "ip_address": ip_addr or "Asignada (DHCP)",
                        "interface": self.interface,
                        "profile_id": target_uuid,
                        "used_saved": True,
                    }

                raw_err = (res.stderr or res.stdout or "Error de conexion").strip()
                err_code, short_msg, hint = self._classify_error(raw_err, had_password=False)
                logger.warning(f"nmcli saved connect failed for '{ssid}' ({err_code.value}): {raw_err}")
                needs_pw = err_code in (
                    WiFiErrorCode.AUTH_FAILED,
                    WiFiErrorCode.SECRETS_REQUIRED,
                    WiFiErrorCode.PROFILE_CONFLICT,
                )
                if needs_pw:
                    hint = "Ingrese nueva clave"
                return False, short_msg, {
                    "ssid": ssid,
                    "error_code": err_code.value,
                    "reason": hint,
                    "needs_password": needs_pw,
                    "profile_id": target_uuid,
                    "raw_error": raw_err,
                }
            except subprocess.TimeoutExpired:
                return False, "Tiempo agotado", {
                    "ssid": ssid,
                    "error_code": WiFiErrorCode.TIMEOUT.value,
                    "reason": "AP no respondio",
                    "needs_password": False,
                }
            except Exception as ex:
                return False, "Error de conexion", {
                    "ssid": ssid,
                    "error_code": WiFiErrorCode.UNKNOWN.value,
                    "reason": shorten_oled_label(str(ex), 19),
                    "raw_error": str(ex),
                }

        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            target_id = profile_id
            if not target_id:
                matches = self._find_profiles_for_ssid(ssid)
                target_id = matches[0].profile_id if matches else None
            if not target_id:
                return False, "Falta la clave", {
                    "ssid": ssid,
                    "reason": "Ingrese nueva clave",
                    "error_code": WiFiErrorCode.SECRETS_REQUIRED.value,
                    "needs_password": True,
                }
            return self._wpa_select_and_verify(
                wpa_cli_bin=wpa_cli_bin,
                ssid=ssid,
                net_id=target_id,
                timeout=timeout,
                created_new=False,
                used_saved=True,
            )

        return False, "Sin gestor Wi-Fi", {
            "ssid": ssid,
            "error_code": WiFiErrorCode.MANAGER_UNAVAILABLE.value,
            "reason": "Falta nmcli/wpa_cli",
        }

    def connect_network(
        self,
        ssid: str,
        password: Optional[str] = None,
        timeout: int = 18,
        profile_id: Optional[str] = None,
        force_update: bool = False,
        security_hint: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Attempts to connect to the specified Wi-Fi network.
        Guards against re-connecting to an already active SSID, supports saved profile
        activation, updates stale profiles cleanly, and classifies errors concisely.
        Returns: (success: bool, message: str, details_dict: dict)
        """
        ssid = ssid.strip()
        if not ssid:
            return False, "SSID no valido", {
                "error_code": WiFiErrorCode.NO_NETWORK_FOUND.value,
                "reason": "Nombre de red vacio",
            }

        # 1. Pre-flight check: already connected to this exact SSID
        active = self.get_active_connection()
        if active and active.get("ssid") == ssid and not force_update:
            return self._already_connected_response(
                ssid, active, profile_id=profile_id, security_hint=security_hint
            )

        # 2. Pre-flight check: Enterprise 802.1X requires identity/cert beyond simple PSK
        if security_hint and any(t in security_hint.upper() for t in ("802.1X", "EAP", "ENTERPRISE")):
            if not profile_id and not self._find_profiles_for_ssid(ssid):
                return False, "Requiere 802.1X", {
                    "ssid": ssid,
                    "error_code": WiFiErrorCode.ENTERPRISE_UNSUPPORTED.value,
                    "reason": "No soportado aqui",
                    "needs_password": False,
                }

        # 2.5 Validate submitted password early before running dry-run/nmcli
        if password is not None and password == "" and (
            force_update or (security_hint is not None and "OPEN" not in security_hint.upper())
        ):
            return False, "Clave vacia", {
                "ssid": ssid,
                "error_code": WiFiErrorCode.AUTH_FAILED.value,
                "reason": "Ingrese una clave",
                "needs_password": True,
            }

        if password is not None and password != "":
            is_dry_test_trigger = self._is_dry_run() and password.lower() in ("wrong", "error", "invalid")
            if not is_dry_test_trigger:
                valid_pw, val_msg, val_code = validate_wifi_password(password, security=security_hint)
                if not valid_pw:
                    return False, val_msg, {
                        "ssid": ssid,
                        "error_code": val_code.value,
                        "reason": val_msg,
                        "needs_password": True,
                    }

        # 3. Saved-profile connect when no new password is provided
        if password is None and not force_update:
            if profile_id or self._find_profiles_for_ssid(ssid):
                return self.connect_saved_network(ssid=ssid, profile_id=profile_id, timeout=timeout)

        # 4. Safe mock / Dry-run
        if self._is_dry_run():
            time.sleep(0.6)
            if password is not None and password.lower() in ("wrong", "error", "invalid"):
                return False, "Clave incorrecta", {
                    "ssid": ssid,
                    "reason": "Verifique la clave",
                    "error_code": WiFiErrorCode.AUTH_FAILED.value,
                    "needs_password": True,
                }
            # Save/update mock profile and mark active
            existing_mock = self._mock_saved_profiles.get(ssid, {})
            new_uuid = existing_mock.get("profile_id") or f"mock-uuid-{len(self._mock_saved_profiles) + 1}"
            self._mock_saved_profiles[ssid] = {
                "profile_id": new_uuid,
                "name": ssid,
                "password": password or "",
                "server_password": password or "",
                "security": security_hint or ("WPA2-PSK" if password else "Open"),
                "autoconnect": True,
            }
            self._mock_active_ssid = ssid
            return True, sanitize_oled_text(f"Conectado a {ssid}"), {
                "ssid": ssid,
                "ip_address": "192.168.1.142",
                "gateway": "192.168.1.1",
                "signal": "92%",
                "security": security_hint or ("WPA2-PSK" if password else "Open"),
                "interface": self.interface,
                "profile_id": new_uuid,
            }

        # 5. Connect via nmcli
        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            existing_profiles = self._find_profiles_for_ssid(ssid)

            # When updating a changed password or removing duplicate profiles, clean stale ones first
            if force_update and existing_profiles:
                self._delete_nmcli_profiles(nmcli_bin, [p.profile_id for p in existing_profiles])
                existing_profiles = []
            elif len(existing_profiles) > 1:
                self._delete_nmcli_profiles(nmcli_bin, [p.profile_id for p in existing_profiles[1:]])
                existing_profiles = existing_profiles[:1]

            cmd = ["--wait", str(timeout), "device", "wifi", "connect", ssid]
            if password is not None and password != "":
                cmd.extend(["password", password])
            cmd.extend(["ifname", self.interface])

            try:
                res = self._run_nmcli(nmcli_bin, cmd, timeout=timeout + 5)

                # Retry once cleanly if NetworkManager hits a stale profile / secrets conflict
                # or if the scan cache expired while the user typed the password.
                if res.returncode != 0:
                    raw_first = (res.stderr or res.stdout or "").strip()
                    first_code, _, _ = self._classify_error(raw_first, had_password=(password is not None))
                    if existing_profiles and password is not None and first_code in (
                        WiFiErrorCode.PROFILE_CONFLICT,
                        WiFiErrorCode.SECRETS_REQUIRED,
                    ):
                        logger.info(f"Cleaning conflicting NM profile(s) for '{ssid}' and retrying...")
                        self._delete_nmcli_profiles(nmcli_bin, [p.profile_id for p in existing_profiles])
                        res = self._run_nmcli(nmcli_bin, cmd, timeout=timeout + 5)
                    elif first_code == WiFiErrorCode.NO_NETWORK_FOUND:
                        logger.info(f"SSID '{ssid}' missing from cache; rescanning and retrying...")
                        try:
                            self._run_nmcli(
                                nmcli_bin,
                                ["device", "wifi", "rescan", "ifname", self.interface],
                                timeout=6,
                            )
                            time.sleep(1.5)
                        except Exception:
                            pass
                        res = self._run_nmcli(nmcli_bin, cmd, timeout=timeout + 5)

                if res.returncode == 0:
                    ip_addr = self._wait_for_ip(max_wait=4.0)
                    return True, sanitize_oled_text(f"Conectado a {ssid}"), {
                        "ssid": ssid,
                        "ip_address": ip_addr or "Asignada (DHCP)",
                        "interface": self.interface,
                    }

                raw_err = (res.stderr or res.stdout or "Error de conexion").strip()
                err_code, short_msg, hint = self._classify_error(raw_err, had_password=(password is not None))
                # After profile-conflict cleanup retry, remaining secret/profile failure means auth rejected
                if err_code == WiFiErrorCode.PROFILE_CONFLICT:
                    err_code = WiFiErrorCode.AUTH_FAILED
                    short_msg = "Clave incorrecta"
                    hint = "Verifique la clave"
                logger.warning(f"nmcli connect failed for '{ssid}' ({err_code.value}): {raw_err}")
                return False, short_msg, {
                    "ssid": ssid,
                    "error_code": err_code.value,
                    "reason": hint,
                    "needs_password": err_code in (WiFiErrorCode.AUTH_FAILED, WiFiErrorCode.SECRETS_REQUIRED),
                    "raw_error": raw_err,
                }
            except subprocess.TimeoutExpired:
                return False, "Tiempo agotado", {
                    "ssid": ssid,
                    "error_code": WiFiErrorCode.TIMEOUT.value,
                    "reason": "AP no respondio",
                    "needs_password": False,
                }
            except Exception as ex:
                return False, "Error de conexion", {
                    "ssid": ssid,
                    "error_code": WiFiErrorCode.UNKNOWN.value,
                    "reason": shorten_oled_label(str(ex), 19),
                    "raw_error": str(ex),
                }

        # 6. Connect via wpa_cli (fallback)
        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            return self._connect_via_wpa_cli(
                wpa_cli_bin=wpa_cli_bin,
                ssid=ssid,
                password=password,
                timeout=timeout,
            )

        return False, "Sin gestor Wi-Fi", {
            "ssid": ssid,
            "error_code": WiFiErrorCode.MANAGER_UNAVAILABLE.value,
            "reason": "Falta nmcli/wpa_cli",
        }

    def disconnect_network(self, ssid: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """Disconnects the active Wi-Fi connection cleanly without deleting saved profiles."""
        if self._is_dry_run():
            prev = self._mock_active_ssid or ssid or self.interface
            if ssid and self._mock_active_ssid and self._mock_active_ssid != ssid:
                return False, "Red no activa", {"ssid": ssid, "error_code": WiFiErrorCode.NO_NETWORK_FOUND.value}
            self._mock_active_ssid = None
            return True, "Desconectado OK", {"ssid": prev, "interface": self.interface}

        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            try:
                active = self.get_active_connection()
                if active and active.get("profile_id"):
                    res = self._run_nmcli(nmcli_bin, ["connection", "down", "uuid", active["profile_id"]], timeout=8)
                else:
                    res = self._run_nmcli(nmcli_bin, ["device", "disconnect", self.interface], timeout=8)
                if res.returncode == 0:
                    return True, "Desconectado OK", {"ssid": (active or {}).get("ssid") or ssid or "", "interface": self.interface}
                raw_err = (res.stderr or res.stdout or "Error al desconectar").strip()
                err_code, short_msg, hint = self._classify_error(raw_err, had_password=False)
                return False, short_msg, {"ssid": ssid or "", "error_code": err_code.value, "reason": hint, "raw_error": raw_err}
            except Exception as ex:
                return False, "Error al desconectar", {"ssid": ssid or "", "error_code": WiFiErrorCode.UNKNOWN.value, "reason": shorten_oled_label(str(ex), 19)}

        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            try:
                res = subprocess.run([wpa_cli_bin, "-i", self.interface, "disconnect"], capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and "OK" in (res.stdout or "").upper():
                    return True, "Desconectado OK", {"ssid": ssid or "", "interface": self.interface}
                return False, "Error al desconectar", {"ssid": ssid or "", "error_code": WiFiErrorCode.UNKNOWN.value}
            except Exception as ex:
                return False, "Error al desconectar", {"ssid": ssid or "", "error_code": WiFiErrorCode.UNKNOWN.value, "reason": shorten_oled_label(str(ex), 19)}

        return False, "Sin gestor Wi-Fi", {"ssid": ssid or "", "error_code": WiFiErrorCode.MANAGER_UNAVAILABLE.value}

    def forget_network(self, ssid: str, profile_id: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """Deletes stored connection profile(s) for an SSID."""
        ssid = ssid.strip()
        if not ssid and not profile_id:
            return False, "SSID no valido", {"error_code": WiFiErrorCode.NO_NETWORK_FOUND.value}

        if self._is_dry_run():
            removed = self._mock_saved_profiles.pop(ssid, None)
            if not removed and profile_id:
                for key, val in list(self._mock_saved_profiles.items()):
                    if val.get("profile_id") == profile_id:
                        self._mock_saved_profiles.pop(key, None)
                        removed = val
                        break
            if self._mock_active_ssid == ssid:
                self._mock_active_ssid = None
            if removed:
                return True, "Red olvidada OK", {"ssid": ssid}
            return False, "Perfil no hallado", {"ssid": ssid, "error_code": WiFiErrorCode.NO_NETWORK_FOUND.value}

        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            uuids = [p.profile_id for p in self._find_profiles_for_ssid(ssid)] if ssid else []
            if profile_id and profile_id not in uuids:
                uuids.append(profile_id)
            if not uuids:
                return False, "Perfil no hallado", {"ssid": ssid, "error_code": WiFiErrorCode.NO_NETWORK_FOUND.value}
            deleted = self._delete_nmcli_profiles(nmcli_bin, uuids)
            if deleted > 0:
                return True, "Red olvidada OK", {"ssid": ssid, "deleted": deleted}
            return False, "Error al olvidar", {"ssid": ssid, "error_code": WiFiErrorCode.UNKNOWN.value}

        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            ids = [p.profile_id for p in self._find_profiles_for_ssid(ssid)] if ssid else []
            if profile_id and profile_id not in ids:
                ids.append(profile_id)
            if not ids:
                return False, "Perfil no hallado", {"ssid": ssid, "error_code": WiFiErrorCode.NO_NETWORK_FOUND.value}
            ok_any = False
            for net_id in ids:
                res = subprocess.run([wpa_cli_bin, "-i", self.interface, "remove_network", str(net_id)], capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and "OK" in (res.stdout or "").upper():
                    ok_any = True
            if ok_any:
                subprocess.run([wpa_cli_bin, "-i", self.interface, "save_config"], capture_output=True, text=True, timeout=5)
                return True, "Red olvidada OK", {"ssid": ssid}
            return False, "Error al olvidar", {"ssid": ssid, "error_code": WiFiErrorCode.UNKNOWN.value}

        return False, "Sin gestor Wi-Fi", {"ssid": ssid, "error_code": WiFiErrorCode.MANAGER_UNAVAILABLE.value}

    def _delete_nmcli_profiles(self, nmcli_bin: str, uuids: List[str]) -> int:
        deleted = 0
        for uuid in uuids:
            try:
                res = self._run_nmcli(nmcli_bin, ["connection", "delete", "uuid", uuid], timeout=6)
                if res.returncode == 0:
                    deleted += 1
            except Exception as ex:
                logger.debug(f"Failed deleting NM uuid {uuid}: {ex}")
        return deleted

    def _connect_via_wpa_cli(
        self,
        wpa_cli_bin: str,
        ssid: str,
        password: Optional[str],
        timeout: int,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        try:
            existing = self._find_profiles_for_ssid(ssid)
            created_new = False
            if existing:
                net_id = existing[0].profile_id
            else:
                res = subprocess.run(
                    [wpa_cli_bin, "-i", self.interface, "add_network"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                net_id = res.stdout.strip()
                if not net_id.isdigit():
                    return False, "Error wpa_cli", {
                        "ssid": ssid,
                        "error_code": WiFiErrorCode.UNKNOWN.value,
                        "reason": "No creo perfil",
                    }
                created_new = True

            escaped_ssid = self._escape_wpa_str(ssid)
            r_ssid = subprocess.run(
                [wpa_cli_bin, "-i", self.interface, "set_network", net_id, "ssid", escaped_ssid],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "OK" not in (r_ssid.stdout or "").upper():
                if created_new:
                    subprocess.run([wpa_cli_bin, "-i", self.interface, "remove_network", net_id], capture_output=True, timeout=5)
                return False, "SSID invalido", {"ssid": ssid, "error_code": WiFiErrorCode.UNKNOWN.value}

            if password:
                escaped_psk = self._escape_wpa_str(password)
                r_key = subprocess.run(
                    [wpa_cli_bin, "-i", self.interface, "set_network", net_id, "psk", escaped_psk],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            else:
                r_key = subprocess.run(
                    [wpa_cli_bin, "-i", self.interface, "set_network", net_id, "key_mgmt", "NONE"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

            if "OK" not in (r_key.stdout or "").upper():
                if created_new:
                    subprocess.run([wpa_cli_bin, "-i", self.interface, "remove_network", net_id], capture_output=True, timeout=5)
                return False, "Clave invalida", {
                    "ssid": ssid,
                    "error_code": WiFiErrorCode.AUTH_FAILED.value,
                    "reason": "Formato PSK invalido",
                    "needs_password": True,
                }

            subprocess.run([wpa_cli_bin, "-i", self.interface, "enable_network", net_id], capture_output=True, timeout=5)
            return self._wpa_select_and_verify(
                wpa_cli_bin=wpa_cli_bin,
                ssid=ssid,
                net_id=net_id,
                timeout=timeout,
                created_new=created_new,
                used_saved=False,
            )
        except Exception as ex:
            return False, "Error wpa_cli", {
                "ssid": ssid,
                "error_code": WiFiErrorCode.UNKNOWN.value,
                "reason": shorten_oled_label(str(ex), 19),
                "raw_error": str(ex),
            }

    def _wpa_select_and_verify(
        self,
        wpa_cli_bin: str,
        ssid: str,
        net_id: str,
        timeout: int,
        created_new: bool,
        used_saved: bool,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        res_sel = subprocess.run(
            [wpa_cli_bin, "-i", self.interface, "select_network", str(net_id)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res_sel.returncode != 0 or "FAIL" in (res_sel.stdout or "").upper():
            if created_new:
                subprocess.run([wpa_cli_bin, "-i", self.interface, "remove_network", str(net_id)], capture_output=True, timeout=5)
            return False, "Error al enlazar", {
                "ssid": ssid,
                "error_code": WiFiErrorCode.UNKNOWN.value,
                "reason": "wpa_cli fallo",
            }

        deadline = time.monotonic() + max(5, min(timeout, 20))
        saw_handshake = False
        saw_assoc = False
        last_state = ""

        while time.monotonic() < deadline:
            status = self._get_wpa_status(wpa_cli_bin)
            state = status.get("wpa_state", "").upper()
            cur_ssid = status.get("ssid", "")
            last_state = state

            if state == "4WAY_HANDSHAKE":
                saw_handshake = True
            elif state in ("ASSOCIATING", "ASSOCIATED"):
                saw_assoc = True
            elif state == "COMPLETED" and (not cur_ssid or cur_ssid == ssid):
                ip_addr = self._wait_for_ip(max_wait=4.0)
                subprocess.run([wpa_cli_bin, "-i", self.interface, "save_config"], capture_output=True, timeout=5)
                if not ip_addr:
                    return False, "Sin IP por DHCP", {
                        "ssid": ssid,
                        "error_code": WiFiErrorCode.DHCP_FAILED.value,
                        "reason": "Revise el router",
                    }
                return True, sanitize_oled_text(f"Conectado a {ssid}"), {
                    "ssid": ssid,
                    "ip_address": ip_addr,
                    "interface": self.interface,
                    "profile_id": str(net_id),
                    "used_saved": used_saved,
                }
            elif saw_handshake and state in ("DISCONNECTED", "SCANNING"):
                if created_new:
                    subprocess.run([wpa_cli_bin, "-i", self.interface, "remove_network", str(net_id)], capture_output=True, timeout=5)
                return False, "Clave incorrecta", {
                    "ssid": ssid,
                    "error_code": WiFiErrorCode.AUTH_FAILED.value,
                    "reason": "Fallo handshake WPA",
                    "needs_password": True,
                }

            time.sleep(0.5)

        if created_new:
            subprocess.run([wpa_cli_bin, "-i", self.interface, "remove_network", str(net_id)], capture_output=True, timeout=5)

        if saw_handshake:
            return False, "Clave incorrecta", {
                "ssid": ssid,
                "error_code": WiFiErrorCode.AUTH_FAILED.value,
                "reason": "Fallo autenticacion",
                "needs_password": True,
            }
        if not saw_assoc and last_state in ("SCANNING", "DISCONNECTED", "INACTIVE", ""):
            return False, "Red no encontrada", {
                "ssid": ssid,
                "error_code": WiFiErrorCode.NO_NETWORK_FOUND.value,
                "reason": "Fuera de alcance",
            }
        return False, "Tiempo agotado", {
            "ssid": ssid,
            "error_code": WiFiErrorCode.TIMEOUT.value,
            "reason": "AP no respondio",
        }

    def _get_wpa_status(self, wpa_cli_bin: str) -> Dict[str, str]:
        status: Dict[str, str] = {}
        try:
            res = subprocess.run(
                [wpa_cli_bin, "-i", self.interface, "status"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        status[k.strip()] = v.strip()
        except Exception:
            pass
        return status

    def _wait_for_ip(self, max_wait: float = 2.0) -> Optional[str]:
        deadline = time.monotonic() + max_wait
        ip_addr = self._get_interface_ip()
        while not ip_addr and time.monotonic() < deadline:
            time.sleep(0.5)
            ip_addr = self._get_interface_ip()
        return ip_addr

    @staticmethod
    def _escape_wpa_str(val: str) -> str:
        """Escapes quotes and backslashes for wpa_cli string parameters."""
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _get_interface_ip(self) -> Optional[str]:
        """Reads IPv4 address of interface."""
        return get_interface_ip(self.interface)

    def get_ip(self) -> Optional[str]:
        """Public accessor for interface's active IPv4 address."""
        return get_interface_ip(self.interface)

    def _classify_error(self, err_msg: str, had_password: bool = True) -> Tuple[WiFiErrorCode, str, str]:
        """
        Classifies nmcli/wpa errors into (WiFiErrorCode, short_title_19, short_hint_19).
        All output strings are strictly ASCII-clean for SH1106 OLED rendering.
        """
        lower = sanitize_oled_text(err_msg).lower()
        # Avoid matching keywords inside quoted SSIDs (e.g. 'LateAP', 'Radio_5G')
        lower_clean = re.sub(r"with ssid '[^']*'", "with ssid", lower)

        if "802.1x" in lower_clean or "enterprise" in lower_clean or re.search(r"\beap\b", lower_clean):
            return WiFiErrorCode.ENTERPRISE_UNSUPPORTED, "Requiere 802.1X", "No soportado aqui"

        if any(k in lower_clean for k in ("wi-fi is disabled", "wifi is disabled", "radio", "rfkill", "sw disabled", "hw disabled", "desactivad", "apagad")):
            return WiFiErrorCode.WIFI_DISABLED, "Wi-Fi desactivado", "Active el radio"

        if any(k in lower_clean for k in ("not managed", "unmanaged", "no device found", "interface does not exist", "unavailable", "device not found", "no esta listo")):
            return WiFiErrorCode.INTERFACE_UNAVAILABLE, "Interfaz no lista", f"Revise {shorten_oled_label(self.interface, 12)}"

        if any(k in lower_clean for k in ("no network", "could not be found", "not found", "no match", "no se encontro")):
            return WiFiErrorCode.NO_NETWORK_FOUND, "Red no encontrada", "Fuera de alcance"

        if any(k in lower_clean for k in ("ip configuration", "dhcp", "ip-config")):
            return WiFiErrorCode.DHCP_FAILED, "Sin IP por DHCP", "Revise el router"

        if any(k in lower_clean for k in ("timeout", "timed out", "tiempo", "agotado")):
            return WiFiErrorCode.TIMEOUT, "Tiempo agotado", "AP no respondio"

        if any(k in lower_clean for k in ("secrets were required", "no agents were available", "secretos", "secrets")):
            if not had_password:
                return WiFiErrorCode.SECRETS_REQUIRED, "Clave requerida", "Ingrese nueva clave"
            return WiFiErrorCode.PROFILE_CONFLICT, "Error de perfil", "Reintente conectar"

        if any(
            k in lower_clean
            for k in (
                "wrong key",
                "bad password",
                "invalid password",
                "incorrect password",
                "password",
                "psk",
                "4-way handshake",
                "authenticat",
                "autentic",
                "contrasena",
                "802-11-wireless-security",
                "failed to activate",
            )
        ):
            return WiFiErrorCode.AUTH_FAILED, "Clave incorrecta", "Verifique la clave"

        short_raw = shorten_oled_label(err_msg, 19) or "Fallo de enlace"
        return WiFiErrorCode.UNKNOWN, "Error de conexion", short_raw

    def _sanitize_error(self, err_msg: str) -> str:
        """Converts long network errors to clear concise ASCII strings for OLED display."""
        _, short_title, _ = self._classify_error(err_msg, had_password=True)
        return short_title

    @staticmethod
    def _sort_networks(networks: List[WiFiNetwork]) -> List[WiFiNetwork]:
        return sorted(
            networks,
            key=lambda n: (not n.in_use, not n.is_saved, -n.signal_pct, n.ssid.lower()),
        )

    def _parse_nmcli_output(
        self,
        output: str,
        saved_by_ssid: Optional[Dict[str, SavedWiFiProfile]] = None,
        active_ssid: Optional[str] = None,
    ) -> List[WiFiNetwork]:
        """Parses colon-delimited terse output from nmcli safely."""
        networks: Dict[str, WiFiNetwork] = {}
        saved_map = saved_by_ssid or {}

        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = self._split_nmcli_terse_line(line)
            if len(parts) < 4:
                continue

            in_use = (parts[0].strip() == "*")
            ssid = parts[1].strip()
            if not ssid or ssid == "--":
                continue

            if active_ssid and ssid == active_ssid:
                in_use = True

            try:
                signal = int(parts[2].strip())
            except ValueError:
                signal = 50
            security = parts[3].strip() or "Open"
            bssid = parts[4].strip() if len(parts) > 4 else ""
            try:
                channel = int(parts[5].strip()) if len(parts) > 5 and parts[5].strip() else 1
            except ValueError:
                channel = 1
            frequency = "5GHz" if channel > 14 else "2.4GHz"

            saved_prof = saved_map.get(ssid)
            net = WiFiNetwork(
                ssid=ssid,
                signal_pct=signal,
                security=security,
                in_use=in_use,
                bssid=bssid,
                frequency=frequency,
                channel=channel,
                is_saved=bool(saved_prof or in_use),
                saved_profile_id=saved_prof.profile_id if saved_prof else None,
                saved_profile_name=saved_prof.name if saved_prof else None,
            )

            existing = networks.get(ssid)
            if existing is None:
                networks[ssid] = net
            else:
                combined_in_use = existing.in_use or net.in_use
                # Prefer active AP, otherwise strongest signal
                if (net.in_use and not existing.in_use) or (
                    net.in_use == existing.in_use and signal > existing.signal_pct
                ):
                    net.in_use = combined_in_use
                    net.is_saved = net.is_saved or existing.is_saved or combined_in_use
                    networks[ssid] = net
                else:
                    existing.in_use = combined_in_use
                    existing.is_saved = existing.is_saved or net.is_saved or combined_in_use

        return self._sort_networks(list(networks.values()))

    def _parse_iwlist_output(
        self,
        output: str,
        saved_by_ssid: Optional[Dict[str, SavedWiFiProfile]] = None,
        active_ssid: Optional[str] = None,
    ) -> List[WiFiNetwork]:
        """Parses output from iwlist wlan0 scan."""
        networks: Dict[str, WiFiNetwork] = {}
        saved_map = saved_by_ssid or {}
        current_ssid = None
        current_signal = 50
        current_security = "Open"

        for line in output.splitlines():
            line = line.strip()
            if "ESSID:" in line:
                match = re.search(r'ESSID:"([^"]+)"', line)
                if match:
                    current_ssid = match.group(1).strip()
            elif "Quality=" in line or "Signal level=" in line:
                match = re.search(r"Quality=(\d+)/(\d+)", line)
                if match:
                    current_signal = int((int(match.group(1)) / float(match.group(2))) * 100)
            elif "Encryption key:on" in line:
                current_security = "WPA2"
            elif "Encryption key:off" in line:
                current_security = "Open"

            if current_ssid:
                saved_prof = saved_map.get(current_ssid)
                in_use = bool(active_ssid and current_ssid == active_ssid)
                net = WiFiNetwork(
                    ssid=current_ssid,
                    signal_pct=current_signal,
                    security=current_security,
                    in_use=in_use,
                    is_saved=bool(saved_prof or in_use),
                    saved_profile_id=saved_prof.profile_id if saved_prof else None,
                    saved_profile_name=saved_prof.name if saved_prof else None,
                )
                if current_ssid not in networks or current_signal > networks[current_ssid].signal_pct:
                    networks[current_ssid] = net
                current_ssid = None

        return self._sort_networks(list(networks.values()))

    def _get_simulated_networks(self) -> List[WiFiNetwork]:
        """Returns stateful simulated Wi-Fi networks for testing."""
        networks: List[WiFiNetwork] = []
        for entry in self._mock_catalog:
            ssid = entry["ssid"]
            saved = self._mock_saved_profiles.get(ssid)
            in_use = (ssid == self._mock_active_ssid)
            networks.append(
                WiFiNetwork(
                    ssid=ssid,
                    signal_pct=entry["signal_pct"],
                    security=entry["security"],
                    in_use=in_use,
                    frequency=entry.get("frequency", "2.4GHz"),
                    channel=entry.get("channel", 1),
                    is_saved=bool(saved or in_use),
                    saved_profile_id=saved.get("profile_id") if saved else None,
                    saved_profile_name=saved.get("name") if saved else None,
                )
            )
        return self._sort_networks(networks)
