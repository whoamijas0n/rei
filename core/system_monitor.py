import threading
import time
import queue
import psutil
import subprocess
import random
import os

class SystemMonitor:
    """
    Productor de datos del sistema.
    Recoge métricas sin bloquear usando un hilo separado.
    """
    def __init__(self, data_queue: queue.Queue):
        self.data_queue = data_queue
        self.running = False
        self.thread = None
        self.version = "OmniDiag Hub v2.1"
        self._mock_battery = 100.0
        self.is_updating = False

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

    def _monitor_loop(self):
        while self.running:
            data = {
                "battery": self._get_battery(),
                "wifi": self._get_wifi(),
                "health": self._get_health(),
                "version": self.version,
                "is_updating": self.is_updating
            }
            # Put in queue, keep only latest state
            while not self.data_queue.empty():
                try:
                    self.data_queue.get_nowait()
                except queue.Empty:
                    break
            self.data_queue.put(data)
            time.sleep(2)

    def _get_battery(self):
        # Mocking PiSugar 3 I2C battery reading
        self._mock_battery -= random.uniform(0.01, 0.1)
        if self._mock_battery < 0: self._mock_battery = 100.0
        voltage = 3.3 + (self._mock_battery / 100.0) * 0.9 # 3.3V to 4.2V approx
        return {"percent": round(self._mock_battery, 1), "voltage": round(voltage, 2)}

    def _get_wifi(self):
        ssid = "Desconectado"
        ip = "---"
        signal = "---"
        try:
            ssid_out = subprocess.check_output(['iwgetid', '-r'], stderr=subprocess.DEVNULL, text=True).strip()
            if ssid_out:
                ssid = ssid_out
                ip_out = subprocess.check_output(['hostname', '-I'], stderr=subprocess.DEVNULL, text=True).strip()
                if ip_out:
                    ip = ip_out.split()[0]
                
                # Mock signal for this sprint as getting it requires root or complex parsing of iwconfig
                signal_dbm = -random.randint(40, 70) 
                signal = f"{signal_dbm} dBm"
        except Exception:
            pass 

        return {"ssid": ssid, "ip": ip, "signal": signal}

    def _get_health(self):
        # CPU Temp
        temp = "N/A"
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp_raw = int(f.read().strip())
                temp = f"{temp_raw / 1000.0:.1f} C"
        except Exception:
            temp = "45.0 C (Mock)"

        # Uptime
        uptime_seconds = time.time() - psutil.boot_time()
        hours, remainder = divmod(int(uptime_seconds), 3600)
        minutes, _ = divmod(remainder, 60)
        uptime = f"{hours}h {minutes}m"

        # Load
        try:
            load = os.getloadavg()
            load_str = f"{load[0]:.2f}, {load[1]:.2f}, {load[2]:.2f}"
        except AttributeError:
            # Fallback if getloadavg is not available (e.g. some Windows setups for testing)
            load_str = "0.00, 0.00, 0.00"

        return {"temp": temp, "uptime": uptime, "load": load_str}

    def start_system_update(self):
        """Simula una actualización de apt update de forma asíncrona."""
        if not self.is_updating:
            self.is_updating = True
            threading.Thread(target=self._run_update_mock, daemon=True).start()

    def _run_update_mock(self):
        time.sleep(5) # Simula el tiempo que toma el apt update
        self.is_updating = False
