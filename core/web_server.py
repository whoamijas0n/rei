"""
REI - Local FastAPI Telemetry & Mobile Report Server (core/web_server.py)
Collects endpoint diagnostic reports over HTTP and serves mobile-friendly HTML audits
for instant QR code smartphone inspection.
Supports FastAPI/Uvicorn with a fallback to Python's standard library http.server.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import uuid

from core.report_renderer import (
    render_index_html,
    render_not_found_html,
    render_report_html,
)
from core.wifi import get_interface_ip

logger = logging.getLogger("REI.Core.WebServer")

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
    from pydantic import BaseModel, Field
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    BaseModel = object  # type: ignore


class StoredReport:
    """In-memory representation of an audit report with AI enrichment."""

    def __init__(self, report_id: str, data: Dict[str, Any]):
        self.report_id = report_id
        self.created_at = time.time()
        self.os_type = str(data.get("os_type", "Desconocido")).upper()
        self.category = str(data.get("category", "GENERAL")).upper()
        self.hostname = data.get("hostname") or data.get("telemetry", {}).get("hostname", "Host Remoto")
        self.telemetry = data.get("telemetry", {})
        self.metrics = data.get("metrics", [])
        self.hardware = data.get("hardware") or self.telemetry.get("hardware", {})
        self.hardware_audit = data.get("hardware_audit") or self.telemetry.get("hardware_audit", {})
        self.malware_audit = data.get("malware_audit") or self.telemetry.get("malware_audit", {})
        self.osi_network = data.get("osi_network") or self.telemetry.get("osi_network") or self.telemetry.get("osi", {})
        self.switch_audit = data.get("switch_audit") or self.telemetry.get("switch_audit", {})
        self.ai_analysis: Optional[Dict[str, Any]] = None
        self.overall_status = "OK"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "created_at": self.created_at,
            "os_type": self.os_type,
            "category": self.category,
            "hostname": self.hostname,
            "overall_status": self.overall_status,
            "hardware": self.hardware,
            "hardware_audit": self.hardware_audit,
            "malware_audit": self.malware_audit,
            "osi_network": self.osi_network,
            "switch_audit": self.switch_audit,
            "telemetry": self.telemetry,
            "metrics": self.metrics,
            "ai_analysis": self.ai_analysis,
        }


class REIWebServer:
    """
    Manages the FastAPI application lifecycle and asynchronous telemetry queue.
    Provides standard http.server fallback if FastAPI/Uvicorn is not yet installed.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        base_url: str = "http://10.0.0.1:8000",
        qr_preferred_interface: str = "wlan0",
    ):
        self.host = host
        self.port = port
        self.base_url = base_url.rstrip("/")
        self.qr_preferred_interface = qr_preferred_interface
        self._reports: Dict[str, StoredReport] = {}
        self._latest_report_id: Optional[str] = None
        self._report_counter: int = 0
        self._lock = threading.Lock()
        self._report_condition = threading.Condition(self._lock)
        self._new_report_event = threading.Event()
        self._server_thread: Optional[threading.Thread] = None
        self._http_server: Optional[HTTPServer] = None
        self._uvicorn_server: Optional[Any] = None
        self.running = False
        self.app: Optional[Any] = None

        if FASTAPI_AVAILABLE:
            self._setup_fastapi()

    def _setup_fastapi(self) -> None:
        """Configures FastAPI route handlers when FastAPI is installed."""
        self.app = FastAPI(title="REI Endpoint Diagnostic Hub", version="2.2.0")

        @self.app.get("/health")
        def health():
            return {"status": "ok", "service": "REI Diagnostic Hub", "timestamp": time.time()}

        @self.app.get("/w/{category:path}", response_class=PlainTextResponse)
        @self.app.get("/w", response_class=PlainTextResponse)
        @self.app.get("/api/v1/payload/windows/{category:path}", response_class=PlainTextResponse)
        @self.app.get("/api/v1/payload/windows", response_class=PlainTextResponse)
        @self.app.get("/api/v1/script/windows/{category:path}", response_class=PlainTextResponse)
        @self.app.get("/api/v1/script/windows", response_class=PlainTextResponse)
        def get_windows_script(category: str = "COMPLETO"):
            from plugins.endpoints.hid_windows import WindowsPayloadGenerator
            norm_cat = urllib.parse.unquote(category).strip("/") if category else "COMPLETO"
            script = WindowsPayloadGenerator.get_powershell_script(norm_cat or "COMPLETO", server_url=self.base_url)
            return PlainTextResponse(script, media_type="text/plain; charset=utf-8")

        @self.app.get("/l/{category:path}", response_class=PlainTextResponse)
        @self.app.get("/l", response_class=PlainTextResponse)
        @self.app.get("/api/v1/payload/linux/{category:path}", response_class=PlainTextResponse)
        @self.app.get("/api/v1/payload/linux", response_class=PlainTextResponse)
        @self.app.get("/api/v1/script/linux/{category:path}", response_class=PlainTextResponse)
        @self.app.get("/api/v1/script/linux", response_class=PlainTextResponse)
        def get_linux_script(category: str = "COMPLETO"):
            from plugins.endpoints.hid_linux import LinuxPayloadGenerator
            norm_cat = urllib.parse.unquote(category).strip("/") if category else "COMPLETO"
            script = LinuxPayloadGenerator.get_bash_script(norm_cat or "COMPLETO", server_url=self.base_url)
            return PlainTextResponse(script, media_type="text/plain; charset=utf-8")

        @self.app.post("/api/v1/endpoint/report")
        async def receive_report(request: Request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            report_id = str(uuid.uuid4())[:8]
            stored = StoredReport(report_id, data)

            with self._lock:
                self._reports[report_id] = stored
                self._latest_report_id = report_id
                self._report_counter += 1
                self._new_report_event.set()
                self._report_condition.notify_all()

            logger.info(f"Received endpoint report {report_id} from {stored.hostname} ({stored.os_type})")
            return {"status": "success", "report_id": report_id, "url": f"{self.base_url}/r/{report_id}"}

        @self.app.get("/api/v1/report/{report_id}")
        def get_report_json(report_id: str):
            target_id = self._latest_report_id if report_id == "latest" else report_id
            with self._lock:
                report = self._reports.get(target_id or "")
            if not report:
                raise HTTPException(status_code=404, detail="Informe no encontrado")
            return report.to_dict()

        @self.app.get("/", response_class=HTMLResponse)
        @self.app.get("/reports", response_class=HTMLResponse)
        def view_report_index():
            with self._lock:
                reports = list(self._reports.values())
            return render_index_html(reports)

        @self.app.get("/report/{report_id}", response_class=HTMLResponse)
        @self.app.get("/r/{report_id}", response_class=HTMLResponse)
        def view_html_report(report_id: str):
            target_id = self._latest_report_id if report_id == "latest" else report_id
            with self._lock:
                report = self._reports.get(target_id or "")
                has_latest = self._latest_report_id is not None
            if not report:
                return HTMLResponse(
                    render_not_found_html(report_id, has_latest=has_latest),
                    status_code=404,
                )
            return self._render_mobile_html(report)

    def _render_mobile_html(self, report: StoredReport) -> str:
        """Renderiza el informe HTML del portal de resultados de REI."""
        return render_report_html(report)

    def attach_ai_analysis(self, report_id: str, ai_data: Dict[str, Any], overall_status: str = "OK") -> None:
        """Enriches an existing report with Gemini AI insights."""
        with self._lock:
            target = self._reports.get(report_id)
            if target:
                target.ai_analysis = ai_data
                target.overall_status = overall_status

    def store_local_report(
        self,
        os_type: str,
        category: str,
        hostname: str,
        telemetry: Dict[str, Any],
        overall_status: str = "OK",
        ai_analysis: Optional[Dict[str, Any]] = None,
        hardware: Optional[Dict[str, Any]] = None,
        hardware_audit: Optional[Dict[str, Any]] = None,
        malware_audit: Optional[Dict[str, Any]] = None,
        osi_network: Optional[Dict[str, Any]] = None,
        switch_audit: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Stores a report constructed locally by REI and returns its report_id."""
        report_id = str(uuid.uuid4())[:8]
        stored = StoredReport(
            report_id=report_id,
            data={
                "os_type": os_type,
                "category": category,
                "hostname": hostname,
                "telemetry": telemetry,
                "hardware": hardware or (telemetry.get("hardware", {}) if isinstance(telemetry, dict) else {}),
                "hardware_audit": hardware_audit or (telemetry.get("hardware_audit", {}) if isinstance(telemetry, dict) else {}),
                "malware_audit": malware_audit or (telemetry.get("malware_audit", {}) if isinstance(telemetry, dict) else {}),
                "osi_network": osi_network or (telemetry.get("osi_network") or telemetry.get("osi", {}) if isinstance(telemetry, dict) else {}),
                "switch_audit": switch_audit or (telemetry.get("switch_audit", {}) if isinstance(telemetry, dict) else {}),
            },
        )
        stored.overall_status = overall_status
        stored.ai_analysis = ai_analysis
        if hardware:
            stored.hardware = hardware
        if hardware_audit:
            stored.hardware_audit = hardware_audit
        if malware_audit:
            stored.malware_audit = malware_audit
        if osi_network:
            stored.osi_network = osi_network
        if switch_audit:
            stored.switch_audit = switch_audit

        with self._lock:
            self._reports[report_id] = stored
            self._latest_report_id = report_id
            self._report_counter += 1
            self._new_report_event.set()
            self._report_condition.notify_all()

        return report_id

    def get_report_watermark(self) -> int:
        """Returns the current report counter for race-free synchronization."""
        with self._lock:
            return self._report_counter

    def get_latest_report_id(self) -> Optional[str]:
        """Returns latest report ID, if any."""
        with self._lock:
            return self._latest_report_id

    def get_report(self, report_id: str) -> Optional[StoredReport]:
        """Returns stored report by ID, or latest if 'latest'."""
        with self._lock:
            target_id = self._latest_report_id if report_id == "latest" else report_id
            return self._reports.get(target_id or "")

    def get_latest_report(self) -> Optional[StoredReport]:
        """Returns the most recent stored report."""
        with self._lock:
            if self._latest_report_id:
                return self._reports.get(self._latest_report_id)
            return None

    def get_active_ip(self, preferred_interface: Optional[str] = None) -> Optional[str]:
        """
        Attempts to detect the active IPv4 address on Wi-Fi (wlan0) or Ethernet (eth0).
        Returns None if not connected to an external network.
        """
        iface = preferred_interface or self.qr_preferred_interface or "wlan0"
        ip = get_interface_ip(iface)
        if ip and not ip.startswith("127.") and not ip.startswith("10.0.0."):
            return ip

        # Fallback to eth0 if preferred was wlan0
        if iface != "eth0":
            eth_ip = get_interface_ip("eth0")
            if eth_ip and not eth_ip.startswith("127.") and not eth_ip.startswith("10.0.0."):
                return eth_ip

        return None

    def get_qr_url(
        self,
        report_id: Optional[str] = None,
        force_interface: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Returns (qr_url, interface_label) for mobile QR code presentation.
        Dynamically detects active Wi-Fi (wlan0) or Ethernet (eth0) IP so smartphones
        on the same network can access the report directly.
        Falls back to base_url (usb0) if Wi-Fi is disconnected or force_interface='usb'.
        """
        target_id = report_id or self._latest_report_id or "latest"

        if force_interface == "usb":
            return f"{self.base_url}/r/{target_id}", "USB"

        active_ip = self.get_active_ip(self.qr_preferred_interface)
        if active_ip:
            label = "WIFI" if self.qr_preferred_interface.startswith("wlan") else "NET"
            return f"http://{active_ip}:{self.port}/r/{target_id}", label

        # Fallback to USB gadget base_url
        return f"{self.base_url}/r/{target_id}", "USB"

    def get_report_url(self, report_id: Optional[str] = None, dynamic: bool = False) -> str:
        """
        Returns report URL. If dynamic=True, resolves dynamic Wi-Fi/Ethernet IP for smartphone QR scanning.
        Otherwise, returns standard base_url (http://10.0.0.1:8000/r/...) for USB endpoint communication.
        """
        if dynamic:
            url, _ = self.get_qr_url(report_id)
            return url
        target_id = report_id or self._latest_report_id or "latest"
        return f"{self.base_url}/r/{target_id}"

    def wait_for_report(
        self,
        watermark: Optional[int] = None,
        timeout_seconds: float = 30.0,
    ) -> Optional[StoredReport]:
        """
        Blocks worker thread until a new endpoint report arrives or timeout expires.
        Immune to race conditions where reports arrive while HID typing is in progress.
        """
        deadline = time.monotonic() + timeout_seconds

        with self._lock:
            # Target watermark: if specified, wait until counter exceeds it.
            # If not specified, wait until counter exceeds the value at call time.
            target_watermark = watermark if watermark is not None else self._report_counter

            # If report already arrived while injecting/typing, return immediately!
            if self._report_counter > target_watermark and self._latest_report_id:
                return self._reports.get(self._latest_report_id)

            while self._report_counter <= target_watermark:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._report_condition.wait(timeout=remaining)

            if self._latest_report_id:
                return self._reports.get(self._latest_report_id)
            return None

    def start(self) -> None:
        """Launches the server in a non-blocking background daemon thread."""
        if self.running:
            return

        self.running = True

        if FASTAPI_AVAILABLE and self.app is not None:
            config = uvicorn.Config(
                app=self.app,
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
            )
            self._uvicorn_server = uvicorn.Server(config)

            def _run_uvicorn():
                logger.info(f"FastAPI Telemetry Server running on {self.host}:{self.port}")
                try:
                    self._uvicorn_server.run()
                except Exception as ex:
                    logger.error(f"FastAPI server error: {ex}")
                finally:
                    self.running = False

            self._server_thread = threading.Thread(target=_run_uvicorn, name="REIFastAPIServer", daemon=True)
            self._server_thread.start()
        else:
            # Fallback using standard library http.server
            outer = self

            class FallbackHTTPHandler(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    pass  # Suppress default request logging to keep console clean

                def do_GET(self):
                    parsed_path = urllib.parse.urlparse(self.path).path
                    if parsed_path in ("/", "/reports"):
                        with outer._lock:
                            reports = list(outer._reports.values())
                        html_content = render_index_html(reports)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(html_content.encode("utf-8"))
                    elif parsed_path == "/health":
                        data = json.dumps({"status": "ok", "service": "REI Diagnostic Hub", "timestamp": time.time()})
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(data.encode("utf-8"))
                    elif parsed_path == "/w" or parsed_path.startswith("/w/") or parsed_path.startswith("/api/v1/payload/windows") or parsed_path.startswith("/api/v1/script/windows"):
                        if parsed_path in ("/w", "/w/"):
                            cat = "COMPLETO"
                        elif parsed_path.startswith("/w/"):
                            cat = parsed_path[3:]
                        elif "/windows/" in parsed_path:
                            cat = parsed_path.split("/windows/")[-1]
                        else:
                            cat = "COMPLETO"
                        cat = urllib.parse.unquote(cat)
                        from plugins.endpoints.hid_windows import WindowsPayloadGenerator
                        script = WindowsPayloadGenerator.get_powershell_script(cat or "COMPLETO", server_url=outer.base_url)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(script.encode("utf-8"))
                    elif parsed_path == "/l" or parsed_path.startswith("/l/") or parsed_path.startswith("/api/v1/payload/linux") or parsed_path.startswith("/api/v1/script/linux"):
                        if parsed_path in ("/l", "/l/"):
                            cat = "COMPLETO"
                        elif parsed_path.startswith("/l/"):
                            cat = parsed_path[3:]
                        elif "/linux/" in parsed_path:
                            cat = parsed_path.split("/linux/")[-1]
                        else:
                            cat = "COMPLETO"
                        cat = urllib.parse.unquote(cat)
                        from plugins.endpoints.hid_linux import LinuxPayloadGenerator
                        script = LinuxPayloadGenerator.get_bash_script(cat or "COMPLETO", server_url=outer.base_url)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(script.encode("utf-8"))
                    elif parsed_path.startswith("/api/v1/report/"):
                        rep_id = parsed_path.split("/")[-1]
                        target_id = outer._latest_report_id if rep_id == "latest" else rep_id
                        with outer._lock:
                            rep = outer._reports.get(target_id or "")
                        if rep:
                            data = json.dumps(rep.to_dict())
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(data.encode("utf-8"))
                        else:
                            self.send_response(404)
                            self.end_headers()
                    elif parsed_path.startswith("/report/") or parsed_path.startswith("/r/"):
                        rep_id = parsed_path.split("/")[-1]
                        target_id = outer._latest_report_id if rep_id == "latest" else rep_id
                        with outer._lock:
                            rep = outer._reports.get(target_id or "")
                            has_latest = outer._latest_report_id is not None
                        if rep:
                            html_content = outer._render_mobile_html(rep)
                            self.send_response(200)
                            self.send_header("Content-Type", "text/html; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(html_content.encode("utf-8"))
                        else:
                            html_content = render_not_found_html(rep_id, has_latest=has_latest)
                            self.send_response(404)
                            self.send_header("Content-Type", "text/html; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(html_content.encode("utf-8"))
                    else:
                        self.send_response(404)
                        self.end_headers()

                def do_POST(self):
                    parsed_path = urllib.parse.urlparse(self.path).path
                    if parsed_path == "/api/v1/endpoint/report":
                        length = int(self.headers.get("content-length", 0))
                        body = self.rfile.read(length) if length > 0 else b"{}"
                        try:
                            data = json.loads(body.decode("utf-8"))
                        except Exception:
                            data = {}

                        report_id = str(uuid.uuid4())[:8]
                        stored = StoredReport(report_id, data)

                        with outer._lock:
                            outer._reports[report_id] = stored
                            outer._latest_report_id = report_id
                            outer._report_counter += 1
                            outer._new_report_event.set()
                            outer._report_condition.notify_all()

                        resp = json.dumps({"status": "success", "report_id": report_id, "url": f"{outer.base_url}/r/{report_id}"})
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(resp.encode("utf-8"))
                    else:
                        self.send_response(404)
                        self.end_headers()

            def _run_http():
                try:
                    self._http_server = HTTPServer((self.host, self.port), FallbackHTTPHandler)
                    logger.info(f"Standard HTTP Telemetry Server running on {self.host}:{self.port}")
                    self._http_server.serve_forever()
                except Exception as ex:
                    logger.error(f"HTTP server error: {ex}")
                finally:
                    self.running = False

            self._server_thread = threading.Thread(target=_run_http, name="REIHTTPServer", daemon=True)
            self._server_thread.start()

    def stop(self) -> None:
        """Gracefully shuts down the background telemetry server."""
        if not self.running:
            return

        self.running = False
        if self._uvicorn_server:
            self._uvicorn_server.should_exit = True
        if self._http_server:
            self._http_server.shutdown()
            self._http_server.server_close()
        logger.info("Telemetry Server stopped.")
