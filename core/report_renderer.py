"""
REI - Renderizador HTML del Portal de Resultados (core/report_renderer.py)
Genera informes técnicos optimizados para móvil y escritorio sin dependencias
externas (100% funcional en redes locales aisladas), con iconografía SVG
vectorial, tema claro/oscuro y hoja de estilos de impresión.
"""

from __future__ import annotations

import datetime
import html
import json
from typing import Any, Dict, List, Optional, Tuple


_MONTHS_ES: Tuple[str, ...] = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)

_SVG_PATHS: Dict[str, str] = {
    "brand": (
        '<rect x="3" y="3" width="18" height="18" rx="4"/>'
        '<path d="M7 12h2.2l1.8-4 2.2 8 1.6-4H17"/>'
    ),
    "check": (
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="m9 12 2 2 4-4"/>'
    ),
    "warn": (
        '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
        '<line x1="12" y1="9" x2="12" y2="13"/>'
        '<line x1="12" y1="17" x2="12.01" y2="17"/>'
    ),
    "crit": (
        '<circle cx="12" cy="12" r="9"/>'
        '<line x1="15" y1="9" x2="9" y2="15"/>'
        '<line x1="9" y1="9" x2="15" y2="15"/>'
    ),
    "info": (
        '<circle cx="12" cy="12" r="9"/>'
        '<line x1="12" y1="16" x2="12" y2="12"/>'
        '<line x1="12" y1="8" x2="12.01" y2="8"/>'
    ),
    "diagnosis": (
        '<path d="M3 12h4l2.5-6 4.5 12 2.5-6H21"/>'
    ),
    "layers": (
        '<polygon points="12 2 2 7 12 12 22 7 12 2"/>'
        '<polyline points="2 12 12 17 22 12"/>'
        '<polyline points="2 17 12 22 22 17"/>'
    ),
    "cpu": (
        '<rect x="6" y="6" width="12" height="12" rx="2"/>'
        '<rect x="9.5" y="9.5" width="5" height="5" rx="0.5"/>'
        '<path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>'
    ),
    "memory": (
        '<rect x="3" y="7" width="18" height="10" rx="1.5"/>'
        '<path d="M7 11v2M11 11v2M15 11v2M7 17v2M12 17v2M17 17v2"/>'
    ),
    "drive": (
        '<rect x="3" y="5" width="18" height="14" rx="2"/>'
        '<line x1="3" y1="13" x2="21" y2="13"/>'
        '<circle cx="17" cy="16" r="0.8" fill="currentColor" stroke="none"/>'
        '<line x1="7" y1="16" x2="11" y2="16"/>'
    ),
    "monitor": (
        '<rect x="2" y="3" width="20" height="14" rx="2"/>'
        '<line x1="8" y1="21" x2="16" y2="21"/>'
        '<line x1="12" y1="17" x2="12" y2="21"/>'
    ),
    "battery": (
        '<rect x="2" y="7" width="17" height="10" rx="2"/>'
        '<line x1="22" y1="10.5" x2="22" y2="13.5"/>'
        '<line x1="6" y1="10" x2="6" y2="14"/>'
        '<line x1="10" y1="10" x2="10" y2="14"/>'
    ),
    "thermometer": (
        '<path d="M14 14.76V5a2 2 0 0 0-4 0v9.76a4 4 0 1 0 4 0z"/>'
    ),
    "shield": (
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
        '<path d="m9 12 2 2 4-4"/>'
    ),
    "shield-alert": (
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
        '<line x1="12" y1="8.5" x2="12" y2="12.5"/>'
        '<line x1="12" y1="16" x2="12.01" y2="16"/>'
    ),
    "wifi": (
        '<path d="M5 12.55a11 11 0 0 1 14.08 0"/>'
        '<path d="M8.53 16.11a6 6 0 0 1 6.95 0"/>'
        '<circle cx="12" cy="19.5" r="0.9" fill="currentColor" stroke="none"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="9"/>'
        '<line x1="3" y1="12" x2="21" y2="12"/>'
        '<path d="M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'
    ),
    "terminal": (
        '<polyline points="4 17 10 11 4 5"/>'
        '<line x1="12" y1="19" x2="20" y2="19"/>'
    ),
    "clock": (
        '<circle cx="12" cy="12" r="9"/>'
        '<polyline points="12 7 12 12 15.5 14"/>'
    ),
    "file-code": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<polyline points="14 2 14 8 20 8"/>'
        '<polyline points="10 13 8 15 10 17"/>'
        '<polyline points="14 13 16 15 14 17"/>'
    ),
    "printer": (
        '<polyline points="6 9 6 2 18 2 18 9"/>'
        '<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>'
        '<rect x="6" y="14" width="12" height="8"/>'
    ),
    "copy": (
        '<rect x="9" y="9" width="12" height="12" rx="2"/>'
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'
    ),
    "sun": (
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>'
    ),
    "moon": (
        '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
    ),
    "chevron": (
        '<polyline points="6 9 12 15 18 9"/>'
    ),
    "inbox": (
        '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/>'
        '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'
    ),
    "search-x": (
        '<circle cx="11" cy="11" r="7"/>'
        '<line x1="21" y1="21" x2="16.65" y2="16.65"/>'
        '<line x1="8.5" y1="8.5" x2="13.5" y2="13.5"/>'
        '<line x1="13.5" y1="8.5" x2="8.5" y2="13.5"/>'
    ),
    "arrow-left": (
        '<line x1="19" y1="12" x2="5" y2="12"/>'
        '<polyline points="12 19 5 12 12 5"/>'
    ),
}


def svg_icon(name: str, size: int = 16, extra_class: str = "") -> str:
    """Devuelve un icono SVG vectorial inline sin dependencias externas."""
    paths = _SVG_PATHS.get(name, _SVG_PATHS["info"])
    cls = f' class="icon {extra_class}"' if extra_class else ' class="icon"'
    return (
        f'<svg{cls} width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="currentColor" stroke-width="1.75" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{paths}</svg>"
    )


def _esc(value: Any) -> str:
    """Escapa texto de forma segura para HTML."""
    return html.escape(str(value))


