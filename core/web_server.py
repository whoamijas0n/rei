"""
REI - Local FastAPI Telemetry & Mobile Report Server (core/web_server.py)
Collects endpoint diagnostic reports over HTTP and serves mobile-friendly HTML audits
for instant QR code smartphone inspection.
Supports FastAPI/Uvicorn with a fallback to Python's standard library http.server.
"""

from dataclasses import dataclass
import datetime
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.parse
import uuid

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
            "telemetry": self.telemetry,
            "metrics": self.metrics,
            "ai_analysis": self.ai_analysis,
        }


class REIWebServer:
    """
    Manages the FastAPI application lifecycle and asynchronous telemetry queue.
    Provides standard http.server fallback if FastAPI/Uvicorn is not yet installed.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8000, base_url: str = "http://10.0.0.1:8000"):
        self.host = host
        self.port = port
        self.base_url = base_url.rstrip("/")
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

        @self.app.get("/report/{report_id}", response_class=HTMLResponse)
        @self.app.get("/r/{report_id}", response_class=HTMLResponse)
        def view_html_report(report_id: str):
            target_id = self._latest_report_id if report_id == "latest" else report_id
            with self._lock:
                report = self._reports.get(target_id or "")
            if not report:
                return HTMLResponse(
                    "<html><body style='background:#0f172a;color:#fff;font-family:sans-serif;padding:2rem;text-align:center'>"
                    "<h2>Informe no encontrado</h2><p>El código QR o ID solicitado ya no está disponible en memoria.</p>"
                    "</body></html>",
                    status_code=404,
                )
            return self._render_mobile_html(report)

    def _render_mobile_html(self, report: StoredReport) -> str:
        """Renders mobile-optimized dark mode Cyberdeck HTML report."""
        dt_str = datetime.datetime.fromtimestamp(report.created_at).strftime("%Y-%m-%d %H:%M:%S")

        status_color = "#10b981"  # OK Green
        if report.overall_status in ("WARN", "WARNING"):
            status_color = "#f59e0b"
        elif report.overall_status in ("CRIT", "CRITICAL", "FAIL"):
            status_color = "#ef4444"

        # AI section HTML
        ai_html = ""
        if report.ai_analysis:
            summary = html.escape(report.ai_analysis.get("summary", "Sin resumen"))
            causes = report.ai_analysis.get("root_causes", [])
            plan = report.ai_analysis.get("action_plan", [])

            causes_li = "".join([f"<li><strong>{html.escape(str(c))}</strong></li>" for c in causes]) or "<li>Sin anomalías críticas detectadas.</li>"
            plan_li = "".join([f"<li>{html.escape(str(p))}</li>" for p in plan]) or "<li>No se requieren acciones correctivas inmediatas.</li>"

            ai_html = f"""
            <div class="card ai-card">
                <div class="card-header">
                    <span class="badge ai-badge">✨ ANÁLISIS INTELIGENTE (GEMINI AI)</span>
                </div>
                <div class="card-body">
                    <p class="ai-summary"><strong>Síntesis:</strong> {summary}</p>
                    <br>
                    <h4>Causas Raíz Identificadas:</h4>
                    <ul>{causes_li}</ul>
                    <br>
                    <h4>Plan de Acción Recomendado:</h4>
                    <ol>{plan_li}</ol>
                </div>
            </div>
            """

        # Telemetry metrics cards
        telemetry_rows = ""
        for k, v in report.telemetry.items():
            k_clean = html.escape(str(k).replace("_", " ").title())
            if isinstance(v, (dict, list)):
                v_clean = f"<pre>{html.escape(str(v))}</pre>"
            else:
                v_clean = html.escape(str(v))
            telemetry_rows += f"<tr><td><strong>{k_clean}</strong></td><td>{v_clean}</td></tr>"

        return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>REI Reporte - {html.escape(report.hostname)}</title>
    <style>
        :root {{
            --bg-base: #090d16;
            --bg-card: #131b2e;
            --border: #1e293b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --cyan: #06b6d4;
            --status: {status_color};
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg-base);
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            padding: 1rem;
            line-height: 1.5;
        }}
        .header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 2px solid var(--border);
            padding-bottom: 0.75rem;
            margin-bottom: 1rem;
        }}
        .title {{ font-size: 1.25rem; font-weight: 800; color: var(--cyan); letter-spacing: 1px; }}
        .badge {{
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 700;
            background: var(--status);
            color: #000;
        }}
        .ai-badge {{ background: #8b5cf6; color: #fff; }}
        .meta {{ font-size: 0.8rem; color: var(--text-muted); margin-bottom: 1rem; }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1rem;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }}
        .ai-card {{ border-color: #8b5cf6; }}
        .card-header {{ font-weight: 700; margin-bottom: 0.5rem; font-size: 0.95rem; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
        td {{ padding: 0.5rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
        ul, ol {{ padding-left: 1.25rem; margin-top: 0.5rem; font-size: 0.85rem; }}
        li {{ margin-bottom: 0.4rem; }}
        pre {{ background: #0b1120; padding: 0.5rem; border-radius: 4px; overflow-x: auto; font-size: 0.75rem; }}
        .footer {{ text-align: center; font-size: 0.75rem; color: var(--text-muted); margin-top: 2rem; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="title">⚡ REI DIAGNOSTICS</div>
        <div class="badge">{report.overall_status}</div>
    </div>
    <div class="meta">
        <div><strong>Host:</strong> {html.escape(report.hostname)} | <strong>SO:</strong> {report.os_type}</div>
        <div><strong>Categoría:</strong> {report.category} | <strong>Fecha:</strong> {dt_str}</div>
    </div>

    {ai_html}

    <div class="card">
        <div class="card-header">📊 TELEMETRÍA DE ENDPOINT</div>
        <table>
            <tbody>
                {telemetry_rows or "<tr><td>Sin telemetría detallada.</td></tr>"}
            </tbody>
        </table>
    </div>

    <div class="footer">
        REI Autonomous Multi-Interface Diagnostic Hub • v2.2
    </div>
</body>
</html>"""

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
            },
        )
        stored.overall_status = overall_status
        stored.ai_analysis = ai_analysis

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

    def get_report_url(self, report_id: Optional[str] = None) -> str:
        """Returns compact URL for QR code encoding (fits Version 2 QR matrix)."""
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
                    if parsed_path == "/health":
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
                        if rep:
                            html_content = outer._render_mobile_html(rep)
                            self.send_response(200)
                            self.send_header("Content-Type", "text/html; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(html_content.encode("utf-8"))
                        else:
                            self.send_response(404)
                            self.end_headers()
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
