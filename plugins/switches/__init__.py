"""
REI - Registro de Perfiles de Switches (plugins/switches/__init__.py)

Centraliza los perfiles por fabricante para autodetección pasiva/activa y
selección manual desde el menú SWITCHES -> CONSOLA SERIAL.
"""

from typing import Dict, List, Optional, Sequence

from .base import (
    ISwitchProfile,
    SWITCH_MENU_CATEGORIES,
    SwitchAuditOutcome,
    SwitchCommand,
    get_switch_category_label,
    normalize_switch_category,
    sanitize_credentials_from_text,
)
from .cisco_ios import CiscoIOSProfile, get_mock_cisco_2960s_outputs
from .generic_serial import GenericSerialProfile
from .tplink_jetstream import TPLinkJetStreamProfile


class SwitchProfileRegistry:
    """Registro extensible de perfiles de switches por fabricante."""

    def __init__(self, profiles: Optional[Sequence[ISwitchProfile]] = None):
        self._profiles: Dict[str, ISwitchProfile] = {}
        default_list = profiles if profiles is not None else (
            CiscoIOSProfile(),
            TPLinkJetStreamProfile(),
            GenericSerialProfile(),
        )
        for prof in default_list:
            self.register(prof)

    def register(self, profile: ISwitchProfile) -> None:
        """Registra o reemplaza un perfil de fabricante por su vendor_id."""
        self._profiles[profile.vendor_id] = profile

    def get(self, vendor_id: Optional[str]) -> Optional[ISwitchProfile]:
        """Obtiene un perfil registrado por su vendor_id."""
        if not vendor_id:
            return None
        return self._profiles.get(vendor_id.strip().lower())

    def get_fallback(self) -> ISwitchProfile:
        """Devuelve el perfil genérico de respaldo."""
        return self._profiles.get("generic") or GenericSerialProfile()

    def list_profiles(self) -> List[ISwitchProfile]:
        """Devuelve todos los perfiles registrados en orden de prioridad."""
        return list(self._profiles.values())

    def detect_passive(self, banner: str, prompt: str = "", min_score: float = 0.25) -> Optional[ISwitchProfile]:
        """Selecciona el perfil con mayor confianza pasiva a partir de banner y prompt."""
        best_profile: Optional[ISwitchProfile] = None
        best_score = min_score
        for prof in self._profiles.values():
            if prof.vendor_id == "generic":
                continue
            score = prof.match_passive(banner, prompt)
            if score > best_score:
                best_score = score
                best_profile = prof
        return best_profile

    def detect_from_probe(self, output: str, min_score: float = 0.30) -> ISwitchProfile:
        """Selecciona el mejor perfil tras evaluar la salida de una sonda segura."""
        best_profile: Optional[ISwitchProfile] = None
        best_score = min_score
        for prof in self._profiles.values():
            if prof.vendor_id == "generic":
                continue
            score = prof.match_probe_output(output)
            if score > best_score:
                best_score = score
                best_profile = prof
        return best_profile or self.get_fallback()


DEFAULT_SWITCH_REGISTRY = SwitchProfileRegistry()

__all__ = [
    "ISwitchProfile",
    "SWITCH_MENU_CATEGORIES",
    "SwitchAuditOutcome",
    "SwitchCommand",
    "SwitchProfileRegistry",
    "DEFAULT_SWITCH_REGISTRY",
    "CiscoIOSProfile",
    "TPLinkJetStreamProfile",
    "GenericSerialProfile",
    "get_mock_cisco_2960s_outputs",
    "get_switch_category_label",
    "normalize_switch_category",
    "sanitize_credentials_from_text",
]