def _safe_float(value: Any) -> Optional[float]:
    """Convierte un valor numérico (o cadena con %) a float si es posible."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            cleaned = value.strip().rstrip("%").replace(",", ".")
            return float(cleaned)
        return float(value)
    except (ValueError, TypeError):
        return None


def _clamp_pct(value: Optional[float]) -> float:
    """Acota un porcentaje al rango [0.0, 100.0]."""
    if value is None:
        return 0.0
    return max(0.0, min(100.0, value))


def _format_timestamp(ts: float) -> str:
    """Formatea un timestamp Unix a fecha legible en español."""
    dt = datetime.datetime.fromtimestamp(ts)
    month = _MONTHS_ES[dt.month - 1]
    return f"{dt.day:02d} {month} {dt.year}, {dt.strftime('%H:%M:%S')}"


def _status_meta(raw_status: Any) -> Tuple[str, str, str]:
    """Mapea el estado crudo del informe a (tono_css, etiqueta_es, icono)."""
    token = str(raw_status or "OK").strip().upper()
    if token in ("OK", "PASS", "HEALTHY", "BAJO"):
        return ("ok", "Operativo", "check")
    if token in ("WARN", "WARNING", "MEDIO", "ALERTA"):
        return ("warn", "Atención requerida", "warn")
    if token in ("CRIT", "CRITICAL", "CRITICO", "CRÍTICO", "FAIL", "ERROR", "ALTO"):
        return ("crit", "Estado crítico", "crit")
    return ("info", str(raw_status or "Informativo"), "info")


def _format_category(raw_category: str) -> str:
    """Convierte la categoría en etiqueta legible con capitalización natural."""
    mapping = {
        "COMPLETO": "Auditoría completa",
        "RED": "Diagnóstico de red",
        "HARDWARE": "Auditoría de hardware",
        "MALWARE": "Seguridad y amenazas",
        "LOGS": "Registros del sistema",
        "GENERAL": "Diagnóstico general",
    }
    key = str(raw_category or "GENERAL").strip().upper()
    return mapping.get(key, key.capitalize())


def _format_os(raw_os: str) -> str:
    """Normaliza la etiqueta del sistema operativo."""
    key = str(raw_os or "Desconocido").strip().upper()
    if key == "WINDOWS":
        return "Windows"
    if key == "LINUX":
        return "Linux"
    return str(raw_os or "Desconocido")


def _pill(text: str, tone: str = "neutral", icon: Optional[str] = None) -> str:
    """Genera una insignia de estado compacta con icono opcional."""
    ico = svg_icon(icon, 13) if icon else ""
    return f'<span class="pill pill-{tone}">{ico}<span>{_esc(text)}</span></span>'


def _copy_code(value: Any, empty_label: str = "No disponible") -> str:
    """Muestra un identificador técnico en monoespaciado con botón de copia rápida."""
    raw = str(value).strip() if value is not None else ""
    if not raw or raw.upper() in ("N/A", "NONE", "NULL"):
        return f'<span class="muted">{_esc(empty_label)}</span>'
    escaped = _esc(raw)
    return (
        f'<button type="button" class="code-copy" data-copy="{escaped}" '
        f'title="Copiar valor" aria-label="Copiar {escaped}">'
        f'<code>{escaped}</code>'
        f'<span class="copy-glyph">{svg_icon("copy", 13)}</span>'
        f"</button>"
    )


def _meter(pct: Optional[float], tone: str = "brand") -> str:
    """Genera una barra medidora horizontal limpia."""
    width = _clamp_pct(pct)
    return (
        f'<div class="meter" role="progressbar" aria-valuenow="{width:.0f}" '
        f'aria-valuemin="0" aria-valuemax="100">'
        f'<span class="meter-fill meter-{tone}" style="width:{width:.1f}%"></span>'
        f"</div>"
    )


def _kv_row(label: str, value_html: str) -> str:
    """Genera una fila clave-valor de lectura cómoda."""
    return (
        '<div class="kv-row">'
        f'<dt class="kv-key">{_esc(label)}</dt>'
        f'<dd class="kv-val">{value_html}</dd>'
        "</div>"
    )


def _extract_section_dict(report: Any, attr_name: str, *telemetry_keys: str) -> Dict[str, Any]:
    """Obtiene un sub-diccionario de reporte tolerando estructura plana o anidada."""
    direct = getattr(report, attr_name, None)
    if isinstance(direct, dict) and direct:
        return direct
    telemetry = getattr(report, "telemetry", None)
    if isinstance(telemetry, dict):
        for key in telemetry_keys:
            candidate = telemetry.get(key)
            if isinstance(candidate, dict) and candidate:
                return candidate
    return {}


def _build_vitals(
    hw_audit: Dict[str, Any],
    osi: Dict[str, Any],
    malware_audit: Dict[str, Any],
    telemetry: Dict[str, Any],
) -> List[str]:
    """Construye hasta 4 indicadores vitales clave según los datos reales del informe."""
    tiles: List[str] = []

    # 1. CPU
    cpu_info = hw_audit.get("cpu", {}) if isinstance(hw_audit.get("cpu"), dict) else {}
    cpu_pct = _safe_float(cpu_info.get("load_pct"))
    if cpu_pct is None and isinstance(telemetry, dict):
        cpu_pct = _safe_float(telemetry.get("cpu_percent"))
    if cpu_pct is not None:
        tone = "crit" if cpu_pct > 90 else ("warn" if cpu_pct > 75 else "ok")
        temp = _safe_float(cpu_info.get("temp_c"))
        sub_parts: List[str] = []
        if cpu_info.get("cores"):
            threads = cpu_info.get("threads")
            sub_parts.append(
                f"{cpu_info['cores']} núcleos / {threads} hilos"
                if threads
                else f"{cpu_info['cores']} núcleos"
            )
        if temp is not None and temp > 0:
            sub_parts.append(f"{temp:g} °C")
        sub = " — ".join(sub_parts) if sub_parts else "Carga instantánea"
        tiles.append(
            '<div class="vital-tile">'
            f'<div class="vital-head"><span class="vital-label">{svg_icon("cpu", 15)} Procesador</span>'
            f'<span class="vital-num tone-{tone}">{cpu_pct:g}%</span></div>'
            f"{_meter(cpu_pct, tone)}"
            f'<div class="vital-sub">{_esc(sub)}</div>'
            "</div>"
        )

    # 2. Memoria RAM
    mem_info = hw_audit.get("memory", {}) if isinstance(hw_audit.get("memory"), dict) else {}
    mem_pct = _safe_float(mem_info.get("usage_pct"))
    if mem_pct is None and isinstance(telemetry, dict):
        mem_pct = _safe_float(telemetry.get("ram_percent"))
    if mem_pct is not None:
        tone = "crit" if mem_pct > 90 else ("warn" if mem_pct > 80 else "ok")
        m_used = mem_info.get("used_gb")
        m_tot = mem_info.get("total_gb")
        sub = (
            f"{m_used} GB de {m_tot} GB en uso"
            if m_used is not None and m_tot is not None
            else "Uso de memoria física"
        )
        tiles.append(
            '<div class="vital-tile">'
            f'<div class="vital-head"><span class="vital-label">{svg_icon("memory", 15)} Memoria RAM</span>'
            f'<span class="vital-num tone-{tone}">{mem_pct:g}%</span></div>'
            f"{_meter(mem_pct, tone)}"
            f'<div class="vital-sub">{_esc(sub)}</div>'
            "</div>"
        )

    # 3. Conectividad / Red
    l3 = osi.get("l3_network", {}) if isinstance(osi.get("l3_network"), dict) else {}
    l7 = osi.get("l7_application", {}) if isinstance(osi.get("l7_application"), dict) else {}
    ip_addr = l3.get("ip") or l3.get("ipv4_address") or (telemetry.get("ip") if isinstance(telemetry, dict) else None)
    gw_ok = l3.get("ping_gateway") if "ping_gateway" in l3 else l3.get("gateway_ping_ok")
    if gw_ok is None and isinstance(telemetry, dict):
        gw_ok = telemetry.get("ping_gateway")
    ext_ok = l3.get("ping_internet") if "ping_internet" in l3 else l3.get("internet_ping_ok")
    if ext_ok is None and isinstance(telemetry, dict):
        ext_ok = telemetry.get("ping_internet")
    dns_ok = l7.get("dns_ok") if "dns_ok" in l7 else l7.get("dns_resolution_ok")

    if ip_addr or gw_ok is not None or ext_ok is not None:
        if gw_ok is False:
            net_state, net_tone = "Sin puerta de enlace", "crit"
        elif ext_ok is False:
            net_state, net_tone = "Solo red local", "warn"
        elif dns_ok is False:
            net_state, net_tone = "Fallo de DNS", "warn"
        elif ext_ok is True or gw_ok is True:
            net_state, net_tone = "Conectada", "ok"
        else:
            net_state, net_tone = "Detectada", "info"

        ip_display = f'<code class="vital-code">{_esc(ip_addr)}</code>' if ip_addr else "Dirección IP no reportada"
        tiles.append(
            '<div class="vital-tile">'
            f'<div class="vital-head"><span class="vital-label">{svg_icon("globe", 15)} Red</span>'
            f'{_pill(net_state, net_tone)}</div>'
            f'<div class="vital-primary">{ip_display}</div>'
            f'<div class="vital-sub">{_esc("Salida externa verificada" if ext_ok else ("Enlace local activo" if gw_ok else "Revise pila OSI"))}</div>'
            "</div>"
        )

    # 4. Seguridad / Amenazas
    if malware_audit and any(
        k in malware_audit
        for k in ("threat_level", "threat_score", "defenses", "suspicious_processes")
    ):
        th_raw = str(malware_audit.get("threat_level") or "BAJO").upper()
        th_score = malware_audit.get("threat_score", 0)
        if th_raw in ("CRITICO", "CRÍTICO", "ALTO", "CRITICAL"):
            th_label, th_tone, th_ico = "Riesgo crítico", "crit", "shield-alert"
        elif th_raw in ("MEDIO", "WARN", "WARNING"):
            th_label, th_tone, th_ico = "Riesgo medio", "warn", "shield-alert"
        else:
            th_label, th_tone, th_ico = "Riesgo bajo", "ok", "shield"
        susp_count = len(malware_audit.get("suspicious_processes") or [])
        sub = (
            f"{susp_count} proceso(s) anómalo(s)"
            if susp_count > 0
            else "Sin procesos sospechosos activos"
        )
        tiles.append(
            '<div class="vital-tile">'
            f'<div class="vital-head"><span class="vital-label">{svg_icon(th_ico, 15)} Seguridad</span>'
            f'{_pill(th_label, th_tone)}</div>'
            f'<div class="vital-primary"><span class="vital-score">{_esc(th_score)}</span> <span class="vital-unit">pts heurísticos</span></div>'
            f'<div class="vital-sub">{_esc(sub)}</div>'
            "</div>"
        )

    # 5. Si solo es un reporte de red y faltan fichas, añadimos latencia DNS y enlace físico
    if len(tiles) < 3 and osi:
        dns_ms = l7.get("dns_ms") or l7.get("dns_response_time_ms")
        if dns_ms is not None or dns_ok is not None:
            d_tone = "ok" if dns_ok else "crit"
            d_val = f"{dns_ms} ms" if dns_ms is not None else ("Resuelve" if dns_ok else "Sin respuesta")
            tiles.append(
                '<div class="vital-tile">'
                f'<div class="vital-head"><span class="vital-label">{svg_icon("layers", 15)} Resolución DNS</span>'
                f'{_pill("Activa" if dns_ok else "Fallida", d_tone)}</div>'
                f'<div class="vital-primary">{_esc(d_val)}</div>'
                '<div class="vital-sub">Capa 7 de aplicación</div>'
                "</div>"
            )

        l1 = osi.get("l1_physical", {}) if isinstance(osi.get("l1_physical"), dict) else {}
        l2 = osi.get("l2_datalink", {}) if isinstance(osi.get("l2_datalink"), dict) else {}
        spd = l1.get("link_speed") or l2.get("speed") or l2.get("link_speed")
        carrier = l1.get("carrier") if "carrier" in l1 else l1.get("link_carrier", "1")
        carrier_ok = str(carrier).lower() in ("1", "true", "up")
        if spd or l1:
            tiles.append(
                '<div class="vital-tile">'
                f'<div class="vital-head"><span class="vital-label">{svg_icon("wifi", 15)} Enlace físico</span>'
                f'{_pill("Portadora activa" if carrier_ok else "Desconectado", "ok" if carrier_ok else "crit")}</div>'
                f'<div class="vital-primary">{_esc(spd or "Enlace estándar")}</div>'
                f'<div class="vital-sub">{_esc(l2.get("adapter") or l2.get("interface_name") or l2.get("interface") or "Interfaz de red")}</div>'
                "</div>"
            )

    return tiles[:4]


def _render_ai_section(ai_analysis: Optional[Dict[str, Any]], overall_status: str) -> str:
    """Renderiza el bloque ejecutivo de diagnóstico y plan de acción."""
    if not ai_analysis or not isinstance(ai_analysis, dict):
        return ""

    tone, _, _ = _status_meta(ai_analysis.get("overall_status") or overall_status)
    summary = _esc(ai_analysis.get("summary") or "Sin síntesis disponible.")
    causes = ai_analysis.get("root_causes") if isinstance(ai_analysis.get("root_causes"), list) else []
    plan = ai_analysis.get("action_plan") if isinstance(ai_analysis.get("action_plan"), list) else []

    if causes:
        causes_html = "".join(
            f'<li><span class="list-icon tone-{tone}">{svg_icon("warn" if tone != "ok" else "check", 15)}</span>'
            f"<span>{_esc(c)}</span></li>"
            for c in causes
        )
    else:
        causes_html = (
            f'<li><span class="list-icon tone-ok">{svg_icon("check", 15)}</span>'
            "<span>No se identificaron anomalías críticas en la telemetría recolectada.</span></li>"
        )

    if plan:
        plan_html = "".join(
            f'<li><span class="step-num">{idx}</span><span>{_esc(step)}</span></li>'
            for idx, step in enumerate(plan, start=1)
        )
    else:
        plan_html = (
            '<li><span class="step-num">1</span>'
            "<span>No se requieren acciones correctivas inmediatas.</span></li>"
        )

    return f"""
    <section id="sec-diagnostico" class="section diagnosis-section diagnosis-{tone}">
        <div class="section-head">
            <div class="section-title-group">
                <span class="section-icon">{svg_icon("diagnosis", 18)}</span>
                <div>
                    <h2>Diagnóstico y plan de acción</h2>
                    <p class="section-desc">Síntesis técnica generada a partir de la telemetría del equipo</p>
                </div>
            </div>
        </div>
        <p class="diagnosis-summary">{summary}</p>
        <div class="diagnosis-grid">
            <div class="diagnosis-col">
                <h3>Hallazgos y causas raíz</h3>
                <ul class="finding-list">{causes_html}</ul>
            </div>
            <div class="diagnosis-col">
                <h3>Pasos recomendados</h3>
                <ol class="step-list">{plan_html}</ol>
            </div>
        </div>
    </section>
    """


def _render_osi_section(osi: Dict[str, Any]) -> str:
    """Renderiza la escalera vertical de diagnóstico OSI (Capa 7 a Capa 1)."""
    if not osi:
        return ""

    l7 = osi.get("l7_application", {}) if isinstance(osi.get("l7_application"), dict) else {}
    l6_l5 = osi.get("l6_l5_session", {}) if isinstance(osi.get("l6_l5_session"), dict) else {}
    l4 = osi.get("l4_transport", {}) if isinstance(osi.get("l4_transport"), dict) else {}
    l3 = osi.get("l3_network", {}) if isinstance(osi.get("l3_network"), dict) else {}
    l2 = osi.get("l2_datalink", {}) if isinstance(osi.get("l2_datalink"), dict) else {}
    l1 = osi.get("l1_physical", {}) if isinstance(osi.get("l1_physical"), dict) else {}

    # --- Capa 7: Aplicación ---
    dns_srv = l7.get("dns_servers")
    if isinstance(dns_srv, list):
        dns_srv_html = " ".join(_copy_code(s) for s in dns_srv) if dns_srv else '<span class="muted">No reportados</span>'
    else:
        dns_srv_html = _copy_code(dns_srv)
    dns_ok = bool(l7.get("dns_ok") or l7.get("dns_resolution_ok"))
    dns_ms = l7.get("dns_ms") or l7.get("dns_response_time_ms")
    dns_time = f' <span class="muted">({ _esc(dns_ms) } ms)</span>' if dns_ms is not None else ""

    captive = bool(l7.get("captive_portal") or l7.get("captive_portal_detected"))
    captive_banner = ""
    if captive:
        captive_banner = (
            '<div class="callout callout-warn">'
            f'<span class="callout-icon">{svg_icon("warn", 18)}</span>'
            "<div><strong>Portal cautivo detectado</strong>"
            "<p>El tráfico saliente está siendo redirigido. Inicia sesión en la red desde un navegador antes de repetir las pruebas externas.</p></div>"
            "</div>"
        )

    http_st = l7.get("http_status") or l7.get("http_outbound_status")
    http_num = _safe_float(http_st)
    if http_num is not None:
        http_tone = "ok" if 200 <= http_num < 400 else "warn"
        http_html = _pill(f"HTTP {int(http_num)}", http_tone)
    elif http_st:
        http_html = _esc(http_st)
    else:
        http_html = '<span class="muted">Sin prueba HTTP</span>'

    if not dns_ok:
        l7_tone, l7_label = "crit", "Fallo DNS"
    elif captive or (http_num is not None and http_num >= 400):
        l7_tone, l7_label = "warn", "Portal cautivo" if captive else "Restricción HTTP"
    else:
        l7_tone, l7_label = "ok", "Operativa"

    # --- Capas 6 y 5: Presentación y Sesión ---
    dom_name = l6_l5.get("domain_name") or "No reportado"
    tcp_conns = l6_l5.get("active_tcp_conns")
    if tcp_conns is None:
        tcp_conns = l6_l5.get("active_established_tcp_conns", 0)
    l65_tone, l65_label = "neutral", f"{tcp_conns} sockets"

    # --- Capa 4: Transporte ---
    p53 = bool(l4.get("gateway_port53_open") or l4.get("gateway_dns_port_53_open"))
    p443 = bool(l4.get("internet_port443_open") or l4.get("internet_https_port_443_open"))
    if p53 and p443:
        l4_tone, l4_label = "ok", "Puertos abiertos"
    elif p53 or p443:
        l4_tone, l4_label = "warn", "Acceso parcial"
    else:
        l4_tone, l4_label = "crit", "Bloqueado"

    # --- Capa 3: Red ---
    ip_addr = l3.get("ip") or l3.get("ipv4_address")
    mask = l3.get("subnet") or "No reportada"
    gw_ip = l3.get("gateway") or l3.get("default_gateway")
    gw_ping = bool(l3.get("ping_gateway") or l3.get("gateway_ping_ok"))
    ext_ping = bool(l3.get("ping_internet") or l3.get("internet_ping_ok"))
    mtu_val = l3.get("mtu") or 1500

    hops = l3.get("traceroute_hops") or []
    hop_chips: List[str] = []
    if isinstance(hops, list):
        for h in hops:
            if isinstance(h, list) and len(h) >= 2:
                hop_chips.append(f'<span class="hop-chip"><b>{h[0]}</b> {_esc(h[1])}</span>')
            elif isinstance(h, dict):
                hop_chips.append(f'<span class="hop-chip"><b>{_esc(h.get("hop", "?"))}</b> {_esc(h.get("ip", "*"))}</span>')
            else:
                hop_chips.append(f'<span class="hop-chip">{_esc(h)}</span>')
    elif isinstance(hops, str) and hops.strip():
        for idx, part in enumerate(hops.split(";"), start=1):
            hop_chips.append(f'<span class="hop-chip"><b>{idx}</b> {_esc(part.strip())}</span>')
    hops_html = f'<div class="hop-trail">{"".join(hop_chips)}</div>' if hop_chips else ""

    if not gw_ping:
        l3_tone, l3_label = "crit", "Sin puerta de enlace"
    elif not ext_ping:
        l3_tone, l3_label = "warn", "Sin salida externa"
    else:
        l3_tone, l3_label = "ok", "Enrutamiento OK"

    # --- Capa 2: Enlace de datos ---
    adapter = l2.get("adapter") or l2.get("interface_name") or l2.get("interface") or "No reportado"
    mac = l2.get("mac") or l2.get("mac_address")
    dhcp_raw = l2.get("dhcp_enabled") if "dhcp_enabled" in l2 else l2.get("is_dhcp")
    dhcp_str = "DHCP automático" if dhcp_raw else ("IP estática" if dhcp_raw is False else "No reportado")
    dhcp_srv = l2.get("dhcp_server")
    arp_gw = l2.get("gateway_arp") or l2.get("gateway_mac_arp") or l2.get("gateway_mac_resolved_arp")

    wifi = l2.get("wifi", {}) if isinstance(l2.get("wifi"), dict) else {}
    wifi_ssid = wifi.get("ssid") or l2.get("wifi_ssid")
    wifi_sig = wifi.get("signal_pct") or wifi.get("signal") or l2.get("wifi_signal")
    wifi_ch = wifi.get("channel") or l2.get("wifi_channel")
    wifi_sig_num = _safe_float(wifi_sig)

    wifi_row = ""
    if wifi_ssid or wifi.get("is_wifi"):
        sig_badge = ""
        if wifi_sig_num is not None:
            sig_tone = "crit" if wifi_sig_num < 30 else ("warn" if wifi_sig_num < 50 else "ok")
            sig_badge = f" {_pill(f'{wifi_sig_num:g}%', sig_tone, 'wifi')}"
        ch_part = f' <span class="muted">Canal {_esc(wifi_ch)}</span>' if wifi_ch else ""
        wifi_row = _kv_row("Enlace Wi-Fi", f"<strong>{_esc(wifi_ssid or 'Red inalámbrica')}</strong>{sig_badge}{ch_part}")

    if wifi_sig_num is not None and wifi_sig_num < 40:
        l2_tone, l2_label = "warn", "Señal Wi-Fi baja"
    elif arp_gw and str(arp_gw).upper() != "N/A":
        l2_tone, l2_label = "ok", "ARP resuelto"
    else:
        l2_tone, l2_label = "neutral", "Verificada"

    # --- Capa 1: Física ---
    link_spd = l1.get("link_speed") or l2.get("speed") or l2.get("link_speed") or "No reportada"
    carrier = l1.get("carrier") if "carrier" in l1 else l1.get("link_carrier", "1")
    oper_st = l1.get("status") or l1.get("operstate") or "Up"
    carrier_ok = str(carrier).lower() in ("1", "true", "up")
    l1_tone = "ok" if carrier_ok else "crit"
    l1_label = "Portadora activa" if carrier_ok else "Sin portadora"

    def _layer_block(
        code: str,
        title: str,
        tone: str,
        badge_text: str,
        rows_html: str,
    ) -> str:
        return f"""
        <div class="osi-layer osi-{tone}">
            <div class="osi-rail" aria-hidden="true">
                <span class="osi-tag">{_esc(code)}</span>
                <span class="osi-line"></span>
            </div>
            <div class="osi-body">
                <div class="osi-head">
                    <h3>{_esc(title)}</h3>
                    {_pill(badge_text, tone)}
                </div>
                <dl class="kv-grid">{rows_html}</dl>
            </div>
        </div>
        """

    layers_html = "".join(
        [
            _layer_block(
                "L7",
                "Aplicación — DNS y salida HTTP",
                l7_tone,
                l7_label,
                _kv_row("Resolución DNS", f"{_pill('Correcta' if dns_ok else 'Fallida', 'ok' if dns_ok else 'crit', 'check' if dns_ok else 'crit')}{dns_time}")
                + _kv_row("Servidores DNS", dns_srv_html)
                + _kv_row("Prueba HTTP saliente", http_html),
            ),
            _layer_block(
                "L6–5",
                "Presentación y sesión",
                l65_tone,
                l65_label,
                _kv_row("Dominio o grupo", _esc(dom_name))
                + _kv_row("Sockets TCP activos", f"<strong>{_esc(tcp_conns)}</strong> conexiones establecidas"),
            ),
            _layer_block(
                "L4",
                "Transporte — Puertos de servicio",
                l4_tone,
                l4_label,
                _kv_row(
                    "Puerto 53 (DNS Gateway)",
                    _pill("Abierto", "ok", "check") if p53 else _pill("Cerrado o sin respuesta", "warn", "warn"),
                )
                + _kv_row(
                    "Puerto 443 (HTTPS Internet)",
                    _pill("Abierto", "ok", "check") if p443 else _pill("Cerrado o sin respuesta", "warn", "warn"),
                ),
            ),
            _layer_block(
                "L3",
                "Red — Direccionamiento y enrutamiento",
                l3_tone,
                l3_label,
                _kv_row("Dirección IPv4", f'{_copy_code(ip_addr)} <span class="muted">Máscara {_esc(mask)} / MTU {_esc(mtu_val)}</span>')
                + _kv_row(
                    "Puerta de enlace",
                    f'{_copy_code(gw_ip)} {_pill("Ping OK" if gw_ping else "Sin respuesta", "ok" if gw_ping else "crit")}',
                )
                + _kv_row(
                    "Conectividad Internet (8.8.8.8)",
                    _pill("Accesible", "ok", "check") if ext_ping else _pill("Sin salida", "crit", "crit"),
                )
                + (_kv_row("Ruta (Traceroute)", hops_html) if hops_html else ""),
            ),
            _layer_block(
                "L2",
                "Enlace de datos — Interfaz y tabla ARP",
                l2_tone,
                l2_label,
                _kv_row("Adaptador y MAC", f"<strong>{_esc(adapter)}</strong> {_copy_code(mac)}")
                + _kv_row(
                    "Asignación IP",
                    f"{_esc(dhcp_str)}" + (f' <span class="muted">Servidor:</span> {_copy_code(dhcp_srv)}' if dhcp_srv and str(dhcp_srv).upper() != "N/A" else ""),
                )
                + _kv_row("MAC puerta de enlace (ARP)", _copy_code(arp_gw))
                + wifi_row,
            ),
            _layer_block(
                "L1",
                "Física — Enlace y medio",
                l1_tone,
                l1_label,
                _kv_row("Estado operativo", f"<strong>{_esc(oper_st)}</strong> <span class=\"muted\">Velocidad: {_esc(link_spd)}</span>")
                + _kv_row(
                    "Detección de portadora",
                    _pill("Conectado", "ok", "check") if carrier_ok else _pill("Desconectado", "crit", "crit"),
                ),
            ),
        ]
    )

    return f"""
    <section id="sec-red" class="section">
        <div class="section-head">
            <div class="section-title-group">
                <span class="section-icon">{svg_icon("layers", 18)}</span>
                <div>
                    <h2>Pila de red (Modelo OSI)</h2>
                    <p class="section-desc">Inspección descendente de Capa 7 a Capa 1 para aislar el punto exacto de falla</p>
                </div>
            </div>
        </div>
        {captive_banner}
        <div class="osi-ladder">
            {layers_html}
        </div>
    </section>
    """


def _render_hardware_section(hw: Dict[str, Any], hw_audit: Dict[str, Any], os_type: str) -> str:
    """Renderiza la identidad del equipo y la auditoría profunda de subsistemas."""
    has_hw = bool(hw)
    has_audit = bool(
        hw_audit
        and any(k in hw_audit for k in ("system", "cpu", "memory", "storage", "graphics", "battery"))
    )
    if not has_hw and not has_audit:
        return ""

    sys_info = hw_audit.get("system", {}) if isinstance(hw_audit.get("system"), dict) else {}
    cpu_info = hw_audit.get("cpu", {}) if isinstance(hw_audit.get("cpu"), dict) else {}
    mem_info = hw_audit.get("memory", {}) if isinstance(hw_audit.get("memory"), dict) else {}
    storage_info = hw_audit.get("storage", {}) if isinstance(hw_audit.get("storage"), dict) else {}
    graphics_info = hw_audit.get("graphics", []) if isinstance(hw_audit.get("graphics"), list) else []
    battery_info = hw_audit.get("battery") if isinstance(hw_audit.get("battery"), dict) else None

    # Identidad unificada (prioriza hardware_audit.system y completa con hardware)
    mfr = sys_info.get("manufacturer") or hw.get("manufacturer") or hw.get("mfr") or ""
    model = sys_info.get("model") or hw.get("model") or ""
    equip_str = f"{mfr} {model}".strip() or "Equipo no identificado"
    serial = sys_info.get("serial") or hw.get("serial") or hw.get("serial_number")
    os_name = sys_info.get("os_name") or hw.get("os_name") or hw.get("os") or _format_os(os_type)
    os_ver = sys_info.get("os_build") or sys_info.get("kernel") or hw.get("os_version") or hw.get("ver") or hw.get("kernel") or hw.get("os_build") or ""
    os_full = f"{os_name} {os_ver}".strip()

    b_mfr = sys_info.get("board_mfr") or ""
    b_prod = sys_info.get("board_product") or ""
    board_str = f"{b_mfr} {b_prod}".strip()
    bios_v = sys_info.get("bios_version")
    bios_d = sys_info.get("bios_date")
    bios_str = f"{bios_v} ({bios_d})" if bios_v and bios_d else (str(bios_v) if bios_v else "")

    identity_rows = [
        _kv_row("Equipo", f"<strong>{_esc(equip_str)}</strong>"),
        _kv_row("Número de serie", _copy_code(serial)),
        _kv_row("Sistema operativo", _esc(os_full)),
    ]
    if board_str:
        identity_rows.append(_kv_row("Placa base", _esc(board_str)))
    if bios_str:
        identity_rows.append(_kv_row("Firmware BIOS / UEFI", f"<code>{_esc(bios_str)}</code>"))

    if not has_audit:
        # Solo hardware resumido disponible
        cpu_m = hw.get("cpu_model") or hw.get("cpu") or "No reportado"
        ram_tot = hw.get("ram_total_gb") or hw.get("ram_gb")
        ram_free = hw.get("ram_free_gb")
        ram_str = (
            f"{ram_tot} GB totales ({ram_free} GB libres)"
            if ram_tot is not None and ram_free is not None
            else (f"{ram_tot} GB" if ram_tot is not None else "No reportada")
        )
        identity_rows.append(_kv_row("Procesador", _esc(cpu_m)))
        identity_rows.append(_kv_row("Memoria RAM", _esc(ram_str)))

    if battery_info and battery_info.get("present"):
        ch = _safe_float(battery_info.get("charge_pct")) or 0.0
        st = str(battery_info.get("status") or "Conectada")
        b_tone = "ok" if ch > 30 else ("warn" if ch > 15 else "crit")
        identity_rows.append(
            _kv_row(
                "Batería",
                f'<div class="inline-meter">{_pill(f"{ch:g}%", b_tone, "battery")} <span class="muted">{_esc(st)}</span></div>',
            )
        )

    subsystems_html = ""
    if has_audit:
        # CPU
        c_name = cpu_info.get("name") or hw.get("cpu_model") or "No reportado"
        c_cores = cpu_info.get("cores")
        c_threads = cpu_info.get("threads")
        c_load = _safe_float(cpu_info.get("load_pct")) or 0.0
        c_tone = "crit" if c_load > 90 else ("warn" if c_load > 75 else "ok")
        c_mhz = cpu_info.get("current_mhz") or cpu_info.get("max_mhz")
        c_max = cpu_info.get("max_mhz")
        c_freq_parts: List[str] = []
        if c_cores is not None:
            c_freq_parts.append(f"{c_cores} núcleos" + (f" / {c_threads} hilos" if c_threads else ""))
        if c_mhz:
            if c_max and c_max != c_mhz:
                c_freq_parts.append(f"{c_mhz} MHz (máx. {c_max} MHz)")
            else:
                c_freq_parts.append(f"{c_mhz} MHz")

        c_temp = _safe_float(cpu_info.get("temp_c"))
        temp_row = ""
        if c_temp is not None and c_temp > 0:
            t_tone = "crit" if c_temp > 85 else ("warn" if c_temp > 75 else "ok")
            throttle_note = (
                ' <span class="tone-crit">Riesgo de estrangulamiento térmico</span>'
                if c_temp > 85
                else ""
            )
            temp_row = _kv_row(
                "Temperatura",
                f'{_pill(f"{c_temp:g} °C", t_tone, "thermometer")}{throttle_note}',
            )

        cpu_block = f"""
        <div class="subpanel">
            <div class="subpanel-head">
                <span class="subpanel-icon">{svg_icon("cpu", 16)}</span>
                <h3>Procesador y térmica</h3>
            </div>
            <dl class="kv-grid">
                {_kv_row("Modelo", f"<strong>{_esc(c_name)}</strong>")}
                {_kv_row("Arquitectura y reloj", _esc(" — ".join(c_freq_parts) if c_freq_parts else "No reportado"))}
                {_kv_row("Carga actual", f'<div class="bar-value"><span>{_pill(f"{c_load:g}%", c_tone)}</span>{_meter(c_load, c_tone)}</div>')}
                {temp_row}
            </dl>
        </div>
        """

        # Memoria RAM
        m_tot = mem_info.get("total_gb", 0)
        m_used = mem_info.get("used_gb", 0)
        m_free = mem_info.get("free_gb", 0)
        m_pct = _safe_float(mem_info.get("usage_pct")) or 0.0
        m_tone = "crit" if m_pct > 90 else ("warn" if m_pct > 80 else "ok")
        slots_used = mem_info.get("slots_used")
        slots_tot = mem_info.get("slots_total")
        slots_row = (
            _kv_row("Ranuras ocupadas", f"{slots_used} de {slots_tot} disponibles")
            if slots_tot
            else ""
        )

        dimms = mem_info.get("dimms", [])
        dimms_row = ""
        if isinstance(dimms, list) and dimms:
            dimm_items: List[str] = []
            for d in dimms:
                if isinstance(d, dict):
                    d_slot = _esc(d.get("slot") or "DIMM")
                    d_sz = _esc(d.get("size_gb") or "?")
                    d_spd = f" a {_esc(d.get('speed_mhz'))} MHz" if d.get("speed_mhz") else ""
                    d_mfr = f" ({_esc(d.get('mfr'))})" if d.get("mfr") else ""
                    dimm_items.append(
                        f'<li><code>{d_slot}</code> <strong>{d_sz} GB</strong>{d_spd}<span class="muted">{d_mfr}</span></li>'
                    )
            if dimm_items:
                dimms_row = _kv_row("Módulos instalados", f'<ul class="compact-list">{"".join(dimm_items)}</ul>')

        mem_block = f"""
        <div class="subpanel">
            <div class="subpanel-head">
                <span class="subpanel-icon">{svg_icon("memory", 16)}</span>
                <h3>Memoria RAM</h3>
            </div>
            <dl class="kv-grid">
                {_kv_row("Capacidad", f"<strong>{_esc(m_used)} GB usados</strong> de {_esc(m_tot)} GB ({_esc(m_free)} GB libres)")}
                {_kv_row("Saturación", f'<div class="bar-value"><span>{_pill(f"{m_pct:g}%", m_tone)}</span>{_meter(m_pct, m_tone)}</div>')}
                {slots_row}
                {dimms_row}
            </dl>
        </div>
        """

        # Almacenamiento y SMART
        p_drives = storage_info.get("physical_drives", [])
        drives_html = ""
        if isinstance(p_drives, list) and p_drives:
            drive_items: List[str] = []
            for pd in p_drives:
                if isinstance(pd, dict):
                    pd_name = _esc(pd.get("model") or pd.get("name") or "Unidad física")
                    pd_sz = pd.get("size_gb") or pd.get("size") or "N/A"
                    sz_str = f"{pd_sz} GB" if isinstance(pd_sz, (int, float)) else str(pd_sz)
                    pd_bus = pd.get("bus") or ""
                    rot = pd.get("rotational")
                    media = "HDD" if rot is True else ("SSD / NVMe" if rot is False else "")
                    meta_bits = [b for b in (sz_str, pd_bus, media) if b]
                    smart = pd.get("smart_fail")
                    smart_badge = ""
                    if smart is True:
                        smart_badge = _pill("Fallo SMART", "crit", "crit")
                    elif smart is False:
                        smart_badge = _pill("SMART saludable", "ok", "check")
                    drive_items.append(
                        '<li class="drive-item">'
                        f'<div><strong>{pd_name}</strong><div class="muted">{_esc(" / ".join(meta_bits))}</div></div>'
                        f"<div>{smart_badge}</div>"
                        "</li>"
                    )
            drives_html = f'<ul class="drive-list">{"".join(drive_items)}</ul>'

        vols = storage_info.get("volumes", [])
        vol_rows: List[str] = []
        if isinstance(vols, list) and vols:
            for v in vols:
                if isinstance(v, dict):
                    v_drv = _esc(v.get("drive") or v.get("mount") or "Volumen")
                    v_fs = _esc(v.get("fs") or "")
                    v_sz = v.get("size_gb") or v.get("size") or "?"
                    v_free = v.get("free_gb") or v.get("free") or "?"
                    free_pct = _safe_float(v.get("free_pct"))
                    use_pct = _safe_float(v.get("use_pct"))

                    if free_pct is not None:
                        used_pct = max(0.0, min(100.0, 100.0 - free_pct))
                        v_tone = "crit" if free_pct < 5 else ("warn" if free_pct < 10 else "ok")
                        v_label = "Crítico" if free_pct < 5 else ("Espacio bajo" if free_pct < 10 else f"{free_pct:g}% libre")
                        cap_text = f"{v_free} GB libres de {v_sz} GB"
                    elif use_pct is not None:
                        used_pct = max(0.0, min(100.0, use_pct))
                        rem = 100.0 - used_pct
                        v_tone = "crit" if rem < 5 else ("warn" if rem < 10 else "ok")
                        v_label = f"{used_pct:g}% usado"
                        cap_text = f"{v_free} libres de {v_sz}"
                    else:
                        used_pct = 0.0
                        v_tone = "neutral"
                        v_label = "Montado"
                        cap_text = f"{v_free} libres de {v_sz}"

                    fs_badge = f' <span class="muted">({v_fs})</span>' if v_fs else ""
                    vol_rows.append(
                        '<div class="volume-row">'
                        f'<div class="volume-top"><span><code>{v_drv}</code>{fs_badge}</span>{_pill(v_label, v_tone)}</div>'
                        f"{_meter(used_pct, v_tone)}"
                        f'<div class="volume-sub">{_esc(cap_text)}</div>'
                        "</div>"
                    )

        storage_block = ""
        if drives_html or vol_rows:
            storage_block = f"""
            <div class="subpanel">
                <div class="subpanel-head">
                    <span class="subpanel-icon">{svg_icon("drive", 16)}</span>
                    <h3>Almacenamiento y salud SMART</h3>
                </div>
                {drives_html}
                {f'<div class="volume-list">{"".join(vol_rows)}</div>' if vol_rows else ''}
            </div>
            """

        # Gráficos
        gpu_block = ""
        if graphics_info:
            gpu_items: List[str] = []
            for g in graphics_info:
                if isinstance(g, dict):
                    g_name = _esc(g.get("name") or "Adaptador de video")
                    details: List[str] = []
                    if g.get("vram_mb"):
                        details.append(f"VRAM {g['vram_mb']} MB")
                    if g.get("driver"):
                        details.append(f"Controlador {g['driver']}")
                    if g.get("res"):
                        details.append(str(g["res"]))
                    gpu_items.append(
                        f'<li><strong>{g_name}</strong>'
                        + (f'<div class="muted">{_esc(" — ".join(details))}</div>' if details else "")
                        + "</li>"
                    )
            if gpu_items:
                gpu_block = f"""
                <div class="subpanel">
                    <div class="subpanel-head">
                        <span class="subpanel-icon">{svg_icon("monitor", 16)}</span>
                        <h3>Gráficos y pantalla</h3>
                    </div>
                    <ul class="compact-list">{"".join(gpu_items)}</ul>
                </div>
                """

        subsystems_html = f'<div class="subsystem-grid">{cpu_block}{mem_block}{storage_block}{gpu_block}</div>'

    return f"""
    <section id="sec-hardware" class="section">
        <div class="section-head">
            <div class="section-title-group">
                <span class="section-icon">{svg_icon("cpu", 18)}</span>
                <div>
                    <h2>Equipo y hardware</h2>
                    <p class="section-desc">Identidad física del host, firmware y estado de los subsistemas principales</p>
                </div>
            </div>
        </div>
        <div class="identity-box">
            <dl class="kv-grid kv-two-col">
                {"".join(identity_rows)}
            </dl>
        </div>
        {subsystems_html}
    </section>
    """


def _render_security_section(malware_audit: Dict[str, Any]) -> str:
    """Renderiza el triaje de seguridad, defensas, procesos, persistencia y sockets."""
    if not malware_audit or not any(
        k in malware_audit
        for k in ("defenses", "suspicious_processes", "persistence", "network_c2", "recent_artifacts", "threat_score", "threat_level")
    ):
        return ""

    th_lvl = str(malware_audit.get("threat_level") or "BAJO").upper()
    th_score = malware_audit.get("threat_score", 0)
    if th_lvl in ("CRITICO", "CRÍTICO", "ALTO", "CRITICAL"):
        th_tone, th_text, sec_icon = "crit", "Nivel crítico", "shield-alert"
    elif th_lvl in ("MEDIO", "WARN", "WARNING"):
        th_tone, th_text, sec_icon = "warn", "Nivel medio", "shield-alert"
    else:
        th_tone, th_text, sec_icon = "ok", "Nivel bajo", "shield"

    defs = malware_audit.get("defenses", {}) if isinstance(malware_audit.get("defenses"), dict) else {}
    av_name = str(defs.get("antivirus_name") or "Ninguno detectado")
    av_act = bool(defs.get("antivirus_active", False))
    fw = defs.get("firewall_enabled")
    sec_mod = defs.get("security_module")

    net_c2 = malware_audit.get("network_c2", {}) if isinstance(malware_audit.get("network_c2"), dict) else {}
    hosts_hijack = bool(net_c2.get("hosts_file_hijack", False))

    defense_rows = [
        _kv_row("Evaluación heurística", f'{_pill(th_text, th_tone, sec_icon)} <span class="muted">Puntaje: <strong>{_esc(th_score)} pts</strong></span>'),
        _kv_row("Antivirus", f"<strong>{_esc(av_name)}</strong> {_pill('Activo', 'ok', 'check') if av_act else _pill('Inactivo', 'crit', 'crit')}"),
        _kv_row(
            "Cortafuegos",
            (_pill("Habilitado", "ok", "check") if fw else _pill("Deshabilitado", "crit", "crit"))
            if fw is not None
            else _pill("No reportado", "warn"),
        ),
        _kv_row(
            "Integridad archivo Hosts",
            _pill("Alterado o secuestrado", "crit", "crit") if hosts_hijack else _pill("Íntegro", "ok", "check"),
        ),
    ]
    if sec_mod:
        defense_rows.append(_kv_row("Módulo de seguridad", f"<strong>{_esc(sec_mod)}</strong>"))

    # Procesos sospechosos
    susp_procs = malware_audit.get("suspicious_processes", []) if isinstance(malware_audit.get("suspicious_processes"), list) else []
    if susp_procs:
        proc_cards: List[str] = []
        for p in susp_procs:
            if isinstance(p, dict):
                p_pid = p.get("pid", "?")
                p_n = _esc(p.get("name") or "proceso")
                p_path = p.get("path") or ""
                p_rsn = _esc(p.get("reason") or "Comportamiento anómalo")
                proc_cards.append(
                    '<li class="alert-item">'
                    f'<div class="alert-item-head">{_pill(f"PID {p_pid}", "crit")} <strong>{p_n}</strong></div>'
                    f'<div class="alert-item-path">{_copy_code(p_path)}</div>'
                    f'<div class="alert-item-reason">Motivo: {p_rsn}</div>'
                    "</li>"
                )
        procs_html = f'<ul class="alert-list">{"".join(proc_cards)}</ul>'
    else:
        procs_html = f'<div class="clean-state">{svg_icon("check", 16)} <span>Sin procesos sospechosos ni ejecución desde carpetas temporales.</span></div>'

    # Persistencia
    persist = malware_audit.get("persistence", []) if isinstance(malware_audit.get("persistence"), list) else []
    if persist:
        p_items: List[str] = []
        for pr in persist:
            if isinstance(pr, dict):
                pr_t = _esc(pr.get("type") or "Inicio")
                pr_n = _esc(pr.get("name") or "Entrada")
                pr_p = pr.get("path") or ""
                pr_loc = f' <span class="muted">({_esc(pr.get("location"))})</span>' if pr.get("location") else ""
                p_items.append(
                    '<li class="entry-item">'
                    f'<div>{_pill(pr_t, "neutral")} <strong>{pr_n}</strong>{pr_loc}</div>'
                    f'<div class="entry-path">{_copy_code(pr_p)}</div>'
                    "</li>"
                )
        persist_html = f'<ul class="entry-list">{"".join(p_items)}</ul>'
    else:
        persist_html = f'<div class="clean-state">{svg_icon("check", 16)} <span>Sin entradas de persistencia automática registradas.</span></div>'

    # Conexiones establecidas y puertos en escucha
    conns = net_c2.get("established_connections", []) if isinstance(net_c2.get("established_connections"), list) else []
    if conns:
        c_items: List[str] = []
        for c in conns:
            if isinstance(c, dict):
                rip = c.get("remote_ip") or ""
                rport = c.get("remote_port", 0)
                proc = _esc(c.get("process") or "desconocido")
                is_susp = bool(c.get("suspicious", False))
                badge = _pill("Sospechosa", "crit", "crit") if is_susp else _pill("Activa", "neutral")
                c_items.append(
                    f'<li class="entry-item"><div>{badge} {_copy_code(f"{rip}:{rport}")} '
                    f'<span class="muted">Proceso: {proc}</span></div></li>'
                )
        conns_html = f'<ul class="entry-list">{"".join(c_items)}</ul>'
    else:
        conns_html = f'<div class="clean-state">{svg_icon("check", 16)} <span>Sin conexiones salientes externas activas en el muestreo.</span></div>'

    l_ports = net_c2.get("listening_ports", []) if isinstance(net_c2.get("listening_ports"), list) else []
    ports_html = ""
    if l_ports:
        port_chips: List[str] = []
        for lp in l_ports:
            if isinstance(lp, dict):
                port_num = lp.get("port", "?")
                proc_lbl = f" ({_esc(lp['process'])})" if lp.get("process") else ""
                port_chips.append(f'<span class="port-chip"><code>:{_esc(port_num)}</code>{proc_lbl}</span>')
            else:
                port_chips.append(f'<span class="port-chip"><code>:{_esc(lp)}</code></span>')
        ports_html = (
            '<div class="sub-block">'
            "<h4>Puertos en escucha</h4>"
            f'<div class="chip-wrap">{"".join(port_chips)}</div>'
            "</div>"
        )

    # Artefactos recientes en Temp
    recent_art = malware_audit.get("recent_artifacts", []) if isinstance(malware_audit.get("recent_artifacts"), list) else []
    if recent_art:
        art_items: List[str] = []
        for a in recent_art:
            if isinstance(a, dict):
                a_n = _esc(a.get("name") or "archivo")
                a_p = a.get("path") or ""
                a_sz = _esc(a.get("size_kb", 0))
                a_dt = _esc(a.get("date") or "")
                art_items.append(
                    '<li class="entry-item">'
                    f'<div><strong>{a_n}</strong> <span class="muted">{a_sz} KB — {a_dt}</span></div>'
                    f'<div class="entry-path">{_copy_code(a_p)}</div>'
                    "</li>"
                )
        art_html = f'<ul class="entry-list">{"".join(art_items)}</ul>'
    else:
        art_html = f'<div class="clean-state">{svg_icon("check", 16)} <span>No se hallaron binarios recientes en carpetas temporales (&lt; 7 días).</span></div>'

    return f"""
    <section id="sec-seguridad" class="section">
        <div class="section-head">
            <div class="section-title-group">
                <span class="section-icon">{svg_icon(sec_icon, 18)}</span>
                <div>
                    <h2>Seguridad y análisis de amenazas</h2>
                    <p class="section-desc">Defensas activas, procesos en memoria, persistencia y actividad de red</p>
                </div>
            </div>
        </div>
        <div class="identity-box">
            <dl class="kv-grid kv-two-col">
                {"".join(defense_rows)}
            </dl>
        </div>
        <div class="subsystem-grid">
            <div class="subpanel">
                <div class="subpanel-head">
                    <span class="subpanel-icon">{svg_icon("terminal", 16)}</span>
                    <h3>Procesos en memoria</h3>
                </div>
                {procs_html}
            </div>
            <div class="subpanel">
                <div class="subpanel-head">
                    <span class="subpanel-icon">{svg_icon("clock", 16)}</span>
                    <h3>Persistencia y arranque</h3>
                </div>
                {persist_html}
            </div>
            <div class="subpanel">
                <div class="subpanel-head">
                    <span class="subpanel-icon">{svg_icon("globe", 16)}</span>
                    <h3>Sockets y conexiones de red</h3>
                </div>
                {conns_html}
                {ports_html}
            </div>
            <div class="subpanel">
                <div class="subpanel-head">
                    <span class="subpanel-icon">{svg_icon("file-code", 16)}</span>
                    <h3>Artefactos temporales recientes</h3>
                </div>
                {art_html}
            </div>
        </div>
    </section>
    """


def _render_telemetry_section(telemetry: Dict[str, Any], default_open: bool = False) -> str:
    """Renderiza métricas adicionales de telemetría en un anexo colapsable limpio."""
    if not isinstance(telemetry, dict):
        return ""

    ignored = {"hardware", "hardware_audit", "malware_audit", "osi_network", "osi"}
    extra_items = [(k, v) for k, v in telemetry.items() if k not in ignored]
    if not extra_items:
        return ""

    rows: List[str] = []
    for k, v in extra_items:
        label = str(k).replace("_", " ").strip().capitalize()
        if isinstance(v, bool):
            val_html = _pill("Sí", "ok") if v else _pill("No", "neutral")
        elif isinstance(v, (dict, list)):
            pretty = json.dumps(v, indent=2, ensure_ascii=False)
            val_html = f"<pre>{_esc(pretty)}</pre>"
        else:
            val_html = _esc(v)
        rows.append(_kv_row(label, val_html))

    open_attr = " open" if default_open else ""
    count = len(extra_items)
    return f"""
    <section id="sec-telemetria" class="section">
        <details class="collapsible"{open_attr}>
            <summary class="collapsible-summary">
                <div class="section-title-group">
                    <span class="section-icon">{svg_icon("terminal", 18)}</span>
                    <div>
                        <h2>Telemetría adicional</h2>
                        <p class="section-desc">{count} campo(s) complementarios reportados por el equipo</p>
                    </div>
                </div>
                <span class="chevron">{svg_icon("chevron", 18)}</span>
            </summary>
            <div class="collapsible-body">
                <dl class="kv-grid kv-two-col">
                    {"".join(rows)}
                </dl>
            </div>
        </details>
    </section>
    """


_BASE_CSS = """
:root {
    --surface-canvas: #F2F4F7;
    --surface-sheet: #FFFFFF;
    --surface-subtle: #E9EEF3;
    --surface-hover: #E1E8F0;
    --border: #D5DCE4;
    --border-strong: #BAC5D2;
    --ink-primary: #111820;
    --ink-secondary: #4B5563;
    --ink-muted: #667384;
    --accent-brand: #1D4ED8;
    --accent-soft: rgba(29, 78, 216, 0.09);

    --ok-ink: #0F766E;
    --ok-bg: #E6F4F1;
    --ok-border: #99D5C9;

    --warn-ink: #B45309;
    --warn-bg: #FEF3C7;
    --warn-border: #F5C26B;

    --crit-ink: #B91C1C;
    --crit-bg: #FEE2E2;
    --crit-border: #F5A3A3;

    --info-ink: #1D4ED8;
    --info-bg: #E8F0FE;
    --info-border: #B3CBF9;

    --neutral-ink: #334155;
    --neutral-bg: #EDF1F6;
    --neutral-border: #D5DCE4;

    --font-sans: "Inter", "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    --font-mono: "JetBrains Mono", "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    --radius-sm: 6px;
    --radius-md: 10px;
    --radius-lg: 14px;
}

