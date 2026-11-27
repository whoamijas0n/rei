"""
REI - Contrato Base para Perfiles de Switches y Categorías de Problema (plugins/switches/base.py)

Define la abstracción ISwitchProfile (patrón Strategy) y las categorías canónicas
de diagnóstico para switches, replicando el modelo extensible de ENDPOINTS PC.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


# Categorías de resolución de problemas disponibles en el menú TIPO PROBLEMA.
# En este primer ciclo solo se expone PROBLEMAS GENERALES; futuras categorías
# (ej. TABLA MAC, POE / ENERGIA, STP / BUCLES) se registran aquí y en cada perfil.
SWITCH_MENU_CATEGORIES: Tuple[Tuple[str, str, str], ...] = (
    ("GENERAL", "PROBLEMAS GENERALES", "SWITCH"),
)

_CATEGORY_LABELS: Dict[str, str] = {
    "GENERAL": "PROBLEMAS GENERALES",
    "MAC_TABLE": "TABLA DE DIRECCIONES MAC",
    "POE": "POE Y ENERGIA",
    "STP": "SPANNING TREE / BUCLES",
    "INTERFACES": "PUERTOS Y ENLACES",
}


def normalize_switch_category(category: Optional[str]) -> str:
    """
    Normaliza cualquier texto de categoría de problema de switch a su clave canónica.
    Por defecto colapsa a 'GENERAL' (PROBLEMAS GENERALES).
    """
    token = (category or "").strip().upper()
    if not token:
        return "GENERAL"
    if "MAC" in token:
        return "MAC_TABLE"
    if "POE" in token or "ENERGIA" in token or "POWER" in token:
        return "POE"
    if "STP" in token or "BUCLE" in token or "SPANNING" in token:
        return "STP"
    if "PUERTO" in token or "INTERFA" in token or "CRC" in token:
        return "INTERFACES"
    if "GENERAL" in token or "COMPLETO" in token:
        return "GENERAL"
    return "GENERAL"


def get_switch_category_label(category: Optional[str]) -> str:
    """Devuelve el nombre legible en español para una categoría canónica."""
    norm = normalize_switch_category(category)
    return _CATEGORY_LABELS.get(norm, "PROBLEMAS GENERALES")


def sanitize_credentials_from_text(text: str, secrets: Sequence[Optional[str]]) -> str:
    """
    Elimina cualquier ocurrencia accidental de contraseñas o secretos del texto
    capturado de consola antes de guardarlo en reportes o enviarlo a IA.
    """
    cleaned = text or ""
    for secret in secrets:
        if secret and len(secret) >= 2:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


@dataclass(frozen=True)
class SwitchCommand:
    """Definición de un comando de solo lectura ejecutado sobre un switch."""

    key: str
    command: str
    label: str
    requires_privileged: bool = False
    timeout_seconds: float = 12.0


@dataclass
class SwitchAuditOutcome:
    """Resultado normalizado devuelto por un perfil tras auditar un switch."""

    vendor_id: str
    vendor_name: str
    category: str
    hostname: str
    overall_status: str  # "OK" | "WARN" | "CRIT"
    summary: str
    details: List[str] = field(default_factory=list)
    detail_pages: List[Dict[str, Any]] = field(default_factory=list)
    switch_audit: Dict[str, Any] = field(default_factory=dict)
    raw_commands: Dict[str, str] = field(default_factory=dict)


class ISwitchProfile(ABC):
    """
    Interfaz base para perfiles de fabricante de switches gestionables.
    Cada fabricante implementa sus regex de identificación, lista de comandos de
    solo lectura por categoría y parsers estructurados.
    """

    @property
    @abstractmethod
    def vendor_id(self) -> str:
        """Identificador interno del perfil (ej. 'cisco_ios', 'tplink_jetstream')."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Nombre corto para UI OLED y reportes (ej. 'CISCO IOS / XE')."""
        pass

    @property
    @abstractmethod
    def netmiko_device_type(self) -> str:
        """Tipo de dispositivo Netmiko para conexión serial."""
        pass

    @property
    def preferred_baudrates(self) -> Tuple[int, ...]:
        """Orden preferido de baudrates para este fabricante."""
        return (9600, 115200, 38400)

    @property
    def probe_commands(self) -> Tuple[str, ...]:
        """Comandos inofensivos de solo lectura para identificación activa (Fase 3)."""
        return ("show version",)

    @abstractmethod
    def match_passive(self, banner: str, prompt: str = "") -> float:
        """Puntuación [0.0..1.0] de coincidencia pasiva usando banner y prompt."""
        pass

    @abstractmethod
    def match_probe_output(self, output: str) -> float:
        """Puntuación [0.0..1.0] tras evaluar la salida de una sonda de solo lectura."""
        pass

    @abstractmethod
    def get_commands(self, category: str = "GENERAL", is_privileged: bool = True) -> List[SwitchCommand]:
        """Devuelve la lista ordenada de comandos de solo lectura para la categoría."""
        pass

    @abstractmethod
    def parse_outputs(
        self,
        command_outputs: Dict[str, str],
        context: Optional[Dict[str, Any]] = None,
    ) -> SwitchAuditOutcome:
        """Convierte las salidas crudas de los comandos en telemetría estructurada."""
        pass

    def prepare_session(self, connection: Any) -> None:
        """Desactiva paginación en la sesión CLI de forma segura si aplica."""
        if hasattr(connection, "send_command"):
            try:
                connection.send_command("terminal length 0", read_timeout=5)
            except Exception:
                pass

    def collect_audit(
        self,
        connection: Any,
        category: str = "GENERAL",
        is_privileged: bool = True,
        progress_cb: Optional[Callable[[str, float], None]] = None,
        context: Optional[Dict[str, Any]] = None,
        secrets_to_scrub: Sequence[Optional[str]] = (),
        prefetched_outputs: Optional[Dict[str, str]] = None,
    ) -> SwitchAuditOutcome:
        """
        Ejecuta secuencialmente los comandos de solo lectura del perfil sobre la
        conexión Netmiko/serial provista, aplicando progreso y sanitización.
        """
        norm_cat = normalize_switch_category(category)
        commands = self.get_commands(category=norm_cat, is_privileged=is_privileged)
        prefetched = prefetched_outputs or {}
        outputs: Dict[str, str] = {}
        skipped_privileged: List[str] = []

        self.prepare_session(connection)

        total = max(1, len(commands))
        for idx, cmd in enumerate(commands):
            if cmd.requires_privileged and not is_privileged:
                skipped_privileged.append(cmd.command)
                continue

            if progress_cb:
                pct = 0.35 + (0.50 * (idx / float(total)))
                progress_cb(cmd.label, pct)

            if cmd.command in prefetched and prefetched[cmd.command].strip():
                cleaned = sanitize_credentials_from_text(prefetched[cmd.command], secrets_to_scrub)
                outputs[cmd.key] = cleaned.strip()
                continue

            try:
                if hasattr(connection, "send_command"):
                    raw_out = connection.send_command(
                        cmd.command,
                        read_timeout=cmd.timeout_seconds,
                    )
                else:
                    raw_out = ""
            except Exception as ex:
                raw_out = f"[ERROR ejecutando '{cmd.command}': {type(ex).__name__}]"

            cleaned = sanitize_credentials_from_text(str(raw_out or ""), secrets_to_scrub)
            outputs[cmd.key] = cleaned.strip()

        merged_ctx = dict(context or {})
        merged_ctx["category"] = norm_cat
        merged_ctx["is_privileged"] = is_privileged
        merged_ctx["skipped_privileged"] = skipped_privileged

        if progress_cb:
            progress_cb("Procesando datos...", 0.90)

        return self.parse_outputs(outputs, context=merged_ctx)


def regex_first_group(pattern: str, text: str, flags: int = 0, default: str = "") -> str:
    """Utilidad auxiliar para extraer el primer grupo de una expresión regular."""
    match = re.search(pattern, text or "", flags)
    if match and match.groups():
        return (match.group(1) or "").strip()
    return default

