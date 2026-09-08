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
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse
import uuid

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
        """Renders mobile-optimized dark mode Cyberdeck HTML report with Hardware & OSI Diagnostics."""
        dt_str = datetime.datetime.fromtimestamp(report.created_at).strftime("%Y-%m-%d %H:%M:%S")

        status_color = "#10b981"  # OK Green
        if report.overall_status in ("WARN", "WARNING"):
            status_color = "#f59e0b"
        elif report.overall_status in ("CRIT", "CRITICAL", "FAIL"):
            status_color = "#ef4444"

        # 1. Hardware & Host Identity Card
        hw = report.hardware or (report.telemetry.get("hardware") if isinstance(report.telemetry, dict) else {})
        hw_html = ""
        if hw and isinstance(hw, dict):
            mfr = html.escape(str(hw.get("manufacturer") or hw.get("mfr") or "N/A"))
            model = html.escape(str(hw.get("model") or "PC"))
            serial = html.escape(str(hw.get("serial") or hw.get("serial_number") or "N/A"))
            os_name = html.escape(str(hw.get("os_name") or hw.get("os") or report.os_type))
            os_ver = html.escape(str(hw.get("os_version") or hw.get("ver") or hw.get("kernel") or hw.get("os_build") or ""))
            cpu_m = html.escape(str(hw.get("cpu_model") or hw.get("cpu") or "N/A"))
            ram_tot = hw.get("ram_total_gb") or hw.get("ram_gb") or "N/A"
            ram_free = hw.get("ram_free_gb") or "N/A"

            hw_html = f"""
            <div class="card">
                <div class="card-header">💻 IDENTIDAD Y HARDWARE DEL HOST</div>
                <table>
                    <tbody>
                        <tr><td><strong>Equipo / Marca:</strong></td><td>{mfr} {model}</td></tr>
                        <tr><td><strong>Número de Serie:</strong></td><td><code>{serial}</code></td></tr>
                        <tr><td><strong>Sistema Operativo:</strong></td><td>{os_name} {os_ver}</td></tr>
                        <tr><td><strong>Procesador (CPU):</strong></td><td>{cpu_m}</td></tr>
                        <tr><td><strong>Memoria RAM:</strong></td><td>{ram_tot} GB total ({ram_free} GB libre)</td></tr>
                    </tbody>
                </table>
            </div>
            """

        # 1.5 Deep Hardware Audit (Comprehensive Subsystems)
        hw_audit = getattr(report, "hardware_audit", None) or (report.telemetry.get("hardware_audit") if isinstance(report.telemetry, dict) else {})
        hw_audit_html = ""
        if hw_audit and isinstance(hw_audit, dict) and any(k in hw_audit for k in ("system", "cpu", "memory", "storage", "graphics", "battery")):
            sys_info = hw_audit.get("system", {}) if isinstance(hw_audit.get("system"), dict) else {}
            cpu_info = hw_audit.get("cpu", {}) if isinstance(hw_audit.get("cpu"), dict) else {}
            mem_info = hw_audit.get("memory", {}) if isinstance(hw_audit.get("memory"), dict) else {}
            storage_info = hw_audit.get("storage", {}) if isinstance(hw_audit.get("storage"), dict) else {}
            graphics_info = hw_audit.get("graphics", []) if isinstance(hw_audit.get("graphics"), list) else []
            battery_info = hw_audit.get("battery") if isinstance(hw_audit.get("battery"), dict) else None

            # Motherboard & BIOS
            b_mfr = html.escape(str(sys_info.get("board_mfr") or sys_info.get("manufacturer") or "N/A"))
            b_prod = html.escape(str(sys_info.get("board_product") or sys_info.get("model") or "N/A"))
            bios_v = html.escape(str(sys_info.get("bios_version") or "N/A"))
            bios_d = html.escape(str(sys_info.get("bios_date") or ""))
            bios_str = f"{bios_v} ({bios_d})" if bios_d else bios_v

            # Battery row
            bat_row = ""
            if battery_info and battery_info.get("present"):
                ch = battery_info.get("charge_pct", 0)
                st = html.escape(str(battery_info.get("status") or "Conectada"))
                ch_color = "pill-ok" if ch > 30 else ("pill-warn" if ch > 15 else "pill-crit")
                bat_row = f"""<tr><td><strong>🔋 Batería:</strong></td><td><span class="status-pill {ch_color}">{ch}%</span> ({st})</td></tr>"""

            # CPU & Thermals
            c_name = html.escape(str(cpu_info.get("name") or "N/A"))
            c_cores = cpu_info.get("cores") or "N/A"
            c_threads = cpu_info.get("threads") or "N/A"
            c_load = cpu_info.get("load_pct", 0)
            try:
                c_load_float = float(c_load)
            except (ValueError, TypeError):
                c_load_float = 0.0
            c_load_color = "pill-crit" if c_load_float > 90 else ("pill-warn" if c_load_float > 75 else "pill-ok")
            c_mhz = cpu_info.get("current_mhz") or cpu_info.get("max_mhz")
            c_freq_str = f" | Reloj: {c_mhz} MHz" if c_mhz else ""
            c_temp = cpu_info.get("temp_c")
            c_temp_row = ""
            if c_temp is not None:
                try:
                    c_temp_float = float(c_temp)
                    if c_temp_float > 0:
                        t_color = "pill-crit" if c_temp_float > 85 else ("pill-warn" if c_temp_float > 75 else "pill-ok")
                        c_temp_row = f"""<tr><td>Temperatura CPU:</td><td><span class="status-pill {t_color}">{c_temp}°C</span>{' ⚠️ Estrangulamiento Térmico' if c_temp_float > 85 else ''}</td></tr>"""
                except (ValueError, TypeError):
                    pass

            # Memory & Slots
            m_tot = mem_info.get("total_gb", 0)
            m_used = mem_info.get("used_gb", 0)
            m_free = mem_info.get("free_gb", 0)
            m_pct = mem_info.get("usage_pct", 0)
            try:
                m_pct_float = float(m_pct)
            except (ValueError, TypeError):
                m_pct_float = 0.0
            m_pct_color = "pill-crit" if m_pct_float > 90 else ("pill-warn" if m_pct_float > 80 else "pill-ok")
            slots_used = mem_info.get("slots_used")
            slots_tot = mem_info.get("slots_total")
            slots_str = f" ({slots_used} de {slots_tot} ranuras ocupadas)" if slots_tot else ""

            dimms = mem_info.get("dimms", [])
            dimms_html = ""
            if dimms and isinstance(dimms, list):
                dimm_items = []
                for d in dimms:
                    if isinstance(d, dict):
                        d_slot = d.get("slot") or "Slot"
                        d_sz = d.get("size_gb") or "?"
                        d_spd = f" @ {d.get('speed_mhz')}MHz" if d.get("speed_mhz") else ""
                        d_mfr = f" ({d.get('mfr')})" if d.get("mfr") else ""
                        dimm_items.append(f"<li><code>{d_slot}</code>: {d_sz} GB{d_spd}{d_mfr}</li>")
                if dimm_items:
                    dimms_html = f"<tr><td>Módulos Físicos:</td><td><ul style='margin:0;padding-left:1rem'>{''.join(dimm_items)}</ul></td></tr>"

            # Storage & SMART
            p_drives = storage_info.get("physical_drives", [])
            drives_rows = []
            if p_drives and isinstance(p_drives, list):
                for pd in p_drives:
                    if isinstance(pd, dict):
                        pd_name = html.escape(str(pd.get("model") or pd.get("name") or "Unidad de Disco"))
                        pd_sz = pd.get("size_gb") or pd.get("size") or "N/A"
                        pd_bus = pd.get("bus") or ""
                        pd_bus_str = f" [{pd_bus}]" if pd_bus else ""
                        pd_smart = pd.get("smart_fail")
                        smart_pill = ""
                        if pd_smart is True:
                            smart_pill = ' <span class="status-pill pill-crit">⚠️ FALLO SMART</span>'
                        elif pd_smart is False:
                            smart_pill = ' <span class="status-pill pill-ok">SMART OK</span>'

                        rot = pd.get("rotational")
                        media_type = "HDD Mecánico" if rot is True else ("SSD/NVMe" if rot is False else "")
                        media_str = f" ({media_type})" if media_type else ""

                        drives_rows.append(f"<li><strong>{pd_name}</strong> - {pd_sz}{' GB' if isinstance(pd_sz, (int, float)) else ''}{pd_bus_str}{media_str}{smart_pill}</li>")

            vols = storage_info.get("volumes", [])
            vol_rows = []
            if vols and isinstance(vols, list):
                for v in vols:
                    if isinstance(v, dict):
                        v_drv = html.escape(str(v.get("drive") or v.get("mount") or "Vol"))
                        v_sz = v.get("size_gb") or v.get("size") or "?"
                        v_free = v.get("free_gb") or v.get("free") or "?"
                        v_fs = v.get("fs") or ""
                        v_pct = v.get("free_pct")
                        if v_pct is not None:
                            try:
                                f_pct_num = float(v_pct)
                                v_pill = '<span class="status-pill pill-crit">Crítico</span>' if f_pct_num < 5 else ('<span class="status-pill pill-warn">Bajo</span>' if f_pct_num < 10 else '<span class="status-pill pill-ok">OK</span>')
                                vol_rows.append(f"<tr><td>Unidad <strong>{v_drv}</strong> ({v_fs}):</td><td>{v_free} GB libres de {v_sz} GB ({v_pct}% libre) {v_pill}</td></tr>")
                            except (ValueError, TypeError):
                                vol_rows.append(f"<tr><td>Unidad <strong>{v_drv}</strong>:</td><td>{v_free} libre / {v_sz}</td></tr>")
                        else:
                            use_pct = v.get("use_pct") or "N/A"
                            vol_rows.append(f"<tr><td>Montaje <strong>{v_drv}</strong>:</td><td>{v_free} libre de {v_sz} (Uso: {use_pct})</td></tr>")

            # Graphics
            gpus_html = ""
            if graphics_info and isinstance(graphics_info, list):
                gpu_items = []
                for g in graphics_info:
                    if isinstance(g, dict):
                        g_name = html.escape(str(g.get("name") or "GPU"))
                        g_vram = f" | VRAM: {g.get('vram_mb')} MB" if g.get("vram_mb") else ""
                        g_drv = f" | Driver: {g.get('driver')}" if g.get("driver") else ""
                        g_res = f" | Res: {g.get('res')}" if g.get("res") else ""
                        gpu_items.append(f"<li>{g_name}{g_vram}{g_drv}{g_res}</li>")
                if gpu_items:
                    gpus_html = f"""
                    <tr class="layer-header"><td colspan="2">GRÁFICOS Y PANTALLA (GPU)</td></tr>
                    <tr><td>Adaptadores de Video:</td><td><ul style='margin:0;padding-left:1rem'>{''.join(gpu_items)}</ul></td></tr>
                    """

            hw_audit_html = f"""
            <div class="card hw-card">
                <div class="card-header">⚙️ AUDITORÍA EXHAUSTIVA DE HARDWARE</div>
                <table>
                    <tbody>
                        <tr class="layer-header"><td colspan="2">PLACA BASE, FIRMWARE Y ENERGÍA</td></tr>
                        <tr><td>Motherboard:</td><td>{b_mfr} {b_prod}</td></tr>
                        <tr><td>BIOS / UEFI:</td><td>{bios_str}</td></tr>
                        {bat_row}

                        <tr class="layer-header"><td colspan="2">PROCESADOR (CPU) Y TÉRMICA</td></tr>
                        <tr><td>Modelo:</td><td><strong>{c_name}</strong></td></tr>
                        <tr><td>Núcleos / Hilos:</td><td>{c_cores} Cores / {c_threads} Threads{c_freq_str}</td></tr>
                        <tr><td>Uso de CPU:</td><td><span class="status-pill {c_load_color}">{c_load}%</span></td></tr>
                        {c_temp_row}

                        <tr class="layer-header"><td colspan="2">MEMORIA RAM Y TOPOLOGÍA</td></tr>
                        <tr><td>Capacidad:</td><td><strong>{m_used} GB usados</strong> de {m_tot} GB total ({m_free} GB libre)</td></tr>
                        <tr><td>Saturación:</td><td><span class="status-pill {m_pct_color}">{m_pct}%</span>{slots_str}</td></tr>
                        {dimms_html}

                        <tr class="layer-header"><td colspan="2">ALMACENAMIENTO Y SALUD SMART</td></tr>
                        {f'<tr><td>Unidades Físicas:</td><td><ul style="margin:0;padding-left:1rem">{"".join(drives_rows)}</ul></td></tr>' if drives_rows else ''}
                        {''.join(vol_rows)}

                        {gpus_html}
                    </tbody>
                </table>
            </div>
            """

        # 1.6 Malware & Live Threat Audit
        malware_audit = getattr(report, "malware_audit", None) or (report.telemetry.get("malware_audit") if isinstance(report.telemetry, dict) else {})
        malware_audit_html = ""
        if malware_audit and isinstance(malware_audit, dict) and any(k in malware_audit for k in ("defenses", "suspicious_processes", "persistence", "network_c2", "recent_artifacts", "threat_score", "threat_level")):
            th_lvl = str(malware_audit.get("threat_level") or "BAJO").upper()
            th_score = malware_audit.get("threat_score", 0)
            th_pill = '<span class="status-pill pill-crit">CRÍTICO</span>' if th_lvl == "CRITICO" else ('<span class="status-pill pill-warn">MEDIO</span>' if th_lvl == "MEDIO" else '<span class="status-pill pill-ok">BAJO</span>')

            # Defenses
            defs = malware_audit.get("defenses", {}) if isinstance(malware_audit.get("defenses"), dict) else {}
            av_n = html.escape(str(defs.get("antivirus_name") or "Ninguno"))
            av_act = defs.get("antivirus_active", False)
            av_pill = '<span class="status-pill pill-ok">Activo</span>' if av_act else '<span class="status-pill pill-crit">Desactivado</span>'
            fw = defs.get("firewall_enabled")
            fw_pill = ('<span class="status-pill pill-ok">Habilitado</span>' if fw else '<span class="status-pill pill-crit">Deshabilitado</span>') if fw is not None else '<span class="status-pill pill-warn">No reportado</span>'
            sec_mod = defs.get("security_module")
            sec_mod_row = f"<tr><td>Módulo de Seguridad:</td><td><strong>{html.escape(str(sec_mod))}</strong></td></tr>" if sec_mod else ""

            # Suspicious Processes
            susp_procs = malware_audit.get("suspicious_processes", []) if isinstance(malware_audit.get("suspicious_processes"), list) else []
            if susp_procs:
                proc_items = []
                for p in susp_procs:
                    if isinstance(p, dict):
                        p_pid = p.get("pid", "?")
                        p_n = html.escape(str(p.get("name") or "proceso"))
                        p_path = html.escape(str(p.get("path") or ""))
                        p_rsn = html.escape(str(p.get("reason") or "Anomalía"))
                        proc_items.append(f"<li><span class='status-pill pill-crit'>PID {p_pid}</span> <strong>{p_n}</strong> - <code>{p_path}</code><br><small style='color:#f87171'>Motivo: {p_rsn}</small></li>")
                procs_html = f"<tr><td>Procesos Anómalos:</td><td><ul style='margin:0;padding-left:1rem'>{''.join(proc_items)}</ul></td></tr>"
            else:
                procs_html = "<tr><td>Procesos en Memoria:</td><td><span class='status-pill pill-ok'>Correcto</span> Sin procesos en directorios temporales ni anomalías detectadas.</td></tr>"

            # Persistence
            persist = malware_audit.get("persistence", []) if isinstance(malware_audit.get("persistence"), list) else []
            if persist:
                persist_items = []
                for pr in persist:
                    if isinstance(pr, dict):
                        pr_t = html.escape(str(pr.get("type") or "Persistencia"))
                        pr_n = html.escape(str(pr.get("name") or ""))
                        pr_p = html.escape(str(pr.get("path") or ""))
                        persist_items.append(f"<li><strong>[{pr_t}]</strong> {pr_n}: <code>{pr_p}</code></li>")
                persist_html = f"<tr><td>Mecanismos de Inicio:</td><td><ul style='margin:0;padding-left:1rem'>{''.join(persist_items)}</ul></td></tr>"
            else:
                persist_html = "<tr><td>Mecanismos de Inicio:</td><td><span class='status-pill pill-ok'>Limpio</span> Sin entradas de persistencia registradas.</td></tr>"

            # Network C2 & Sockets
            net_c2 = malware_audit.get("network_c2", {}) if isinstance(malware_audit.get("network_c2"), dict) else {}
            conns = net_c2.get("established_connections", []) if isinstance(net_c2.get("established_connections"), list) else []
            c2_items = []
            for c in conns:
                if isinstance(c, dict):
                    rip = html.escape(str(c.get("remote_ip") or ""))
                    rport = c.get("remote_port", 0)
                    proc = html.escape(str(c.get("process") or ""))
                    is_susp = c.get("suspicious", False)
                    if is_susp:
                        c2_items.append(f"<li><span class='status-pill pill-crit'>SOSPECHOSO</span> <code>{rip}:{rport}</code> (Proc: {proc})</li>")
                    else:
                        c2_items.append(f"<li><code>{rip}:{rport}</code> (Proc: {proc})</li>")
            c2_html = f"<tr><td>Conexiones Establecidas:</td><td><ul style='margin:0;padding-left:1rem'>{''.join(c2_items)}</ul></td></tr>" if c2_items else "<tr><td>Conexiones Establecidas:</td><td>Sin conexiones externas salientes activas.</td></tr>"

            hosts_hijack = net_c2.get("hosts_file_hijack", False)
            hosts_pill = '<span class="status-pill pill-crit">ALTERADO / HIJACK</span> Redirección de antivirus o dominios críticos detectada' if hosts_hijack else '<span class="status-pill pill-ok">Íntegro</span>'

            # Recent Temp Artifacts
            recent_art = malware_audit.get("recent_artifacts", []) if isinstance(malware_audit.get("recent_artifacts"), list) else []
            if recent_art:
                art_items = []
                for a in recent_art:
                    if isinstance(a, dict):
                        a_n = html.escape(str(a.get("name") or ""))
                        a_p = html.escape(str(a.get("path") or ""))
                        a_sz = a.get("size_kb", 0)
                        a_dt = html.escape(str(a.get("date") or ""))
                        art_items.append(f"<li><code>{a_n}</code> ({a_sz} KB, {a_dt}) - <small>{a_p}</small></li>")
                art_html = f"<tr><td>Binarios en Temp (<7 días):</td><td><ul style='margin:0;padding-left:1rem'>{''.join(art_items)}</ul></td></tr>"
            else:
                art_html = "<tr><td>Binarios en Temp (<7 días):</td><td><span class='status-pill pill-ok'>Ninguno</span> No se hallaron ejecutables recientes en carpetas temporales.</td></tr>"

            malware_audit_html = f"""
            <div class="card malware-card">
                <div class="card-header">🛡️ AUDITORÍA DE SEGURIDAD Y ANÁLISIS DE AMENAZAS</div>
                <table>
                    <tbody>
                        <tr class="layer-header"><td colspan="2">EVALUACIÓN HEURÍSTICA Y DEFENSAS</td></tr>
                        <tr><td>Nivel de Amenaza Estimado:</td><td>{th_pill} (Puntaje Heurístico: <strong>{th_score} pts</strong>)</td></tr>
                        <tr><td>Software Antivirus:</td><td><strong>{av_n}</strong> ({av_pill})</td></tr>
                        <tr><td>Cortafuegos (Firewall):</td><td>{fw_pill}</td></tr>
                        {sec_mod_row}

                        <tr class="layer-header"><td colspan="2">PROCESOS SOSPECHOSOS Y MEMORIA</td></tr>
                        {procs_html}

                        <tr class="layer-header"><td colspan="2">PERSISTENCIA Y ARRANQUE AUTOMÁTICO</td></tr>
                        {persist_html}

                        <tr class="layer-header"><td colspan="2">RED, SOCKETS Y ARCHIVO HOSTS</td></tr>
                        <tr><td>Integridad de Archivo Hosts:</td><td>{hosts_pill}</td></tr>
                        {c2_html}

                        <tr class="layer-header"><td colspan="2">ARTEFACTOS Y STAGING EN CARPETAS TEMPORALES</td></tr>
                        {art_html}
                    </tbody>
                </table>
            </div>
            """

        # 2. OSI Stack Network Diagnostics
        osi = report.osi_network or (report.telemetry.get("osi_network") or report.telemetry.get("osi", {}) if isinstance(report.telemetry, dict) else {})
        osi_html = ""
        if osi and isinstance(osi, dict):
            l7 = osi.get("l7_application", {}) if isinstance(osi.get("l7_application"), dict) else {}
            l6_l5 = osi.get("l6_l5_session", {}) if isinstance(osi.get("l6_l5_session"), dict) else {}
            l4 = osi.get("l4_transport", {}) if isinstance(osi.get("l4_transport"), dict) else {}
            l3 = osi.get("l3_network", {}) if isinstance(osi.get("l3_network"), dict) else {}
            l2 = osi.get("l2_datalink", {}) if isinstance(osi.get("l2_datalink"), dict) else {}
            l1 = osi.get("l1_physical", {}) if isinstance(osi.get("l1_physical"), dict) else {}

            # L7 details
            dns_srv = l7.get("dns_servers")
            dns_srv_str = ", ".join(dns_srv) if isinstance(dns_srv, list) else str(dns_srv or "N/A")
            dns_ok = l7.get("dns_ok") or l7.get("dns_resolution_ok")
            dns_badge = '<span class="status-pill pill-ok">OK</span>' if dns_ok else '<span class="status-pill pill-crit">FAIL</span>'
            dns_ms = l7.get("dns_ms") or l7.get("dns_response_time_ms")
            dns_time_str = f" ({dns_ms} ms)" if dns_ms else ""

            captive = l7.get("captive_portal") or l7.get("captive_portal_detected")
            captive_alert = ""
            if captive:
                captive_alert = '<div class="alert-box alert-warn">⚠️ <strong>ALERTA PORTAL CAUTIVO:</strong> Redirección web detectada. Se requiere inicio de sesión en red.</div>'

            http_st = l7.get("http_status") or l7.get("http_outbound_status")
            http_str = f"Código {http_st}" if http_st else "N/A"

            # L6/L5 details
            dom_name = l6_l5.get("domain_name") or "N/A"
            tcp_conns = l6_l5.get("active_tcp_conns") or l6_l5.get("active_established_tcp_conns") or 0

            # L4 details
            p53 = l4.get("gateway_port53_open") or l4.get("gateway_dns_port_53_open")
            p443 = l4.get("internet_port443_open") or l4.get("internet_https_port_443_open")
            p53_str = '<span class="status-pill pill-ok">Abierto [✓]</span>' if p53 else '<span class="status-pill pill-warn">Cerrado/Timeout</span>'
            p443_str = '<span class="status-pill pill-ok">Abierto [✓]</span>' if p443 else '<span class="status-pill pill-warn">Cerrado/Timeout</span>'

            # L3 details
            ip_addr = l3.get("ip") or l3.get("ipv4_address") or "N/A"
            mask = l3.get("subnet") or "N/A"
            gw_ip = l3.get("gateway") or l3.get("default_gateway") or "N/A"
            gw_ping = l3.get("ping_gateway") or l3.get("gateway_ping_ok")
            gw_ping_badge = '<span class="status-pill pill-ok">OK</span>' if gw_ping else '<span class="status-pill pill-crit">FAIL</span>'
            ext_ping = l3.get("ping_internet") or l3.get("internet_ping_ok")
            ext_ping_badge = '<span class="status-pill pill-ok">OK</span>' if ext_ping else '<span class="status-pill pill-crit">FAIL</span>'
            mtu_val = l3.get("mtu") or 1500
            hops = l3.get("traceroute_hops") or []
            hops_str = ""
            if isinstance(hops, list):
                hops_items = []
                for h in hops:
                    if isinstance(h, list) and len(h) >= 2:
                        hops_items.append(f"#{h[0]}: {h[1]}")
                    elif isinstance(h, dict):
                        hops_items.append(f"#{h.get('hop')}: {h.get('ip')}")
                    else:
                        hops_items.append(str(h))
                hops_str = " → ".join(hops_items)
            elif isinstance(hops, str):
                hops_str = hops.replace(";", " → ")

            # L2 details
            adapter = l2.get("adapter") or l2.get("interface_name") or l2.get("interface") or "N/A"
            mac = l2.get("mac") or l2.get("mac_address") or "N/A"
            dhcp_on = l2.get("dhcp_enabled") or l2.get("is_dhcp")
            dhcp_str = "DHCP Automático" if dhcp_on else "IP Estática"
            dhcp_srv = l2.get("dhcp_server") or "N/A"
            arp_gw = l2.get("gateway_arp") or l2.get("gateway_mac_arp") or l2.get("gateway_mac_resolved_arp") or "N/A"

            wifi = l2.get("wifi", {}) if isinstance(l2.get("wifi"), dict) else {}
            wifi_html = ""
            if wifi and (wifi.get("is_wifi") or wifi.get("ssid")):
                w_ssid = html.escape(str(wifi.get("ssid") or "N/A"))
                w_sig = wifi.get("signal_pct") or wifi.get("signal") or "N/A"
                w_ch = wifi.get("channel") or "N/A"
                wifi_html = f"<tr><td><strong>📡 Enlace Wi-Fi:</strong></td><td>SSID: <strong>{w_ssid}</strong> | Señal: <strong>{w_sig}%</strong> | Canal: <strong>{w_ch}</strong></td></tr>"

            # L1 details
            link_spd = l1.get("link_speed") or l2.get("speed") or l2.get("link_speed") or "N/A"
            carrier = l1.get("carrier") or l1.get("link_carrier") or "1"
            oper_st = l1.get("status") or l1.get("operstate") or "Up"
            carrier_str = '<span class="status-pill pill-ok">Conectado [✓]</span>' if str(carrier) in ("1", "True", "true") else '<span class="status-pill pill-crit">Desconectado</span>'

            osi_html = f"""
            <div class="card osi-card">
                <div class="card-header">🌐 ANÁLISIS DE RED ESTRUCTURADO (MODELO OSI)</div>
                {captive_alert}
                <table>
                    <tbody>
                        <tr class="layer-header"><td colspan="2">CAPA 7: APLICACIÓN (DNS / HTTP)</td></tr>
                        <tr><td>Resolución DNS:</td><td>{dns_badge} Servidores: {dns_srv_str}{dns_time_str}</td></tr>
                        <tr><td>Salida Web (HTTP):</td><td>{http_str}</td></tr>

                        <tr class="layer-header"><td colspan="2">CAPAS 6 & 5: PRESENTACIÓN Y SESIÓN</td></tr>
                        <tr><td>Dominio / Red:</td><td>{dom_name} ({tcp_conns} sockets TCP activos)</td></tr>

                        <tr class="layer-header"><td colspan="2">CAPA 4: TRANSPORTE (SOCKETS TCP)</td></tr>
                        <tr><td>Puertos Clave:</td><td>Gateway 53: {p53_str} | Internet 443: {p443_str}</td></tr>

                        <tr class="layer-header"><td colspan="2">CAPA 3: RED (ENRUTAMIENTO, PING & TRACEROUTE)</td></tr>
                        <tr><td>Dirección IPv4:</td><td><strong>{ip_addr}</strong> (Máscara: {mask} | MTU: {mtu_val})</td></tr>
                        <tr><td>Puerta de Enlace:</td><td>{gw_ip} (Ping: {gw_ping_badge})</td></tr>
                        <tr><td>Internet (8.8.8.8):</td><td>Ping: {ext_ping_badge}</td></tr>
                        {f'<tr><td>Traceroute:</td><td><small>{hops_str}</small></td></tr>' if hops_str else ''}

                        <tr class="layer-header"><td colspan="2">CAPA 2: ENLACE DE DATOS (INTERFACES & ARP)</td></tr>
                        <tr><td>Adaptador / MAC:</td><td>{adapter} (<code>{mac}</code>)</td></tr>
                        <tr><td>Configuración IP:</td><td>{dhcp_str} (Servidor: {dhcp_srv})</td></tr>
                        <tr><td>Resolución ARP GW:</td><td><code>{arp_gw}</code></td></tr>
                        {wifi_html}

                        <tr class="layer-header"><td colspan="2">CAPA 1: FÍSICA (ENLACE Y MEDIO)</td></tr>
                        <tr><td>Estado de Enlace:</td><td>{oper_st} | Velocidad: {link_spd}</td></tr>
                        <tr><td>Detección Portadora:</td><td>{carrier_str}</td></tr>
                    </tbody>
                </table>
            </div>
            """

        # 3. AI section HTML
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

        # 4. Additional telemetry metrics cards
        telemetry_rows = ""
        if isinstance(report.telemetry, dict):
            for k, v in report.telemetry.items():
                if k in ("hardware", "hardware_audit", "malware_audit", "osi_network", "osi"):
                    continue
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
        .osi-card {{ border-color: #0284c7; }}
        .hw-card {{ border-color: #06b6d4; }}
        .malware-card {{ border-color: #ef4444; }}
        .card-header {{ font-weight: 700; margin-bottom: 0.5rem; font-size: 0.95rem; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
        td {{ padding: 0.5rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
        .layer-header {{ background: #1e293b; color: #38bdf8; font-weight: 800; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.5px; }}
        .status-pill {{ padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; font-weight: 700; display: inline-block; }}
        .pill-ok {{ background: #059669; color: #fff; }}
        .pill-warn {{ background: #d97706; color: #fff; }}
        .pill-crit {{ background: #dc2626; color: #fff; }}
        .alert-box {{ padding: 0.6rem 0.8rem; border-radius: 6px; margin-bottom: 0.75rem; font-size: 0.85rem; }}
        .alert-warn {{ background: #451a03; border: 1px solid #f59e0b; color: #fde68a; }}
        ul, ol {{ padding-left: 1.25rem; margin-top: 0.5rem; font-size: 0.85rem; }}
        li {{ margin-bottom: 0.4rem; }}
        code {{ background: #0f172a; padding: 2px 5px; border-radius: 4px; font-family: monospace; font-size: 0.8rem; color: #38bdf8; }}
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

    {hw_html}

    {hw_audit_html}

    {malware_audit_html}

    {osi_html}

    {ai_html}

    {f'''
    <div class="card">
        <div class="card-header">📊 TELEMETRÍA ADICIONAL</div>
        <table>
            <tbody>
                {telemetry_rows}
            </tbody>
        </table>
    </div>
    ''' if telemetry_rows else ''}

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
        hardware: Optional[Dict[str, Any]] = None,
        hardware_audit: Optional[Dict[str, Any]] = None,
        malware_audit: Optional[Dict[str, Any]] = None,
        osi_network: Optional[Dict[str, Any]] = None,
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