[data-theme="dark"] {
    --surface-canvas: #12161B;
    --surface-sheet: #1A2028;
    --surface-subtle: #232B35;
    --surface-hover: #2B3542;
    --border: #2C3643;
    --border-strong: #3E4B5B;
    --ink-primary: #EDF1F5;
    --ink-secondary: #A6B2C1;
    --ink-muted: #7F8C9D;
    --accent-brand: #60A5FA;
    --accent-soft: rgba(96, 165, 250, 0.13);

    --ok-ink: #5EEAD4;
    --ok-bg: rgba(20, 184, 166, 0.14);
    --ok-border: rgba(94, 234, 212, 0.30);

    --warn-ink: #FCD34D;
    --warn-bg: rgba(245, 158, 11, 0.14);
    --warn-border: rgba(252, 211, 77, 0.30);

    --crit-ink: #FCA5A5;
    --crit-bg: rgba(239, 68, 68, 0.16);
    --crit-border: rgba(252, 165, 165, 0.32);

    --info-ink: #93C5FD;
    --info-bg: rgba(59, 130, 246, 0.15);
    --info-border: rgba(147, 197, 253, 0.30);

    --neutral-ink: #CBD5E1;
    --neutral-bg: #252E39;
    --neutral-border: #364252;
}

*, *::before, *::after {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

html {
    scroll-behavior: smooth;
    -webkit-text-size-adjust: 100%;
}

body {
    background: var(--surface-canvas);
    color: var(--ink-primary);
    font-family: var(--font-sans);
    font-size: 15px;
    line-height: 1.55;
    font-variant-numeric: tabular-nums;
    min-height: 100vh;
}

a {
    color: var(--accent-brand);
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

:focus-visible {
    outline: 2px solid var(--accent-brand);
    outline-offset: 2px;
}

.icon {
    flex-shrink: 0;
    display: inline-block;
    vertical-align: middle;
}

/* Barra superior utilitaria */
.topbar {
    background: var(--surface-sheet);
    border-bottom: 1px solid var(--border);
    position: relative;
    z-index: 20;
}

.topbar-inner {
    max-width: 920px;
    margin: 0 auto;
    padding: 0.7rem 1rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    flex-wrap: wrap;
}

.brand {
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    color: var(--ink-primary);
    font-weight: 700;
    font-size: 0.95rem;
    letter-spacing: -0.01em;
}

.brand:hover {
    text-decoration: none;
}

.brand-mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border-radius: 8px;
    background: var(--accent-soft);
    color: var(--accent-brand);
}

.brand-sep {
    color: var(--border-strong);
    font-weight: 400;
}

.report-ref {
    font-family: var(--font-mono);
    font-size: 0.82rem;
    font-weight: 500;
    color: var(--ink-secondary);
}

.topbar-actions {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    flex-wrap: wrap;
}

.tool-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.38rem;
    padding: 0.4rem 0.7rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
    background: var(--surface-sheet);
    color: var(--ink-primary);
    font-family: var(--font-sans);
    font-size: 0.82rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 140ms ease, border-color 140ms ease;
    text-decoration: none;
}

