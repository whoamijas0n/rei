"""
REI - PiSugar 3 UPS HAT Telemetry Client
Interfaces with the PiSugar 3 Power Management IC via:
1. PiSugar Server Unix Domain Socket / TCP Port 8421
2. Direct I2C Bus 1 (Address 0x57) register reads
3. Linux standard sysfs power_supply fallback
4. Safe simulation mode for testing and non-Pi environments
"""

from dataclasses import dataclass, field
import logging
import os
import socket
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger("REI.Core.PiSugar3")

# PiSugar 3 Hardware Specs & I2C Register Constants
PISUGAR3_I2C_BUS = 1
PISUGAR3_I2C_ADDR = 0x57  # Standard PiSugar 3 I2C address

REG_CHG_STATUS = 0x02    # Charging & power status register (bit 7: power plugged)
REG_BAT_LEVEL = 0x2A     # Battery percentage (0 - 100%)
REG_VOLTAGE_HIGH = 0x22  # Battery voltage high byte (mV)
REG_VOLTAGE_LOW = 0x23   # Battery voltage low byte (mV)
REG_CURRENT_HIGH = 0x24  # Battery current high byte (mA, signed)
REG_CURRENT_LOW = 0x25   # Battery current low byte (mA, signed)
REG_TEMP = 0x05          # Temperature register


@dataclass
class PiSugarTelemetry:
    """Encapsulates structured battery and power telemetry from PiSugar 3."""
    percentage: int = 0
    voltage: float = 0.0
    status: str = "DESCONOCIDO"
    current_ma: int = 0
    power_w: float = 0.0
    temperature_c: Optional[float] = None
    is_charging: bool = False
    is_power_plugged: bool = False
    source: str = "simulated"
    details: List[str] = field(default_factory=list)


