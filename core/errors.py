"""
REI - Formateador Estructurado de Errores y Avisos para OLED.

Traduce excepciones de Python, fallos de red, errores de subprocess/APT/Git,
permisos del sistema y estados USB/HID en avisos consistentes, claros y
accionables en español para la pantalla OLED de 128x64 px.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from core.interfaces import DiagnosticStatus, Severity
from core.parser import sanitize_oled_text


class NoticeLevel(str, Enum):
    """Nivel semántico unificado para avisos, alertas y cabeceras OLED."""

    OK = "OK"
    INFO = "INFO"
    WARNING = "AVISO"
    ERROR = "ERROR"


@dataclass(frozen=True)
class FormattedNotice:
    """Aviso estructurado listo para mostrarse en NoticeView o DetailCardView."""

    title: str
    lines: List[str] = field(default_factory=list)
    level: NoticeLevel = NoticeLevel.INFO
    action_hint: str = ""


def severity_to_notice_level(value: Any) -> NoticeLevel:
    """Convierte Severity, DiagnosticStatus o etiquetas de texto en NoticeLevel."""
    if isinstance(value, NoticeLevel):
        return value
    if isinstance(value, Severity):
        if value == Severity.OK:
            return NoticeLevel.OK
        if value == Severity.WARNING:
            return NoticeLevel.WARNING
        if value == Severity.CRITICAL:
            return NoticeLevel.ERROR
        return NoticeLevel.INFO
    if isinstance(value, DiagnosticStatus):
        if value == DiagnosticStatus.SUCCESS:
            return NoticeLevel.OK
        if value == DiagnosticStatus.WARNING:
            return NoticeLevel.WARNING
        if value == DiagnosticStatus.FAILED:
            return NoticeLevel.ERROR
        return NoticeLevel.INFO

    token = str(value or "").strip().upper()
    if token in ("OK", "SUCCESS", "LISTO", "CONECTADO"):
        return NoticeLevel.OK
    if token in ("WARN", "WARNING", "AVISO", "ALERTA"):
        return NoticeLevel.WARNING
    if token in ("ERR", "ERROR", "FAIL", "FAILED", "CRIT", "CRITICAL", "FALLO"):
        return NoticeLevel.ERROR
    return NoticeLevel.INFO


def format_oled_error(
    error: Any,
    context: str = "",
    fallback_title: str = "ERROR",
) -> FormattedNotice:
    """
    Convierte una excepción o mensaje técnico en un aviso legible en español.
    Evita mostrar trazas crudas, códigos Errno o textos cortados sin sentido.
    """
    raw_msg = sanitize_oled_text(str(error or "")).strip()
    lower = raw_msg.lower()
    ctx = sanitize_oled_text(context).strip().upper()

    # 1. Errores de conectividad / red / DNS
    if any(
        tok in lower
        for tok in (
            "network is unreachable",
            "temporary failure in name resolution",
            "name or service not known",
            "nodename nor servname",
            "could not resolve host",
            "no route to host",
            "sin conexion",
            "connection refused",
            "connection reset",
        )
    ):
        return FormattedNotice(
            title="SIN CONEXION",
            lines=[
                "No se pudo conectar a la red.",
                "Verifique Wi-Fi o cable y reintente.",
            ],
            level=NoticeLevel.ERROR,
        )

    # 2. Timeouts / tiempo de espera agotado
    if any(tok in lower for tok in ("timed out", "timeout", "tiempo agotado", "tiempo de espera")):
        if "hid" in lower or "usb" in lower or "HID" in ctx:
            return FormattedNotice(
                title="TIMEOUT USB",
                lines=[
                    "El equipo no responde por HID.",
                    "Revise cable de datos y puerto USB.",
                ],
                level=NoticeLevel.WARNING,
            )
        return FormattedNotice(
            title="TIEMPO AGOTADO",
            lines=[
                "La operacion tardo demasiado.",
                "Verifique conexion y reintente.",
            ],
            level=NoticeLevel.WARNING,
        )

    # 3. Permisos insuficientes
    if any(
        tok in lower
        for tok in (
            "permission denied",
            "operation not permitted",
            "errno 13",
            "eacces",
            "eperm",
            "are you root",
            "superuser",
        )
    ):
        return FormattedNotice(
            title="SIN PERMISOS",
            lines=[
                "Faltan privilegios de sistema.",
                "Ejecute REI mediante rei.service.",
            ],
            level=NoticeLevel.ERROR,
        )

    # 4. Dispositivo USB HID desconectado / no listo
    if any(
        tok in lower
        for tok in ("/dev/hidg0", "hidg0", "usb desconectado", "gadget", "udc")
    ):
        return FormattedNotice(
            title="USB NO LISTO",
            lines=[
                "Interfaz USB HID no disponible.",
                "Active Modo HID en Utilidades > USB.",
            ],
            level=NoticeLevel.WARNING,
        )

    # 5. Bloqueo de APT / dpkg
    if any(
        tok in lower
        for tok in ("could not get lock", "dpkg was interrupted", "unable to acquire the dpkg")
    ):
        return FormattedNotice(
            title="APT OCUPADO",
            lines=[
                "El gestor de paquetes esta en uso.",
                "Espere unos segundos y reintente.",
            ],
            level=NoticeLevel.WARNING,
        )

    # 6. Conflictos o fallos de Git
    if any(
        tok in lower
        for tok in (
            "not possible to fast-forward",
            "local changes",
            "would be overwritten",
            "merge conflict",
            "dubious ownership",
        )
    ):
        return FormattedNotice(
            title="CONFLICTO GIT",
            lines=[
                "Existen cambios locales en REI.",
                "Revise el repositorio antes de actualizar.",
            ],
            level=NoticeLevel.WARNING,
        )

    # 7. Binario o archivo no encontrado
    if any(tok in lower for tok in ("no such file", "not found", "errno 2", "filenotfound")):
        return FormattedNotice(
            title="RECURSO AUSENTE",
            lines=[
                "Falta un archivo o herramienta.",
                raw_msg if len(raw_msg) <= 44 else "Revise la instalacion de REI.",
            ],
            level=NoticeLevel.ERROR,
        )

    # 8. Fallback limpio y legible
    title = ctx or sanitize_oled_text(fallback_title).strip().upper() or "ERROR"
    if raw_msg:
        # Limpiar prefijos técnicos como "[Errno 111] "
        cleaned = raw_msg
        if cleaned.startswith("[Errno") and "]" in cleaned:
            cleaned = cleaned.split("]", 1)[1].strip()
        lines = [cleaned or "Fallo inesperado en la operacion."]
    else:
        lines = ["Ocurrio un fallo inesperado.", "Revise el registro del sistema."]

    return FormattedNotice(
        title=title,
        lines=lines,
        level=NoticeLevel.ERROR,
    )


def sanitize_detail_lines(
    details: Optional[List[str]],
    fallback_summary: str = "",
) -> List[str]:
    """Limpia líneas de detalle preservando contenido legible y no vacío."""
    cleaned: List[str] = []
    if details:
        for item in details:
            txt = sanitize_oled_text(item).strip()
            if txt:
                cleaned.append(txt)
    if not cleaned and fallback_summary:
        txt = sanitize_oled_text(fallback_summary).strip()
        if txt:
            cleaned.append(txt)
    return cleaned