.tool-btn:hover {
    background: var(--surface-subtle);
    border-color: var(--border-strong);
    text-decoration: none;
}

.tool-btn-primary {
    background: var(--accent-brand);
    border-color: var(--accent-brand);
    color: #FFFFFF;
}

.tool-btn-primary:hover {
    filter: brightness(1.06);
    background: var(--accent-brand);
    border-color: var(--accent-brand);
}

/* Contenedor principal */
.page {
    max-width: 920px;
    margin: 0 auto;
    padding: 1.25rem 1rem 3rem;
}

/* Hero / Veredicto principal */
.hero {
    background: var(--surface-sheet);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 1.35rem 1.35rem 1.25rem;
    margin-bottom: 1rem;
}

.hero-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.9rem;
    flex-wrap: wrap;
}

.hero-host {
    font-size: clamp(1.4rem, 3.8vw, 1.95rem);
    font-weight: 700;
    letter-spacing: -0.025em;
    line-height: 1.18;
    color: var(--ink-primary);
    overflow-wrap: anywhere;
}

.hero-facts {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem 1.35rem;
    margin-top: 0.75rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
    font-size: 0.86rem;
    color: var(--ink-secondary);
}

.hero-fact {
    display: inline-flex;
    align-items: baseline;
    gap: 0.4rem;
}

