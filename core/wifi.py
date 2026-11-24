"""
REI - Wi-Fi Manager and Scanner
Handles network interface discovery, wireless access point scanning,
and connection management using nmcli, iwlist, and wpa_supplicant.
Provides full simulation support for safe development and testing.
"""

from dataclasses import dataclass, field
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from typing import Dict, List, Optional, Tuple, Any

from .wifi_errors import (
    ERROR_CATALOG,
    WiFiErrorCode,
    classify_error,
)

logger = logging.getLogger("REI.Core.WiFi")

# Prefix for connection profiles owned by REI. REI never modifies profiles it
# does not own (for example netplan-generated ones): it only activates them
# read-only with their stored secret.
REI_PROFILE_PREFIX = "rei-"


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
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ip_bytes = fcntl.ioctl(
            s.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack("256s", interface[:15].encode("utf-8"))
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
            stdin=subprocess.DEVNULL,
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
class ActiveConnection:
    """Currently associated Wi-Fi connection on the managed interface."""

    ssid: str
    profile_name: str = ""
    profile_uuid: str = ""
    ip: Optional[str] = None


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

    @property
    def is_secured(self) -> bool:
        """Returns True if network requires a password."""
        sec = self.security.upper()
        return not (sec in ("", "--", "OPEN", "NONE") or "OPEN" in sec)

    @property
    def signal_dbm(self) -> int:
        """Approximate dBm from percentage."""
        return int((self.signal_pct / 2.0) - 100)


class WiFiManager:
    """
    Manages scanning and connecting to Wi-Fi networks on Linux/Raspberry Pi.
    Gracefully falls back across:
    1. nmcli (NetworkManager)
    2. iwlist / iw scan
    3. wpa_cli / wpa_supplicant
    4. Calibrated simulated access points (Dry-Run / Mock).
    """

    def __init__(self, interface: str = "wlan0"):
        self.interface = interface

    @staticmethod
    def validate_password(password: Optional[str], security: str = "") -> Tuple[bool, str]:
        """
        Validates a Wi-Fi password against the security type advertised by the AP.
        Returns (is_valid, message).
        """
        if not password:
            return False, "Ingrese la clave"
        if len(password) > 63:
            return False, "Maximo 63 caracteres"

        sec = (security or "").upper()
        if "WPA" in sec and len(password) < 8:
            return False, "Minimo 8 caracteres"
        return True, ""

    @staticmethod
    def _run_cmd(
        cmd: List[str],
        timeout: int = 5,
    ) -> subprocess.CompletedProcess:
        """
        Runs a network command without ever inheriting an interactive stdin.

        Forces a C locale so NetworkManager messages stay ASCII and stable for
        the error classifier regardless of the system language.
        """
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=env,
        )

    def scan_networks(self) -> List[WiFiNetwork]:
        """
        Scans for available wireless networks and returns a deduplicated list
        sorted by signal strength.
        """
        # Safe mock / Dry-run
        if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_WIFI") == "1":
            return self._get_simulated_networks()

        # 1. Try NetworkManager CLI (nmcli)
        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            try:
                res = self._run_cmd(
                    [nmcli_bin, "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,BSSID,CHAN",
                     "dev", "wifi", "list", "--rescan", "yes"],
                    timeout=8,
                )
                if res.returncode == 0 and res.stdout.strip():
                    networks = self._parse_nmcli_output(res.stdout)
                    if networks:
                        return networks
            except Exception as ex:
                logger.debug(f"nmcli scan failed: {ex}")

        # 2. Try iwlist scan
        iwlist_bin = shutil.which("iwlist")
        if iwlist_bin:
            try:
                cmd = [iwlist_bin, self.interface, "scan"]
                if os.geteuid() != 0:
                    sudo_bin = shutil.which("sudo")
                    if sudo_bin:
                        cmd.insert(0, sudo_bin)
                res = self._run_cmd(cmd, timeout=8)
                if res.returncode == 0 and res.stdout.strip():
                    networks = self._parse_iwlist_output(res.stdout)
                    if networks:
                        return networks
            except Exception as ex:
                logger.debug(f"iwlist scan failed: {ex}")

        # In a real system, never invent access points: an empty list is the
        # honest answer when no backend could produce results.
        logger.warning("Wi-Fi scan produced no results (no backend available or no APs).")
        return []

    def connect_network(
        self,
        ssid: str,
        password: Optional[str] = None,
        timeout: int = 18
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Connects to a Wi-Fi network using an explicit profile state machine:

        1. Already associated with that SSID -> success without touching
           NetworkManager (a password prompt for the active network would only
           trigger a profile update that can fail with unrelated errors).
        2. Saved profile and no typed password -> ``nmcli connection up`` with
           the stored secret.
        3. Typed password -> upsert a REI-owned profile (``rei-<ssid>``) and
           activate it. Foreign profiles (netplan, user-made) are never
           modified, only activated read-only.
        4. Every failure is classified through ``core.wifi_errors`` so the UI
           never shows raw stderr.

        Returns: (success: bool, message: str, details_dict: dict)
        """
        ssid = ssid.strip()
        if not ssid:
            return False, "SSID no valido", {
                "ssid": ssid,
                "error_code": WiFiErrorCode.UNKNOWN.value,
            }

        # Safe mock / Dry-run
        if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_WIFI") == "1":
            return self._connect_simulated(ssid, password)

        # 1. Connect via nmcli
        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            return self._connect_nmcli(nmcli_bin, ssid, password, timeout)

        # 2. Connect via wpa_cli (fallback)
        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            return self._connect_wpa_cli(wpa_cli_bin, ssid, password, timeout)

        return False, ERROR_CATALOG[WiFiErrorCode.NO_BACKEND].message, {
            "ssid": ssid,
            "error_code": WiFiErrorCode.NO_BACKEND.value,
        }

    # ------------------------------------------------------------------
    # Connection state machine
    # ------------------------------------------------------------------

    def _connect_simulated(self, ssid: str, password: Optional[str]) -> Tuple[bool, str, Dict[str, Any]]:
        """Deterministic dry-run version of the connection state machine."""
        time.sleep(0.8)
        simulated = {net.ssid: net for net in self._get_simulated_networks()}
        base = {"ssid": ssid, "interface": self.interface}

        if password and password.lower() in ("wrong", "error", "invalid"):
            info = ERROR_CATALOG[WiFiErrorCode.AUTH_FAILED]
            return False, info.message, {
                **base,
                "reason": "Fallo de autenticación WPA",
                "error_code": info.code.value,
                "needs_password": info.needs_password,
                "retryable": info.retryable,
                "raw_error": "4-way handshake failed (simulated)",
            }

        active = self.get_active_connection()
        if active and active.ssid == ssid and not password:
            return True, f"Ya conectado a {ssid}", {
                **base,
                "ip_address": active.ip,
                "already_connected": True,
                "error_code": WiFiErrorCode.ALREADY_CONNECTED.value,
            }

        saved = self.get_saved_networks()
        if not password:
            if ssid in saved:
                if ssid == "IT_Dep_Secure":
                    # Simulates a saved profile whose secret is stale: the AP
                    # changed the password since the last connection.
                    info = ERROR_CATALOG[WiFiErrorCode.SAVED_SECRET_INVALID]
                    return False, info.message, {
                        **base,
                        "error_code": info.code.value,
                        "needs_password": info.needs_password,
                        "retryable": info.retryable,
                        "saved_profile": True,
                        "raw_error": "no secrets: No agents were available (simulated)",
                    }
                return True, f"Conectado a {ssid}", {
                    **base,
                    "ip_address": "192.168.1.142",
                    "saved_profile": True,
                    "error_code": WiFiErrorCode.NONE.value,
                }
            net = simulated.get(ssid)
            if net is None or net.is_secured:
                info = ERROR_CATALOG[WiFiErrorCode.PASSWORD_REQUIRED]
                return False, info.message, {
                    **base,
                    "error_code": info.code.value,
                    "needs_password": True,
                    "retryable": True,
                }
            return True, f"Conectado a {ssid}", {
                **base,
                "ip_address": "192.168.1.142",
                "error_code": WiFiErrorCode.NONE.value,
            }

        return True, f"Conectado a {ssid}", {
            **base,
            "ip_address": "192.168.1.142",
            "gateway": "192.168.1.1",
            "signal": "92%",
            "security": "WPA2-PSK",
            "error_code": WiFiErrorCode.NONE.value,
        }

    def get_active_connection(self) -> Optional[ActiveConnection]:
        """
        Returns the Wi-Fi network currently associated on the interface, or
        None when disconnected / no backend available.
        """
        if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_WIFI") == "1":
            return ActiveConnection(
                ssid="REI_Lab_AP",
                profile_name="REI_Lab_AP",
                profile_uuid="sim-1",
                ip="192.168.1.142",
            )

        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            try:
                res = self._run_cmd(
                    [nmcli_bin, "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION",
                     "dev", "show", self.interface],
                    timeout=5,
                )
                if res.returncode != 0:
                    return None
                state = ""
                profile = ""
                for line in res.stdout.splitlines():
                    parts = self._split_nmcli_line(line)
                    if len(parts) < 2:
                        continue
                    if parts[0].strip() == "GENERAL.STATE":
                        state = parts[1].strip()
                    elif parts[0].strip() == "GENERAL.CONNECTION":
                        profile = parts[1].strip()
                if not profile or not state.startswith("100"):
                    return None
                ssid = self._query_profile_ssid(nmcli_bin, profile)
                if not ssid:
                    return None
                return ActiveConnection(
                    ssid=ssid,
                    profile_name=profile,
                    ip=self._get_interface_ip(),
                )
            except Exception as ex:
                logger.debug(f"nmcli active connection lookup failed: {ex}")
                return None

        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            return self._wpa_active_connection(wpa_cli_bin)
        return None

    def _wpa_active_connection(self, wpa_cli_bin: str) -> Optional[ActiveConnection]:
        """Reads the active SSID from ``wpa_cli status`` (no nmcli backend)."""
        try:
            res = self._run_cmd(
                [wpa_cli_bin, "-i", self.interface, "status"], timeout=5
            )
            fields: Dict[str, str] = {}
            for line in (res.stdout or "").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields[key.strip()] = value.strip()
            ssid = fields.get("ssid", "")
            if not ssid or fields.get("wpa_state") != "COMPLETED":
                return None
            return ActiveConnection(
                ssid=ssid,
                profile_name=ssid,
                ip=fields.get("ip_address") or self._get_interface_ip(),
            )
        except Exception as ex:
            logger.debug(f"wpa_cli status failed: {ex}")
            return None

    def get_saved_networks(self) -> Dict[str, str]:
        """
        Returns the Wi-Fi networks NetworkManager has stored profiles for,
        mapped ``ssid -> profile uuid``. Falls back to ``wpa_cli`` when nmcli
        is not available.
        """
        if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_WIFI") == "1":
            return {
                "Corp_Net_5G": "sim-0",
                "REI_Lab_AP": "sim-1",
                "IT_Dep_Secure": "sim-2",
            }

        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            saved: Dict[str, str] = {}
            try:
                res = self._run_cmd(
                    [nmcli_bin, "-t", "-f", "NAME,UUID,TYPE", "connection", "show"],
                    timeout=8,
                )
                for line in res.stdout.splitlines():
                    parts = self._split_nmcli_line(line)
                    if len(parts) < 3:
                        continue
                    if parts[2].strip() != "802-11-wireless":
                        continue
                    uuid = parts[1].strip()
                    if not uuid:
                        continue
                    ssid = self._query_profile_ssid(nmcli_bin, uuid)
                    if ssid and ssid not in saved:
                        saved[ssid] = uuid
                    if len(saved) >= 64:
                        break
                return saved
            except Exception as ex:
                logger.debug(f"nmcli saved network lookup failed: {ex}")
                return {}

        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            return self._wpa_saved_networks(wpa_cli_bin)
        return {}

    def _wpa_saved_networks(self, wpa_cli_bin: str) -> Dict[str, str]:
        """Lists saved networks from wpa_supplicant (``ssid -> network id``)."""
        saved: Dict[str, str] = {}
        try:
            res = self._run_cmd(
                [wpa_cli_bin, "-i", self.interface, "list_networks"], timeout=5
            )
            for line in (res.stdout or "").splitlines()[1:]:
                fields = line.split("\t")
                if len(fields) >= 2:
                    network_id, ssid = fields[0].strip(), fields[1].strip()
                    if ssid and ssid not in saved:
                        saved[ssid] = network_id
        except Exception as ex:
            logger.debug(f"wpa_cli list_networks failed: {ex}")
        return saved

    def _query_profile_ssid(self, nmcli_bin: str, profile: str) -> str:
        """Reads the configured SSID of a connection profile (name or uuid)."""
        try:
            res = self._run_cmd(
                [nmcli_bin, "-t", "-f", "802-11-wireless.ssid",
                 "connection", "show", profile],
                timeout=5,
            )
            for line in res.stdout.splitlines():
                parts = self._split_nmcli_line(line)
                if len(parts) >= 2 and parts[0].strip() == "802-11-wireless.ssid":
                    return ":".join(parts[1:]).strip()
        except Exception as ex:
            logger.debug(f"Profile SSID query failed for '{profile}': {ex}")
        return ""

    def _wait_for_ip(self, timeout: int = 8) -> Optional[str]:
        """Polls the interface until DHCP assigns an IPv4 address."""
        deadline = time.monotonic() + max(1, timeout)
        while time.monotonic() < deadline:
            ip = self._get_interface_ip()
            if ip:
                return ip
            time.sleep(0.5)
        return None

    def _connect_nmcli(
        self,
        nmcli_bin: str,
        ssid: str,
        password: Optional[str],
        timeout: int,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """nmcli branch of the connection state machine."""
        active = self.get_active_connection()
        if active and active.ssid == ssid and not password:
            return True, f"Ya conectado a {ssid}", {
                "ssid": ssid,
                "ip_address": active.ip or self._get_interface_ip(),
                "interface": self.interface,
                "already_connected": True,
                "profile": active.profile_name,
                "error_code": WiFiErrorCode.ALREADY_CONNECTED.value,
            }

        # Saved profile without a typed password: reactivate with its stored
        # secret. This also covers netplan-generated profiles (read-only use).
        if not password:
            saved = self.get_saved_networks()
            profile_uuid = saved.get(ssid, "")
            if profile_uuid:
                return self._activate_profile(
                    nmcli_bin, profile_uuid, ssid, timeout, saved_profile=True
                )
            return self._upsert_rei_profile(nmcli_bin, ssid, None, timeout)

        return self._upsert_rei_profile(nmcli_bin, ssid, password, timeout)

    def _activate_profile(
        self,
        nmcli_bin: str,
        profile_uuid: str,
        ssid: str,
        timeout: int,
        saved_profile: bool = True,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Activates an existing connection profile with its stored secret."""
        base = {"ssid": ssid, "interface": self.interface, "saved_profile": saved_profile}
        try:
            res = self._run_cmd(
                self._privileged(
                    [nmcli_bin, "connection", "up", profile_uuid,
                     "ifname", self.interface]
                ),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            info = ERROR_CATALOG[WiFiErrorCode.TIMEOUT]
            return False, info.message, {
                **base,
                "error_code": info.code.value,
                "retryable": True,
                "raw_error": "connection activation timed out",
            }
        except Exception as ex:
            safe_ex = self._redact(str(ex), None)
            return False, ERROR_CATALOG[WiFiErrorCode.UNKNOWN].message, {
                **base,
                "error_code": WiFiErrorCode.UNKNOWN.value,
                "raw_error": safe_ex[:160],
            }

        if res.returncode == 0:
            ok, message, details = self._success_result(ssid, profile_uuid)
            details["saved_profile"] = saved_profile
            return ok, message, details

        raw = self._redact(res.stderr or res.stdout or "", None).strip()
        info = classify_error(raw)
        if info.code == WiFiErrorCode.ALREADY_CONNECTED:
            return self._success_result(ssid, profile_uuid)
        return False, info.message, {
            **base,
            "error_code": info.code.value,
            "needs_password": info.needs_password,
            "retryable": info.retryable,
            "profile": profile_uuid,
            "raw_error": raw[:160],
        }

    def _upsert_rei_profile(
        self,
        nmcli_bin: str,
        ssid: str,
        password: Optional[str],
        timeout: int,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Creates or updates the REI-owned profile ``rei-<ssid>`` and activates it.

        Existing foreign profiles are left untouched, so a failed attempt never
        overwrites a user password that might still be valid.
        """
        profile_name = f"{REI_PROFILE_PREFIX}{ssid}"
        base = {"ssid": ssid, "interface": self.interface, "profile": profile_name}
        created = False

        try:
            existing_uuid = self._find_rei_profile(nmcli_bin, ssid)
            if existing_uuid:
                target = existing_uuid
                if password:
                    res = self._run_cmd(
                        self._privileged([
                            nmcli_bin, "connection", "modify", target,
                            "802-11-wireless-security.key-mgmt", "wpa-psk",
                            "802-11-wireless-security.psk", password,
                        ]),
                        timeout=10,
                    )
                    if res.returncode != 0:
                        raw = self._redact(res.stderr or res.stdout or "", password).strip()
                        info = classify_error(raw)
                        return False, info.message, {
                            **base,
                            "error_code": info.code.value,
                            "retryable": info.retryable,
                            "raw_error": raw[:160],
                        }
            else:
                con_args = [
                    nmcli_bin, "connection", "add",
                    "type", "wifi",
                    "ifname", self.interface,
                    "con-name", profile_name,
                    "ssid", ssid,
                    "connection.autoconnect", "yes",
                    "connection.autoconnect-priority", "5",
                ]
                if password:
                    con_args.extend([
                        "802-11-wireless-security.key-mgmt", "wpa-psk",
                        "802-11-wireless-security.psk", password,
                    ])
                res = self._run_cmd(self._privileged(con_args), timeout=10)
                if res.returncode != 0:
                    raw = self._redact(res.stderr or res.stdout or "", password).strip()
                    info = classify_error(raw)
                    return False, info.message, {
                        **base,
                        "error_code": info.code.value,
                        "retryable": info.retryable,
                        "raw_error": raw[:160],
                    }
                target = profile_name
                created = True

            res = self._run_cmd(
                self._privileged(
                    [nmcli_bin, "connection", "up", target, "ifname", self.interface]
                ),
                timeout=timeout,
            )
            if res.returncode == 0:
                return self._success_result(ssid, target)

            if created:
                # Never leave a profile with a wrong password behind.
                self._run_cmd(
                    self._privileged(
                        [nmcli_bin, "connection", "delete", profile_name]
                    ),
                    timeout=10,
                )
            raw = self._redact(res.stderr or res.stdout or "", password).strip()
            info = classify_error(raw)
            if info.code == WiFiErrorCode.ALREADY_CONNECTED:
                return self._success_result(ssid, target)
            if info.needs_password:
                # "no secrets" after an activation attempt means NetworkManager
                # could not authenticate: with a typed password that is a wrong
                # key; without one it means we must prompt for it.
                info = (
                    ERROR_CATALOG[WiFiErrorCode.PASSWORD_REQUIRED]
                    if not password
                    else ERROR_CATALOG[WiFiErrorCode.AUTH_FAILED]
                )
            logger.warning(
                "Wi-Fi connect to '%s' failed (%s): %s", ssid, info.code.value, raw[:160]
            )
            return False, info.message, {
                **base,
                "error_code": info.code.value,
                "needs_password": info.needs_password,
                "retryable": info.retryable,
                "raw_error": raw[:160],
            }
        except subprocess.TimeoutExpired:
            if created:
                self._run_cmd(
                    self._privileged(
                        [nmcli_bin, "connection", "delete", profile_name]
                    ),
                    timeout=10,
                )
            info = ERROR_CATALOG[WiFiErrorCode.TIMEOUT]
            logger.warning("Wi-Fi connect to '%s' timed out", ssid)
            return False, info.message, {
                **base,
                "error_code": info.code.value,
                "retryable": True,
            }
        except Exception as ex:
            safe_ex = self._redact(str(ex), password)
            logger.warning("Wi-Fi connect to '%s' raised: %s", ssid, safe_ex)
            return False, ERROR_CATALOG[WiFiErrorCode.UNKNOWN].message, {
                **base,
                "error_code": WiFiErrorCode.UNKNOWN.value,
                "raw_error": safe_ex[:160],
            }

    def _find_rei_profile(self, nmcli_bin: str, ssid: str) -> Optional[str]:
        """Returns the uuid of the REI-owned profile for a SSID, if any."""
        profile_name = f"{REI_PROFILE_PREFIX}{ssid}"
        try:
            res = self._run_cmd(
                [nmcli_bin, "-t", "-f", "NAME,UUID", "connection", "show"],
                timeout=8,
            )
            for line in res.stdout.splitlines():
                parts = self._split_nmcli_line(line)
                if len(parts) >= 2 and parts[0].strip() == profile_name:
                    return parts[1].strip()
        except Exception as ex:
            logger.debug(f"REI profile lookup failed: {ex}")
        return None

    def _success_result(self, ssid: str, profile: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Builds a success payload after verifying DHCP actually delivered."""
        ip_addr = self._wait_for_ip()
        return True, f"Conectado a {ssid}", {
            "ssid": ssid,
            "ip_address": ip_addr or "Sin IP (DHCP pendiente)",
            "interface": self.interface,
            "profile": profile,
            "error_code": WiFiErrorCode.NO_IP.value if not ip_addr else WiFiErrorCode.NONE.value,
        }

    def _privileged(self, cmd: List[str]) -> List[str]:
        """Wraps state-changing commands with sudo when not running as root."""
        if os.geteuid() != 0:
            sudo_bin = shutil.which("sudo")
            if sudo_bin:
                return [sudo_bin] + cmd
        return cmd

    def _connect_wpa_cli(
        self,
        wpa_cli_bin: str,
        ssid: str,
        password: Optional[str],
        timeout: int,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """wpa_supplicant fallback of the connection state machine."""
        base = {"ssid": ssid, "interface": self.interface}
        try:
            # Add network and configure
            res = self._run_cmd(
                [wpa_cli_bin, "-i", self.interface, "add_network"], timeout=5
            )
            net_id = res.stdout.strip()
            if not net_id.isdigit():
                return False, ERROR_CATALOG[WiFiErrorCode.NO_BACKEND].message, {
                    **base,
                    "error_code": WiFiErrorCode.NO_BACKEND.value,
                }

            escaped_ssid = self._escape_wpa_str(ssid)
            self._run_cmd(
                [wpa_cli_bin, "-i", self.interface, "set_network", net_id, "ssid", escaped_ssid],
                timeout=5,
            )
            if password:
                escaped_psk = self._escape_wpa_str(password)
                self._run_cmd(
                    [wpa_cli_bin, "-i", self.interface, "set_network", net_id, "psk", escaped_psk],
                    timeout=5,
                )
            else:
                self._run_cmd(
                    [wpa_cli_bin, "-i", self.interface, "set_network", net_id, "key_mgmt", "NONE"],
                    timeout=5,
                )
            self._run_cmd(
                [wpa_cli_bin, "-i", self.interface, "enable_network", net_id], timeout=5
            )
            self._run_cmd(
                [wpa_cli_bin, "-i", self.interface, "select_network", net_id], timeout=5
            )

            # Verify the association instead of blindly reporting success.
            if self._wait_for_wpa_connection(wpa_cli_bin, timeout=timeout):
                ip_addr = self._wait_for_ip()
                return True, f"Conectado a {ssid}", {
                    **base,
                    "ip_address": ip_addr or "DHCP pendiente",
                    "error_code": WiFiErrorCode.NO_IP.value if not ip_addr else WiFiErrorCode.NONE.value,
                }
            info = classify_error("4-way handshake failed")
            return False, info.message, {
                **base,
                "error_code": info.code.value,
                "needs_password": info.needs_password,
                "retryable": True,
                "reason": "Fallo de autenticación o red fuera de alcance",
            }
        except subprocess.TimeoutExpired:
            info = ERROR_CATALOG[WiFiErrorCode.TIMEOUT]
            return False, info.message, {
                **base,
                "error_code": info.code.value,
                "retryable": True,
            }
        except Exception as ex:
            safe_ex = self._redact(str(ex), password)
            return False, ERROR_CATALOG[WiFiErrorCode.UNKNOWN].message, {
                **base,
                "error_code": WiFiErrorCode.UNKNOWN.value,
                "raw_error": safe_ex[:160],
            }

    def _wait_for_wpa_connection(self, wpa_cli_bin: str, timeout: int = 18) -> bool:
        """
        Polls ``wpa_cli status`` until the interface completes association.

        Returns False on timeout, authentication failure or on any error.
        """
        deadline = time.monotonic() + max(1, timeout)
        while time.monotonic() < deadline:
            try:
                res = self._run_cmd(
                    [wpa_cli_bin, "-i", self.interface, "status"], timeout=5
                )
                output = res.stdout or ""
                if "wpa_state=COMPLETED" in output:
                    return True
                if "wpa_state=4WAY_HANDSHAKE" in output:
                    pass
                elif "wpa_state=SCANNING" in output or "wpa_state=ASSOCIATING" in output:
                    pass
                elif "wpa_state=DISCONNECTED" in output and "reason=" in output:
                    # Keep waiting: the supplicant may retry the association.
                    pass
            except subprocess.TimeoutExpired:
                return False
            except Exception:
                return False
            time.sleep(0.5)
        return False

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

    @staticmethod
    def _redact(text: str, secret: Optional[str]) -> str:
        """Removes a secret (Wi-Fi password) from any diagnostic message."""
        if not text:
            return ""
        if secret:
            return text.replace(secret, "***")
        return text

    @staticmethod
    def _split_nmcli_line(line: str) -> List[str]:
        """
        Splits a terse ``nmcli -t`` line honoring its escaping rules:
        ``\\:`` is a literal colon and ``\\\\`` a literal backslash.
        """
        parts: List[str] = []
        current: List[str] = []
        escaped = False
        for ch in line:
            if escaped:
                current.append(ch)
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == ":":
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)
        if escaped:
            current.append("\\")
        parts.append("".join(current))
        return parts

    def _parse_nmcli_output(self, output: str) -> List[WiFiNetwork]:
        """Parses colon-delimited tabular output from nmcli."""
        networks: Dict[str, WiFiNetwork] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = self._split_nmcli_line(line)
            if len(parts) < 4:
                continue

            in_use = parts[0].strip() == "*"
            ssid = parts[1].strip()
            if not ssid or ssid == "--":
                continue
            try:
                signal = int(parts[2].strip())
            except ValueError:
                signal = 50
            security = parts[3].strip() or "Open"
            bssid = parts[4].strip() if len(parts) > 4 else ""
            try:
                channel = int(parts[5].strip()) if len(parts) > 5 else 1
            except ValueError:
                channel = 1

            net = WiFiNetwork(
                ssid=ssid,
                signal_pct=signal,
                security=security,
                in_use=in_use,
                bssid=bssid,
                channel=channel,
            )
            existing = networks.get(ssid)
            if existing is None:
                networks[ssid] = net
            elif in_use and not existing.in_use:
                # The active BSSID must survive deduplication even when it is
                # not the strongest one, otherwise the UI loses the "already
                # connected" state for this SSID.
                net.signal_pct = max(net.signal_pct, existing.signal_pct)
                net.channel = net.channel or existing.channel
                networks[ssid] = net
            elif existing.in_use:
                existing.signal_pct = max(existing.signal_pct, signal)
            elif signal > existing.signal_pct:
                networks[ssid] = net

        return sorted(networks.values(), key=lambda n: n.signal_pct, reverse=True)

    def _parse_iwlist_output(self, output: str) -> List[WiFiNetwork]:
        """Parses output from iwlist wlan0 scan, grouping fields per cell."""
        networks: Dict[str, WiFiNetwork] = {}
        current_ssid: Optional[str] = None
        current_signal = 50
        current_security = "Open"

        def flush() -> None:
            nonlocal current_ssid, current_signal, current_security
            if current_ssid:
                net = WiFiNetwork(
                    ssid=current_ssid,
                    signal_pct=current_signal,
                    security=current_security,
                )
                if (
                    current_ssid not in networks
                    or current_signal > networks[current_ssid].signal_pct
                ):
                    networks[current_ssid] = net
            current_ssid = None
            current_signal = 50
            current_security = "Open"

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if line.startswith("Cell ") or line.startswith("Cell:"):
                flush()
                continue

            if "ESSID:" in line:
                match = re.search(r'ESSID:"([^"]*)"', line)
                if match:
                    current_ssid = match.group(1).strip()
                continue

            if "Quality=" in line:
                match = re.search(r'Quality=(\d+)/(\d+)', line)
                if match:
                    total = float(match.group(2))
                    if total > 0:
                        current_signal = int((int(match.group(1)) / total) * 100)
                continue

            if "Signal level=" in line:
                match = re.search(r"Signal level=(-?\d+)", line)
                if match:
                    dbm = int(match.group(1))
                    current_signal = max(0, min(100, 2 * (dbm + 100)))
                continue

            if "Encryption key:on" in line:
                current_security = "WPA2"
            elif "Encryption key:off" in line:
                current_security = "Open"

        flush()
        return sorted(networks.values(), key=lambda n: n.signal_pct, reverse=True)

    def _get_simulated_networks(self) -> List[WiFiNetwork]:
        """Returns realistic simulated Wi-Fi networks for testing."""
        return [
            WiFiNetwork(ssid="Corp_Net_5G", signal_pct=92, security="WPA2-Enterprise", frequency="5GHz"),
            WiFiNetwork(ssid="REI_Lab_AP", signal_pct=84, security="WPA2-PSK", frequency="2.4GHz"),
            WiFiNetwork(ssid="Guest_WiFi", signal_pct=68, security="Open", frequency="2.4GHz"),
            WiFiNetwork(ssid="Switch_Mgmt", signal_pct=55, security="WPA3-SAE", frequency="5GHz"),
            WiFiNetwork(ssid="IT_Dep_Secure", signal_pct=42, security="WPA2-PSK", frequency="2.4GHz"),
        ]
