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

logger = logging.getLogger("REI.Core.WiFi")


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
                res = subprocess.run(
                    [nmcli_bin, "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,BSSID,CHAN", "dev", "wifi", "list", "--rescan", "yes"],
                    capture_output=True,
                    text=True,
                    timeout=8
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
                    cmd.insert(0, "sudo")
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if res.returncode == 0 and res.stdout.strip():
                    networks = self._parse_iwlist_output(res.stdout)
                    if networks:
                        return networks
            except Exception as ex:
                logger.debug(f"iwlist scan failed: {ex}")

        # Fallback to simulated networks if no physical Wi-Fi hardware detected
        return self._get_simulated_networks()

    def connect_network(
        self,
        ssid: str,
        password: Optional[str] = None,
        timeout: int = 18
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Attempts to connect to the specified Wi-Fi network.
        Returns: (success: bool, message: str, details_dict: dict)
        """
        ssid = ssid.strip()
        if not ssid:
            return False, "SSID no valido", {}

        # Safe mock / Dry-run
        if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_WIFI") == "1":
            time.sleep(0.8)
            # Simulate failure if password is 'wrong' or 'error'
            if password and password.lower() in ("wrong", "error", "invalid"):
                return False, "Contraseña incorrecta", {
                    "ssid": ssid,
                    "reason": "Fallo de autenticación WPA",
                    "error_code": "AUTH_FAILED"
                }
            return True, f"Conectado a {ssid}", {
                "ssid": ssid,
                "ip_address": "192.168.1.142",
                "gateway": "192.168.1.1",
                "signal": "92%",
                "security": "WPA2-PSK",
                "interface": self.interface
            }

        # 1. Connect via nmcli
        nmcli_bin = shutil.which("nmcli")
        if nmcli_bin:
            try:
                cmd = [nmcli_bin, "device", "wifi", "connect", ssid]
                if password:
                    cmd.extend(["password", password])
                cmd.extend(["ifname", self.interface])

                res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                if res.returncode == 0:
                    ip_addr = self._get_interface_ip()
                    return True, f"Conectado a {ssid}", {
                        "ssid": ssid,
                        "ip_address": ip_addr or "Asignada (DHCP)",
                        "interface": self.interface
                    }
                else:
                    err = (res.stderr or res.stdout or "Error de conexión").strip()
                    # Clean message for OLED display
                    clean_err = self._sanitize_error(err)
                    return False, clean_err, {"ssid": ssid, "raw_error": err}
            except subprocess.TimeoutExpired:
                return False, "Tiempo de espera agotado", {"ssid": ssid, "error": "TIMEOUT"}
            except Exception as ex:
                return False, f"Error: {str(ex)[:18]}", {"ssid": ssid, "error": str(ex)}

        # 2. Connect via wpa_cli (fallback)
        wpa_cli_bin = shutil.which("wpa_cli")
        if wpa_cli_bin:
            try:
                # Add network and configure
                res = subprocess.run([wpa_cli_bin, "-i", self.interface, "add_network"], capture_output=True, text=True, timeout=5)
                net_id = res.stdout.strip()
                if net_id.isdigit():
                    escaped_ssid = self._escape_wpa_str(ssid)
                    subprocess.run([wpa_cli_bin, "-i", self.interface, "set_network", net_id, "ssid", escaped_ssid], timeout=5)
                    if password:
                        escaped_psk = self._escape_wpa_str(password)
                        subprocess.run([wpa_cli_bin, "-i", self.interface, "set_network", net_id, "psk", escaped_psk], timeout=5)
                    else:
                        subprocess.run([wpa_cli_bin, "-i", self.interface, "set_network", net_id, "key_mgmt", "NONE"], timeout=5)
                    subprocess.run([wpa_cli_bin, "-i", self.interface, "enable_network", net_id], timeout=5)
                    subprocess.run([wpa_cli_bin, "-i", self.interface, "select_network", net_id], timeout=5)
                    time.sleep(3)
                    ip_addr = self._get_interface_ip()
                    return True, f"Conectado a {ssid}", {"ssid": ssid, "ip_address": ip_addr or "DHCP OK"}
            except Exception as ex:
                return False, f"Error wpa_cli: {str(ex)[:16]}", {"ssid": ssid, "error": str(ex)}

        return False, "Gestor de red no disponible", {"ssid": ssid}

    @staticmethod
    def _escape_wpa_str(val: str) -> str:
        """Escapes quotes and backslashes for wpa_cli string parameters."""
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _get_interface_ip(self) -> Optional[str]:
        """Reads IPv4 address of interface."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return None

    def _sanitize_error(self, err_msg: str) -> str:
        """Converts long network errors to clear concise strings for OLED display."""
        lower = err_msg.lower()
        if "secret" in lower or "password" in lower or "psk" in lower or "802-11-wireless-security" in lower:
            return "Contraseña incorrecta"
        if "not found" in lower or "no network" in lower or "no match" in lower:
            return "Red no encontrada"
        if "timeout" in lower or "timed out" in lower:
            return "Tiempo agotado"
        if "failed to activate" in lower:
            return "Fallo de autenticación"
        return err_msg[:20]

    def _parse_nmcli_output(self, output: str) -> List[WiFiNetwork]:
        """Parses colon-delimited tabular output from nmcli."""
        networks: Dict[str, WiFiNetwork] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) >= 4:
                in_use = (parts[0].strip() == "*")
                ssid = parts[1].strip()
                if not ssid or ssid == "--":
                    continue
                try:
                    signal = int(parts[2].strip())
                except ValueError:
                    signal = 50
                security = parts[3].strip() or "Open"
                bssid = parts[4].strip() if len(parts) > 4 else ""

                net = WiFiNetwork(
                    ssid=ssid,
                    signal_pct=signal,
                    security=security,
                    in_use=in_use,
                    bssid=bssid
                )
                # Keep highest signal instance of SSID
                if ssid not in networks or signal > networks[ssid].signal_pct:
                    networks[ssid] = net

        return sorted(networks.values(), key=lambda n: n.signal_pct, reverse=True)

    def _parse_iwlist_output(self, output: str) -> List[WiFiNetwork]:
        """Parses output from iwlist wlan0 scan."""
        networks: Dict[str, WiFiNetwork] = {}
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
                match = re.search(r'Quality=(\d+)/(\d+)', line)
                if match:
                    current_signal = int((int(match.group(1)) / float(match.group(2))) * 100)
            elif "Encryption key:on" in line:
                current_security = "WPA2"
            elif "Encryption key:off" in line:
                current_security = "Open"

            if current_ssid:
                net = WiFiNetwork(
                    ssid=current_ssid,
                    signal_pct=current_signal,
                    security=current_security
                )
                if current_ssid not in networks or current_signal > networks[current_ssid].signal_pct:
                    networks[current_ssid] = net
                current_ssid = None

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