.hero-fact-label {
    color: var(--ink-muted);
    font-size: 0.8rem;
}

.hero-fact strong {
    color: var(--ink-primary);
    font-weight: 600;
}

/* Sello de estado principal */
.verdict-stamp {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.42rem 0.8rem;
    border-radius: 999px;
    font-size: 0.86rem;
    font-weight: 600;
    border: 1px solid transparent;
    white-space: nowrap;
}

.verdict-ok { background: var(--ok-bg); color: var(--ok-ink); border-color: var(--ok-border); }
.verdict-warn { background: var(--warn-bg); color: var(--warn-ink); border-color: var(--warn-border); }
.verdict-crit { background: var(--crit-bg); color: var(--crit-ink); border-color: var(--crit-border); }
.verdict-info { background: var(--info-bg); color: var(--info-ink); border-color: var(--info-border); }

/* Franja de signos vitales */
.vitals-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
    gap: 0.75rem;
    margin-top: 1.05rem;
}

.vital-tile {
    background: var(--surface-canvas);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 0.8rem 0.9rem;
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
}

.vital-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
}

.vital-label {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.82rem;
    font-weight: 500;
    color: var(--ink-secondary);
}

.vital-num {
    font-size: 1.08rem;
    font-weight: 700;
}

.vital-primary {
    font-size: 1.02rem;
    font-weight: 600;
    color: var(--ink-primary);
    overflow-wrap: anywhere;
}