class PiSugar3Client:
    """
    Robust telemetry client for PiSugar 3 UPS HAT.
    Employs layered fallback mechanism:
    PiSugar Daemon Socket -> Direct I2C Bus -> Sysfs Power Supply -> Calibrated Simulation.
    """

    def __init__(self, i2c_bus: int = PISUGAR3_I2C_BUS, i2c_addr: int = PISUGAR3_I2C_ADDR):
        self.i2c_bus = i2c_bus
        self.i2c_addr = i2c_addr

    def get_telemetry(self) -> PiSugarTelemetry:
        """
        Retrieves real-time power telemetry from the best available source.
        Guaranteed not to raise unhandled exceptions.
        """
        # 1. Try PiSugar Server Socket / TCP Daemon
        socket_telemetry = self._read_from_pisugar_server()
        if socket_telemetry:
            return self._build_telemetry(socket_telemetry, source="pisugar-server")

        # 2. Try Direct I2C Hardware Registers
        i2c_telemetry = self._read_from_i2c_registers()
        if i2c_telemetry:
            return self._build_telemetry(i2c_telemetry, source="i2c-direct")

        # 3. Try Linux Sysfs Power Supply
        sysfs_telemetry = self._read_from_sysfs()
        if sysfs_telemetry:
            return self._build_telemetry(sysfs_telemetry, source="sysfs")

        # 4. Fallback: Safe Mock / Simulation for Development Host
        return self._get_simulated_telemetry()

    def _read_from_pisugar_server(self) -> Optional[Dict[str, Any]]:
        """Queries the pisugar-server daemon via Unix domain socket or local TCP."""
        socket_paths = [
            "/tmp/pisugar-server.sock",
            "/run/pisugar-server.sock",
            "/var/run/pisugar-server.sock",
        ]

        # Try Unix Domain Sockets first
        for path in socket_paths:
            if os.path.exists(path):
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(0.4)
                        s.connect(path)
                        data = self._query_socket_commands(s)
                        if data:
                            return data
                except Exception as ex:
                    logger.debug(f"PiSugar UNIX socket query failed on {path}: {ex}")

        # Try Local TCP Port 8421
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.4)
                s.connect(("127.0.0.1", 8421))
                data = self._query_socket_commands(s)
                if data:
                    return data
        except Exception as ex:
            logger.debug(f"PiSugar TCP query failed on 127.0.0.1:8421: {ex}")

        return None

    def _query_socket_commands(self, s: socket.socket) -> Optional[Dict[str, Any]]:
        """Helper to send queries to PiSugar server."""
        commands = {
            "battery": "get battery\n",
            "battery_v": "get battery_v\n",
            "battery_charging": "get battery_charging\n",
            "battery_power": "get battery_power_plugged\n",
            "battery_i": "get battery_i\n",
            "temperature": "get temperature\n",
        }
        res: Dict[str, Any] = {}
        for key, cmd in commands.items():
            try:
                s.sendall(cmd.encode("utf-8"))
                reply = s.recv(128).decode("utf-8").strip()
                # Server response format: "battery: 89.5" or "battery_v: 4.12"
                if ":" in reply:
                    val_str = reply.split(":", 1)[1].strip()
                    if key in ("battery", "battery_v", "battery_i", "temperature"):
                        try:
                            res[key] = float(val_str)
                        except ValueError:
                            res[key] = val_str
                    elif key in ("battery_charging", "battery_power"):
                        res[key] = val_str.lower() in ("true", "1", "yes")
            except Exception:
                continue

        if "battery" in res or "battery_v" in res:
            return res
        return None

    def _read_from_i2c_registers(self) -> Optional[Dict[str, Any]]:
        """Reads hardware registers directly from the PiSugar 3 micro-controller over I2C."""
        bus = None
        try:
            # Try smbus2 or smbus
            try:
                import smbus2 as smbus_module
            except ImportError:
                import smbus as smbus_module

            bus = smbus_module.SMBus(self.i2c_bus)

            # Test connection by reading battery level
            level = bus.read_byte_data(self.i2c_addr, REG_BAT_LEVEL)
            if level > 100:
                level = 100

            # Read Voltage (High + Low byte in mV)
            v_high = bus.read_byte_data(self.i2c_addr, REG_VOLTAGE_HIGH)
            v_low = bus.read_byte_data(self.i2c_addr, REG_VOLTAGE_LOW)
            voltage_mv = (v_high << 8) | v_low
            voltage_v = voltage_mv / 1000.0 if voltage_mv > 0 else 3.7

            # Read Charging Status Register
            status_reg = bus.read_byte_data(self.i2c_addr, REG_CHG_STATUS)
            is_power_plugged = bool(status_reg & 0x80)
            is_charging = bool(status_reg & 0x40) or (is_power_plugged and level < 98)

            # Read Current (High + Low byte in mA, signed 16-bit)
            c_high = bus.read_byte_data(self.i2c_addr, REG_CURRENT_HIGH)
            c_low = bus.read_byte_data(self.i2c_addr, REG_CURRENT_LOW)
            raw_current = (c_high << 8) | c_low
            # Convert two's complement 16-bit
            if raw_current >= 32768:
                raw_current -= 65536
            current_ma = raw_current

            # Read Temperature
            temp_c = None
            try:
                raw_temp = bus.read_byte_data(self.i2c_addr, REG_TEMP)
                if raw_temp > 0:
                    temp_c = float(raw_temp)
            except Exception:
                pass

            return {
                "percentage": int(level),
                "voltage": round(voltage_v, 2),
                "is_charging": is_charging,
                "is_power_plugged": is_power_plugged,
                "current_ma": current_ma,
                "temperature": temp_c,
            }

        except Exception as ex:
            logger.debug(f"Direct I2C PiSugar 3 read failed: {ex}")
            return None
        finally:
            if bus is not None:
                try:
                    bus.close()
                except Exception:
                    pass

    def _read_from_sysfs(self) -> Optional[Dict[str, Any]]:
        """Reads power metrics from standard Linux /sys/class/power_supply/."""
        base_path = "/sys/class/power_supply"
        if not os.path.exists(base_path):
            return None

        try:
            for entry in os.listdir(base_path):
                bat_dir = os.path.join(base_path, entry)
                capacity_file = os.path.join(bat_dir, "capacity")
                if os.path.isfile(capacity_file):
                    with open(capacity_file, "r") as f:
                        level = int(f.read().strip())

                    voltage_v = 3.85
                    voltage_file = os.path.join(bat_dir, "voltage_now")
                    if os.path.isfile(voltage_file):
                        with open(voltage_file, "r") as f:
                            raw_v = float(f.read().strip())
                            voltage_v = (raw_v / 1_000_000.0) if raw_v > 1000 else (raw_v / 1000.0)

                    status = "Discharging"
                    status_file = os.path.join(bat_dir, "status")
                    if os.path.isfile(status_file):
                        with open(status_file, "r") as f:
                            status = f.read().strip()

                    current_ma = -280
                    current_file = os.path.join(bat_dir, "current_now")
                    if os.path.isfile(current_file):
                        with open(current_file, "r") as f:
                            raw_c = float(f.read().strip())
                            current_ma = int(raw_c / 1000.0) if abs(raw_c) > 10000 else int(raw_c)

                    return {
                        "percentage": level,
                        "voltage": round(voltage_v, 2),
                        "is_charging": status.lower() == "charging",
                        "is_power_plugged": status.lower() in ("charging", "full"),
                        "current_ma": current_ma,
                    }
        except Exception as ex:
            logger.debug(f"Sysfs power read failed: {ex}")

        return None

    def _get_simulated_telemetry(self) -> PiSugarTelemetry:
        """Generates realistic simulation data for non-embedded environments."""
        level = 88
        voltage = 4.12
        current_ma = -260  # Discharging at ~260mA
        power_w = round(abs(voltage * (current_ma / 1000.0)), 2)
        state_str = "DESCARGA"

        details = [
            f"Nivel:   {level}%",
            f"Voltaje: {voltage:.2f}V",
            f"Estado:  {state_str}",
            f"Consumo: {abs(current_ma)}mA (~{power_w}W)",
        ]

        return PiSugarTelemetry(
            percentage=level,
            voltage=voltage,
            status=state_str,
            current_ma=current_ma,
            power_w=power_w,
            temperature_c=34.5,
            is_charging=False,
            is_power_plugged=False,
            source="simulated",
            details=details,
        )

    def _build_telemetry(self, raw: Dict[str, Any], source: str) -> PiSugarTelemetry:
        """Standardizes raw dictionary fields into a PiSugarTelemetry object."""
        level = int(raw.get("percentage") or raw.get("battery") or 85)
        level = max(0, min(100, level))

        voltage = float(raw.get("voltage") or raw.get("battery_v") or 4.10)
        if voltage > 100:  # If in mV
            voltage = voltage / 1000.0
        voltage = round(voltage, 2)

        is_charging = bool(raw.get("is_charging") or raw.get("battery_charging"))
        is_plugged = bool(raw.get("is_power_plugged") or raw.get("battery_power") or is_charging)

        current_ma = int(raw.get("current_ma") or raw.get("battery_i") or (-280 if not is_charging else 450))

        if is_charging:
            state_str = "CARGANDO"
        elif is_plugged and level >= 98:
            state_str = "COMPLETO"
        elif is_plugged:
            state_str = "EXTERNO"
        else:
            state_str = "DESCARGA"

        power_w = round(abs(voltage * (current_ma / 1000.0)), 2)
        temp_c = raw.get("temperature")

        consumption_label = f"+{current_ma}mA" if current_ma > 0 else f"{abs(current_ma)}mA"
        details = [
            f"Nivel:   {level}%",
            f"Voltaje: {voltage:.2f}V",
            f"Estado:  {state_str}",
            f"Consumo: {consumption_label} (~{power_w}W)",
        ]

        return PiSugarTelemetry(
            percentage=level,
            voltage=voltage,
            status=state_str,
            current_ma=current_ma,
            power_w=power_w,
            temperature_c=temp_c,
            is_charging=is_charging,
            is_power_plugged=is_plugged,
            source=source,
            details=details,
        )