.vital-code {
    font-family: var(--font-mono);
    font-size: 0.95rem;
    font-weight: 600;
    background: transparent;
    padding: 0;
}

.vital-score {
    font-size: 1.15rem;
    font-weight: 700;
}

.vital-unit {
    font-size: 0.8rem;
    font-weight: 400;
    color: var(--ink-secondary);
}

.vital-sub {
    font-size: 0.78rem;
    color: var(--ink-muted);
    line-height: 1.35;
}

/* Navegación rápida pegajosa */
.section-nav {
    position: sticky;
    top: 0;
    z-index: 15;
    background: var(--surface-canvas);
    padding: 0.55rem 0;
    margin-bottom: 0.85rem;
    border-bottom: 1px solid var(--border);
}

.section-nav-inner {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    overflow-x: auto;
    scrollbar-width: none;
    padding-bottom: 2px;
}

.section-nav-inner::-webkit-scrollbar {
    display: none;
}

.nav-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.42rem 0.8rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface-sheet);
    color: var(--ink-secondary);
    font-size: 0.84rem;
    font-weight: 500;
    white-space: nowrap;
    transition: color 140ms ease, border-color 140ms ease, background 140ms ease;
}

.nav-pill:hover {
    color: var(--ink-primary);
    border-color: var(--border-strong);
    text-decoration: none;
}

.nav-pill.is-active {
    background: var(--accent-soft);
    border-color: var(--accent-brand);
    color: var(--accent-brand);
    font-weight: 600;
}

/* Secciones principales */
.section {
    background: var(--surface-sheet);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 1.3rem 1.35rem;
    margin-bottom: 1rem;
    scroll-margin-top: 3.8rem;
}

.section-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 1.05rem;
    padding-bottom: 0.8rem;
    border-bottom: 1px solid var(--border);
}

.section-title-group {
    display: flex;
    align-items: flex-start;
    gap: 0.7rem;
}

.section-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    border-radius: 8px;
    background: var(--surface-subtle);
    color: var(--ink-primary);
    flex-shrink: 0;
    margin-top: 1px;
}

.section h2 {
    font-size: 1.08rem;
    font-weight: 700;
    letter-spacing: -0.015em;
    color: var(--ink-primary);
    line-height: 1.3;
}

.section-desc {
    font-size: 0.84rem;
    color: var(--ink-secondary);
    margin-top: 0.15rem;
}

.section h3 {
    font-size: 0.94rem;
    font-weight: 600;
    color: var(--ink-primary);
    line-height: 1.35;
}

.section h4 {
    font-size: 0.84rem;
    font-weight: 600;
    color: var(--ink-secondary);
    margin-bottom: 0.5rem;
}

/* Sección de Diagnóstico */
.diagnosis-section {
    border-left: 4px solid var(--accent-brand);
}
.diagnosis-ok { border-left-color: var(--ok-ink); }
.diagnosis-warn { border-left-color: var(--warn-ink); }
.diagnosis-crit { border-left-color: var(--crit-ink); }

.diagnosis-summary {
    font-size: 0.97rem;
    line-height: 1.65;
    color: var(--ink-primary);
    max-width: 70ch;
    margin-bottom: 1.15rem;
}

.diagnosis-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 1.15rem;
    padding-top: 0.95rem;
    border-top: 1px solid var(--border);
}

.diagnosis-col h3 {
    margin-bottom: 0.65rem;
}

.finding-list, .step-list {
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
}

.finding-list li, .step-list li {
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
    font-size: 0.9rem;
    line-height: 1.5;
    color: var(--ink-primary);
}

.list-icon {
    margin-top: 2px;
    flex-shrink: 0;
}

.step-num {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 22px;
    height: 22px;
    border-radius: 999px;
    background: var(--accent-soft);
    color: var(--accent-brand);
    font-size: 0.76rem;
    font-weight: 700;
    margin-top: 1px;
    flex-shrink: 0;
}

/* Escalera OSI */
.osi-ladder {
    display: flex;
    flex-direction: column;
}

.osi-layer {
    display: grid;
    grid-template-columns: 54px 1fr;
    gap: 0.85rem;
}

.osi-rail {
    display: flex;
    flex-direction: column;
    align-items: center;
}

.osi-tag {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 48px;
    height: 28px;
    border-radius: var(--radius-sm);
    font-family: var(--font-mono);
    font-size: 0.78rem;
    font-weight: 700;
    border: 1px solid var(--neutral-border);
    background: var(--neutral-bg);
    color: var(--neutral-ink);
    flex-shrink: 0;
}

.osi-line {
    width: 2px;
    flex: 1;
    min-height: 14px;
    background: var(--border);
    margin: 4px 0;
}

.osi-layer:last-child .osi-line {
    display: none;
}

.osi-ok .osi-tag { background: var(--ok-bg); color: var(--ok-ink); border-color: var(--ok-border); }
.osi-ok .osi-line { background: var(--ok-border); }

.osi-warn .osi-tag { background: var(--warn-bg); color: var(--warn-ink); border-color: var(--warn-border); }
.osi-warn .osi-line { background: var(--warn-border); }

.osi-crit .osi-tag { background: var(--crit-bg); color: var(--crit-ink); border-color: var(--crit-border); }
.osi-crit .osi-line { background: var(--crit-border); }

.osi-body {
    padding-bottom: 1.15rem;
    margin-bottom: 0.35rem;
    border-bottom: 1px solid var(--border);
    min-width: 0;
}

.osi-layer:last-child .osi-body {
    padding-bottom: 0;
    margin-bottom: 0;
    border-bottom: none;
}

.osi-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    flex-wrap: wrap;
    margin-bottom: 0.6rem;
}

.hop-trail {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
}

.hop-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.16rem 0.48rem;
    border-radius: var(--radius-sm);
    background: var(--surface-subtle);
    font-family: var(--font-mono);
    font-size: 0.78rem;
    color: var(--ink-primary);
}

.hop-chip b {
    color: var(--ink-muted);
    font-weight: 600;
}

/* Rejillas de clave-valor */
.kv-grid {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.kv-two-col {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 0.65rem 1.5rem;
}

.kv-row {
    display: grid;
    grid-template-columns: minmax(118px, 38%) 1fr;
    gap: 0.35rem 0.85rem;
    align-items: baseline;
    font-size: 0.88rem;
    min-width: 0;
}

.kv-key {
    color: var(--ink-secondary);
    font-weight: 500;
    line-height: 1.45;
}

.kv-val {
    color: var(--ink-primary);
    min-width: 0;
    overflow-wrap: anywhere;
    line-height: 1.45;
}

/* Identidad y subpaneles */
.identity-box {
    background: var(--surface-canvas);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 0.95rem 1.05rem;
    margin-bottom: 1rem;
}

.subsystem-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
    gap: 0.9rem;
}

.subpanel {
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 1rem 1.05rem;
    background: var(--surface-sheet);
    min-width: 0;
}

.subpanel-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.8rem;
    padding-bottom: 0.55rem;
    border-bottom: 1px solid var(--border);
}

.subpanel-icon {
    color: var(--ink-secondary);
    display: inline-flex;
}

/* Medidores */
.meter {
    width: 100%;
    height: 7px;
    background: var(--surface-subtle);
    border-radius: 999px;
    overflow: hidden;
}

.meter-fill {
    display: block;
    height: 100%;
    border-radius: 999px;
}

.meter-ok { background: var(--ok-ink); }
.meter-warn { background: var(--warn-ink); }
.meter-crit { background: var(--crit-ink); }
.meter-brand { background: var(--accent-brand); }
.meter-neutral { background: var(--ink-muted); }

.bar-value {
    display: flex;
    align-items: center;
    gap: 0.65rem;
}

.bar-value .meter {
    flex: 1;
    min-width: 80px;
}

.inline-meter {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
}

/* Discos y volúmenes */
.drive-list, .compact-list, .alert-list, .entry-list {
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
}

.drive-item {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.6rem;
    padding: 0.6rem 0.75rem;
    border-radius: var(--radius-sm);
    background: var(--surface-canvas);
    border: 1px solid var(--border);
    font-size: 0.86rem;
}

.volume-list {
    display: flex;
    flex-direction: column;
    gap: 0.7rem;
    margin-top: 0.75rem;
}

.volume-row {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
}

.volume-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    font-size: 0.86rem;
}

.volume-sub {
    font-size: 0.78rem;
    color: var(--ink-muted);
}

/* Seguridad: estados limpios y alertas */
.clean-state {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.7rem 0.8rem;
    border-radius: var(--radius-sm);
    background: var(--ok-bg);
    color: var(--ok-ink);
    font-size: 0.86rem;
    line-height: 1.45;
}

.alert-item {
    padding: 0.7rem 0.85rem;
    border-radius: var(--radius-sm);
    background: var(--crit-bg);
    border: 1px solid var(--crit-border);
    font-size: 0.86rem;
}

.alert-item-head {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    flex-wrap: wrap;
    margin-bottom: 0.35rem;
}

.alert-item-path {
    margin-bottom: 0.3rem;
}

.alert-item-reason {
    color: var(--crit-ink);
    font-size: 0.82rem;
    font-weight: 500;
}

.entry-item {
    padding: 0.55rem 0.7rem;
    border-radius: var(--radius-sm);
    background: var(--surface-canvas);
    border: 1px solid var(--border);
    font-size: 0.85rem;
}

.entry-path {
    margin-top: 0.3rem;
}

.sub-block {
    margin-top: 0.85rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
}

.chip-wrap {
    display: flex;
    flex-wrap: wrap;
    gap: 0.38rem;
}

.port-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.18rem 0.5rem;
    border-radius: var(--radius-sm);
    background: var(--surface-subtle);
    font-size: 0.8rem;
    color: var(--ink-secondary);
}

/* Callout de alerta */
.callout {
    display: flex;
    align-items: flex-start;
    gap: 0.7rem;
    padding: 0.85rem 1rem;
    border-radius: var(--radius-md);
    margin-bottom: 1rem;
    font-size: 0.88rem;
}

.callout-warn {
    background: var(--warn-bg);
    border: 1px solid var(--warn-border);
    color: var(--warn-ink);
}

.callout p {
    margin-top: 0.2rem;
    color: var(--ink-primary);
    font-size: 0.85rem;
}

/* Insignias y tonos */
.pill {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.14rem 0.52rem;
    border-radius: 999px;
    font-size: 0.76rem;
    font-weight: 600;
    line-height: 1.4;
    border: 1px solid transparent;
    white-space: nowrap;
}

.pill-ok { background: var(--ok-bg); color: var(--ok-ink); border-color: var(--ok-border); }
.pill-warn { background: var(--warn-bg); color: var(--warn-ink); border-color: var(--warn-border); }
.pill-crit { background: var(--crit-bg); color: var(--crit-ink); border-color: var(--crit-border); }
.pill-info { background: var(--info-bg); color: var(--info-ink); border-color: var(--info-border); }
.pill-neutral { background: var(--neutral-bg); color: var(--neutral-ink); border-color: var(--neutral-border); }

.tone-ok { color: var(--ok-ink); }
.tone-warn { color: var(--warn-ink); }
.tone-crit { color: var(--crit-ink); }
.tone-info { color: var(--info-ink); }
.muted { color: var(--ink-muted); font-size: 0.84em; }

/* Código y copia en un clic */
code {
    font-family: var(--font-mono);
    font-size: 0.82em;
    background: var(--surface-subtle);
    color: var(--ink-primary);
    padding: 0.12rem 0.38rem;
    border-radius: 4px;
    overflow-wrap: anywhere;
}

.code-copy {
    display: inline-flex;
    align-items: center;
    gap: 0.32rem;
    padding: 0.12rem 0.42rem;
    border-radius: 5px;
    border: 1px solid transparent;
    background: var(--surface-subtle);
    color: var(--ink-primary);
    font-family: var(--font-mono);
    font-size: 0.8rem;
    cursor: pointer;
    max-width: 100%;
    text-align: left;
    transition: border-color 140ms ease, background 140ms ease;
}

.code-copy code {
    background: transparent;
    padding: 0;
    font-size: inherit;
}

.code-copy:hover {
    border-color: var(--border-strong);
    background: var(--surface-hover);
}

.copy-glyph {
    color: var(--ink-muted);
    display: inline-flex;
    flex-shrink: 0;
}

.code-copy.copied {
    border-color: var(--ok-border);
    background: var(--ok-bg);
    color: var(--ok-ink);
}

.code-copy.copied .copy-glyph {
    color: var(--ok-ink);
}

pre {
    background: var(--surface-canvas);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 0.7rem 0.8rem;
    overflow-x: auto;
    font-family: var(--font-mono);
    font-size: 0.78rem;
    line-height: 1.5;
    color: var(--ink-primary);
}

/* Colapsable nativo */
.collapsible-summary {
    list-style: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
}

.collapsible-summary::-webkit-details-marker {
    display: none;
}

.collapsible .chevron {
    color: var(--ink-muted);
    transition: transform 160ms ease;
}

.collapsible[open] .chevron {
    transform: rotate(180deg);
}

.collapsible-body {
    margin-top: 1rem;
    padding-top: 0.9rem;
    border-top: 1px solid var(--border);
}

/* Lista de informes (índice) y estado vacío / 404 */
.report-list {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
}

.report-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.9rem;
    padding: 0.95rem 1.05rem;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: var(--surface-sheet);
    color: var(--ink-primary);
    transition: border-color 140ms ease, background 140ms ease;
}

.report-row:hover {
    border-color: var(--accent-brand);
    background: var(--surface-canvas);
    text-decoration: none;
}

.report-row-title {
    font-weight: 600;
    font-size: 0.98rem;
}

.report-row-meta {
    font-size: 0.82rem;
    color: var(--ink-secondary);
    margin-top: 0.2rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 1rem;
}

.empty-panel {
    background: var(--surface-sheet);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 2rem 1.5rem;
    max-width: 560px;
    margin: 2rem auto;
}

.empty-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    border-radius: 10px;
    background: var(--surface-subtle);
    color: var(--ink-secondary);
    margin-bottom: 1rem;
}

.empty-panel h1 {
    font-size: 1.25rem;
    font-weight: 700;
    margin-bottom: 0.5rem;
}

.empty-panel p {
    color: var(--ink-secondary);
    font-size: 0.92rem;
    line-height: 1.6;
    margin-bottom: 1.15rem;
}

.empty-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
}

/* Pie de página */
.footer {
    margin-top: 1.75rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.5rem;
    font-size: 0.8rem;
    color: var(--ink-muted);
}

/* Aviso flotante de copia */
.toast {
    position: fixed;
    bottom: 1.25rem;
    right: 1.25rem;
    background: var(--ink-primary);
    color: var(--surface-sheet);
    padding: 0.55rem 0.9rem;
    border-radius: var(--radius-sm);
    font-size: 0.84rem;
    font-weight: 500;
    opacity: 0;
    pointer-events: none;
    transform: translateY(6px);
    transition: opacity 150ms ease, transform 150ms ease;
    z-index: 50;
}

.toast.show {
    opacity: 1;
    transform: translateY(0);
}

/* Adaptación móvil */
@media (max-width: 600px) {
    .page { padding: 0.85rem 0.75rem 2.5rem; }
    .hero, .section { padding: 1rem 0.95rem; }
    .kv-row { grid-template-columns: 1fr; gap: 0.15rem; }
    .osi-layer { grid-template-columns: 42px 1fr; gap: 0.6rem; }
    .osi-tag { width: 40px; height: 26px; font-size: 0.74rem; }
    .topbar-inner { padding: 0.6rem 0.75rem; }
    .btn-label-sm { display: none; }
}

/* Respetar preferencia de movimiento reducido */
@media (prefers-reduced-motion: reduce) {
    html { scroll-behavior: auto; }
    *, *::before, *::after { transition: none !important; animation: none !important; }
}

/* Impresión y exportación a PDF */
@media print {
    :root, [data-theme="dark"] {
        --surface-canvas: #FFFFFF;
        --surface-sheet: #FFFFFF;
        --surface-subtle: #F3F4F6;
        --border: #D1D5DB;
        --ink-primary: #000000;
        --ink-secondary: #374151;
        --ink-muted: #4B5563;
    }
    body { background: #FFFFFF; font-size: 11pt; }
    .topbar-actions, .section-nav, .toast, .copy-glyph { display: none !important; }
    .page { max-width: 100%; padding: 0.5rem 0; }
    .hero, .section, .subpanel { break-inside: avoid; box-shadow: none; }
    .code-copy { border: none; background: transparent; padding: 0; }
}
"""

_THEME_INIT_JS = """
(function(){
  try {
    var saved = localStorage.getItem('rei-theme');
    if (saved === 'dark' || saved === 'light') {
      document.documentElement.setAttribute('data-theme', saved);
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      document.documentElement.setAttribute('data-theme', 'dark');
    }
  } catch (e) {}
})();
"""

_INTERACTION_JS = """
(function(){
  var themeBtn = document.getElementById('theme-toggle');
  function syncThemeBtn() {
    if (!themeBtn) return;
    var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    var lbl = themeBtn.querySelector('.theme-label');
    var sun = themeBtn.querySelector('.ico-sun');
    var moon = themeBtn.querySelector('.ico-moon');
    if (lbl) lbl.textContent = isDark ? 'Claro' : 'Oscuro';
    if (sun) sun.style.display = isDark ? 'inline-flex' : 'none';
    if (moon) moon.style.display = isDark ? 'none' : 'inline-flex';
  }
  syncThemeBtn();
  if (themeBtn) {
    themeBtn.addEventListener('click', function(){
      var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('rei-theme', next); } catch (e) {}
      syncThemeBtn();
    });
  }

  var toast = document.getElementById('copy-toast');
  var toastTimer = null;
  function showToast(msg) {
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function(){ toast.classList.remove('show'); }, 1800);
  }

  function copyValue(text, btn) {
    function markDone() {
      btn.classList.add('copied');
      showToast('Copiado al portapapeles');
      setTimeout(function(){ btn.classList.remove('copied'); }, 1400);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(markDone).catch(function(){ fallbackCopy(text, markDone); });
    } else {
      fallbackCopy(text, markDone);
    }
  }

  function fallbackCopy(text, done) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) {}
    document.body.removeChild(ta);
  }

  document.addEventListener('click', function(ev){
    var target = ev.target.closest ? ev.target.closest('[data-copy]') : null;
    if (target) {
      copyValue(target.getAttribute('data-copy') || '', target);
    }
  });

  var printBtn = document.getElementById('print-btn');
  if (printBtn) {
    printBtn.addEventListener('click', function(){
      document.querySelectorAll('details.collapsible').forEach(function(d){ d.open = true; });
      window.print();
    });
  }

  var navLinks = Array.prototype.slice.call(document.querySelectorAll('.nav-pill'));
  if (navLinks.length && 'IntersectionObserver' in window) {
    var byId = {};
    navLinks.forEach(function(a){
      var id = (a.getAttribute('href') || '').replace(/^#/, '');
      if (id) byId[id] = a;
    });
    var observer = new IntersectionObserver(function(entries){
      entries.forEach(function(entry){
        if (entry.isIntersecting && byId[entry.target.id]) {
          navLinks.forEach(function(a){ a.classList.remove('is-active'); });
          byId[entry.target.id].classList.add('is-active');
        }
      });
    }, { rootMargin: '-18% 0px -68% 0px', threshold: 0 });
    Object.keys(byId).forEach(function(id){
      var el = document.getElementById(id);
      if (el) observer.observe(el);
    });
  }
})();
"""


def _render_topbar(report_id: Optional[str] = None, show_report_actions: bool = False) -> str:
    """Renderiza la barra superior común con identidad REI y controles."""
    ref_html = (
        f'<span class="brand-sep">/</span><span class="report-ref">#{_esc(report_id)}</span>'
        if report_id
        else ""
    )
    report_actions = ""
    if show_report_actions and report_id:
        report_actions = (
            f'<a class="tool-btn" href="/api/v1/report/{_esc(report_id)}" target="_blank" rel="noopener">'
            f'{svg_icon("file-code", 15)}<span class="btn-label-sm">JSON</span></a>'
            '<button type="button" id="print-btn" class="tool-btn">'
            f'{svg_icon("printer", 15)}<span class="btn-label-sm">Imprimir</span></button>'
        )

    theme_btn = (
        '<button type="button" id="theme-toggle" class="tool-btn" aria-label="Cambiar tema visual">'
        f'<span class="ico-sun" style="display:none">{svg_icon("sun", 15)}</span>'
        f'<span class="ico-moon">{svg_icon("moon", 15)}</span>'
        '<span class="theme-label">Oscuro</span>'
        "</button>"
    )

    return f"""
    <header class="topbar">
        <div class="topbar-inner">
            <a class="brand" href="/">
                <span class="brand-mark">{svg_icon("brand", 18)}</span>
                <span>REI Diagnóstico</span>
                {ref_html}
            </a>
            <div class="topbar-actions">
                {report_actions}
                {theme_btn}
            </div>
        </div>
    </header>
    """


def render_report_html(report: Any) -> str:
    """Renderiza el informe completo de diagnóstico REI en HTML autocontenido."""
    dt_str = _format_timestamp(getattr(report, "created_at", 0.0) or 0.0)
    status_tone, status_label, status_icon = _status_meta(getattr(report, "overall_status", "OK"))

    hw = _extract_section_dict(report, "hardware", "hardware")
    hw_audit = _extract_section_dict(report, "hardware_audit", "hardware_audit")
    malware_audit = _extract_section_dict(report, "malware_audit", "malware_audit")
    osi = _extract_section_dict(report, "osi_network", "osi_network", "osi")
    telemetry = getattr(report, "telemetry", {}) if isinstance(getattr(report, "telemetry", {}), dict) else {}
    ai_analysis = getattr(report, "ai_analysis", None)

    ai_html = _render_ai_section(ai_analysis, getattr(report, "overall_status", "OK"))
    osi_html = _render_osi_section(osi)
    hw_html = _render_hardware_section(hw, hw_audit, getattr(report, "os_type", "Desconocido"))
    sec_html = _render_security_section(malware_audit)

    has_structured = any((ai_html, osi_html, hw_html, sec_html))
    telemetry_html = _render_telemetry_section(telemetry, default_open=not has_structured)

    # Navegación rápida solo para las secciones presentes
    nav_items: List[Tuple[str, str, str]] = []
    if ai_html:
        nav_items.append(("sec-diagnostico", "Diagnóstico", "diagnosis"))
    if osi_html:
        nav_items.append(("sec-red", "Red OSI", "layers"))
    if hw_html:
        nav_items.append(("sec-hardware", "Hardware", "cpu"))
    if sec_html:
        nav_items.append(("sec-seguridad", "Seguridad", "shield"))
    if telemetry_html:
        nav_items.append(("sec-telemetria", "Telemetría", "terminal"))

    nav_html = ""
    if len(nav_items) > 1:
        pills = "".join(
            f'<a class="nav-pill{" is-active" if idx == 0 else ""}" href="#{sec_id}">'
            f"{svg_icon(icon_name, 14)}<span>{_esc(label)}</span></a>"
            for idx, (sec_id, label, icon_name) in enumerate(nav_items)
        )
        nav_html = f'<nav class="section-nav" aria-label="Secciones del informe"><div class="section-nav-inner">{pills}</div></nav>'

    # Signos vitales
    vital_tiles = _build_vitals(hw_audit, osi, malware_audit, telemetry)
    vitals_html = f'<div class="vitals-grid">{"".join(vital_tiles)}</div>' if vital_tiles else ""

    # Metadatos del encabezado
    sys_info = hw_audit.get("system", {}) if isinstance(hw_audit.get("system"), dict) else {}
    os_display = sys_info.get("os_name") or hw.get("os_name") or _format_os(getattr(report, "os_type", ""))
    cat_display = _format_category(getattr(report, "category", "GENERAL"))
    hostname = str(getattr(report, "hostname", "Host remoto"))
    report_id = str(getattr(report, "report_id", "latest"))

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>REI — {_esc(hostname)}</title>
    <script>{_THEME_INIT_JS}</script>
    <style>{_BASE_CSS}</style>
</head>
<body>
    {_render_topbar(report_id=report_id, show_report_actions=True)}
    <main class="page">
        <section class="hero" aria-label="Resumen del equipo">
            <div class="hero-top">
                <h1 class="hero-host">{_esc(hostname)}</h1>
                <div class="verdict-stamp verdict-{status_tone}">
                    {svg_icon(status_icon, 16)}
                    <span>{_esc(status_label)}</span>
                </div>
            </div>
            <div class="hero-facts">
                <div class="hero-fact"><span class="hero-fact-label">Sistema</span><strong>{_esc(os_display)}</strong></div>
                <div class="hero-fact"><span class="hero-fact-label">Alcance</span><strong>{_esc(cat_display)}</strong></div>
                <div class="hero-fact"><span class="hero-fact-label">Capturado</span><strong>{_esc(dt_str)}</strong></div>
                <div class="hero-fact"><span class="hero-fact-label">Informe</span>{_copy_code(report_id)}</div>
            </div>
            {vitals_html}
        </section>

        {nav_html}
        {ai_html}
        {osi_html}
        {hw_html}
        {sec_html}
        {telemetry_html}

        <footer class="footer">
            <span>REI — Estación portátil de diagnóstico de campo</span>
            <span>Informe en memoria local</span>
        </footer>
    </main>
    <div id="copy-toast" class="toast" role="status" aria-live="polite"></div>
    <script>{_INTERACTION_JS}</script>
</body>
</html>"""


def render_index_html(reports: List[Any]) -> str:
    """Renderiza el índice local de informes disponibles en memoria."""
    if reports:
        ordered = sorted(reports, key=lambda r: getattr(r, "created_at", 0.0), reverse=True)
        rows: List[str] = []
        for rep in ordered:
            rep_id = str(getattr(rep, "report_id", ""))
            host = str(getattr(rep, "hostname", "Host remoto"))
            os_lbl = _format_os(getattr(rep, "os_type", ""))
            cat_lbl = _format_category(getattr(rep, "category", "GENERAL"))
            dt_str = _format_timestamp(getattr(rep, "created_at", 0.0) or 0.0)
            tone, st_lbl, st_ico = _status_meta(getattr(rep, "overall_status", "OK"))
            rows.append(
                f'<a class="report-row" href="/r/{_esc(rep_id)}">'
                "<div>"
                f'<div class="report-row-title">{_esc(host)}</div>'
                '<div class="report-row-meta">'
                f"<span>{_esc(os_lbl)}</span>"
                f"<span>{_esc(cat_lbl)}</span>"
                f"<span>{_esc(dt_str)}</span>"
                f"<span><code>#{_esc(rep_id)}</code></span>"
                "</div>"
                "</div>"
                f"{_pill(st_lbl, tone, st_ico)}"
                "</a>"
            )
        body_html = f"""
        <section class="section">
            <div class="section-head">
                <div class="section-title-group">
                    <span class="section-icon">{svg_icon("inbox", 18)}</span>
                    <div>
                        <h2>Informes capturados en esta sesión</h2>
                        <p class="section-desc">{len(ordered)} informe(s) disponibles en la memoria local de REI</p>
                    </div>
                </div>
            </div>
            <div class="report-list">
                {"".join(rows)}
            </div>
        </section>
        """
    else:
        body_html = f"""
        <div class="empty-panel">
            <div class="empty-icon">{svg_icon("inbox", 22)}</div>
            <h1>Todavía no hay informes en memoria</h1>
            <p>
                REI conserva los resultados de diagnóstico en memoria durante la sesión activa.
                Ejecuta una auditoría de endpoint desde la pantalla OLED del dispositivo para generar el primer informe.
            </p>
            <div class="empty-actions">
                <a class="tool-btn tool-btn-primary" href="/health">Verificar estado del servidor</a>
            </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>REI — Portal de Diagnóstico</title>
    <script>{_THEME_INIT_JS}</script>
    <style>{_BASE_CSS}</style>
</head>
<body>
    {_render_topbar(report_id=None, show_report_actions=False)}
    <main class="page">
        {body_html}
        <footer class="footer">
            <span>REI — Estación portátil de diagnóstico de campo</span>
            <span>Servidor local de telemetría</span>
        </footer>
    </main>
    <script>{_INTERACTION_JS}</script>
</body>
</html>"""


def render_not_found_html(report_id: str, has_latest: bool = False) -> str:
    """Renderiza una página 404 clara con orientación para el técnico."""
    actions: List[str] = []
    if has_latest:
        actions.append(
            f'<a class="tool-btn tool-btn-primary" href="/r/latest">'
            f'{svg_icon("diagnosis", 15)}<span>Abrir último informe</span></a>'
        )
    actions.append(
        f'<a class="tool-btn" href="/">'
        f'{svg_icon("arrow-left", 15)}<span>Ver lista de informes</span></a>'
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>REI — Informe no disponible</title>
    <script>{_THEME_INIT_JS}</script>
    <style>{_BASE_CSS}</style>
</head>
<body>
    {_render_topbar(report_id=report_id, show_report_actions=False)}
    <main class="page">
        <div class="empty-panel">
            <div class="empty-icon">{svg_icon("search-x", 22)}</div>
            <h1>El informe solicitado ya no está en memoria</h1>
            <p>
                No se encontró el identificador <code>{_esc(report_id)}</code>.
                REI almacena los informes únicamente en memoria RAM y se limpian cuando el dispositivo se reinicia.
                Vuelve a ejecutar el diagnóstico desde REI o abre el informe más reciente disponible.
            </p>
            <div class="empty-actions">
                {"".join(actions)}
            </div>
        </div>
    </main>
    <script>{_INTERACTION_JS}</script>
</body>
</html>"""

