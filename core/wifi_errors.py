"""
REI - Wi-Fi error catalog and classifier.

Single source of truth for every user-facing connection failure. Core modules
produce a ``WiFiErrorCode`` and UI layers render the catalog message, so raw
``nmcli`` stderr never reaches the OLED.

All catalog strings are plain ASCII and fit one 128x64 OLED line (<= 20 chars),
because the default bitmap-style font cannot render accented glyphs.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class WiFiErrorCode(str, Enum):
    """Structured reason behind a Wi-Fi connection outcome."""

    NONE = "NONE"
    ALREADY_CONNECTED = "ALREADY_CONNECTED"
    PASSWORD_REQUIRED = "PASSWORD_REQUIRED"
    AUTH_FAILED = "AUTH_FAILED"
    SAVED_SECRET_INVALID = "SAVED_SECRET_INVALID"
    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    NO_IP = "NO_IP"
    PROFILE_ERROR = "PROFILE_ERROR"
    READ_ONLY_FS = "READ_ONLY_FS"
    NO_BACKEND = "NO_BACKEND"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class WiFiErrorInfo:
    """User-facing presentation of one error code."""

    code: WiFiErrorCode
    message: str          # Short ASCII reason for the OLED (<= 20 chars).
    hint: str             # Short ASCII action hint ("" when none).
    needs_password: bool = False
    retryable: bool = False


ERROR_CATALOG: Dict[WiFiErrorCode, WiFiErrorInfo] = {
    WiFiErrorCode.NONE: WiFiErrorInfo(WiFiErrorCode.NONE, "", ""),
    WiFiErrorCode.ALREADY_CONNECTED: WiFiErrorInfo(
        WiFiErrorCode.ALREADY_CONNECTED, "YA CONECTADO", "K3=SALIR"
    ),
    WiFiErrorCode.PASSWORD_REQUIRED: WiFiErrorInfo(
        WiFiErrorCode.PASSWORD_REQUIRED, "SE NECESITA CLAVE", "K2=NUEVA CLAVE",
        needs_password=True, retryable=True,
    ),
    WiFiErrorCode.AUTH_FAILED: WiFiErrorInfo(
        WiFiErrorCode.AUTH_FAILED, "CLAVE INCORRECTA", "K2=REINTENTAR",
        needs_password=True, retryable=True,
    ),
    WiFiErrorCode.SAVED_SECRET_INVALID: WiFiErrorInfo(
        WiFiErrorCode.SAVED_SECRET_INVALID, "CLAVE CAMBIADA?", "K2=NUEVA CLAVE",
        needs_password=True, retryable=True,
    ),
    WiFiErrorCode.NOT_FOUND: WiFiErrorInfo(
        WiFiErrorCode.NOT_FOUND, "RED NO ENCONTRADA", "K3=SALIR", retryable=True,
    ),
    WiFiErrorCode.TIMEOUT: WiFiErrorInfo(
        WiFiErrorCode.TIMEOUT, "TIEMPO AGOTADO", "K2=REINTENTAR", retryable=True,
    ),
    WiFiErrorCode.NO_IP: WiFiErrorInfo(
        WiFiErrorCode.NO_IP, "CONECTADO SIN IP", "REVISE DHCP", retryable=True,
    ),
    WiFiErrorCode.PROFILE_ERROR: WiFiErrorInfo(
        WiFiErrorCode.PROFILE_ERROR, "PERFIL DE RED", "NO SE PUDO GUARDAR",
    ),
    WiFiErrorCode.READ_ONLY_FS: WiFiErrorInfo(
        WiFiErrorCode.READ_ONLY_FS, "SISTEMA SOLO LECTURA", "K3=SALIR",
    ),
    WiFiErrorCode.NO_BACKEND: WiFiErrorInfo(
        WiFiErrorCode.NO_BACKEND, "SIN GESTOR DE RED", "K3=SALIR",
    ),
    WiFiErrorCode.NOT_AUTHORIZED: WiFiErrorInfo(
        WiFiErrorCode.NOT_AUTHORIZED, "SIN PERMISOS", "K3=SALIR",
    ),
    WiFiErrorCode.UNKNOWN: WiFiErrorInfo(
        WiFiErrorCode.UNKNOWN, "FALLO DE CONEXION", "K3=SALIR", retryable=True,
    ),
}


def get_error_info(code: Optional[WiFiErrorCode]) -> WiFiErrorInfo:
    """Returns the catalog entry for a code, falling back to UNKNOWN."""
    if isinstance(code, WiFiErrorCode) and code in ERROR_CATALOG:
        return ERROR_CATALOG[code]
    if isinstance(code, str):
        try:
            enum_code = WiFiErrorCode(code)
        except ValueError:
            return ERROR_CATALOG[WiFiErrorCode.UNKNOWN]
        return ERROR_CATALOG.get(enum_code, ERROR_CATALOG[WiFiErrorCode.UNKNOWN])
    return ERROR_CATALOG[WiFiErrorCode.UNKNOWN]


# Substring rules, evaluated in order. The first match wins, so generic
# patterns (for example "psk") must come after specific ones (for example
# "key-mgmt: property is missing", which is a profile problem, not a wrong
# password). Evidence: NetworkManager rejects an update of a netplan-managed
# profile with "802-11-wireless-security.key-mgmt: property is missing"
# *before* ever trying the password.
_ERROR_RULES = (
    (WiFiErrorCode.READ_ONLY_FS, ("read-only", "read only", "solo lectura")),
    (WiFiErrorCode.NOT_AUTHORIZED, ("not authorized", "permission denied", "polkit")),
    (WiFiErrorCode.PROFILE_ERROR, (
        "key-mgmt",
        "property is missing",
        "invalid property",
        "connection-update",
        "failed to modify",
        "failed to add connection",
        "not a wifi connection",
    )),
    (WiFiErrorCode.SAVED_SECRET_INVALID, (
        "no secrets",
        "secrets were required",
        "no agents were available",
        "no-secrets",
    )),
    (WiFiErrorCode.AUTH_FAILED, (
        "4-way handshake",
        "4way",
        "wrong psk",
        "psk",
        "secret",
        "auth",
    )),
    (WiFiErrorCode.NOT_FOUND, (
        "not found",
        "no network with ssid",
        "no match",
        "no ap with matching ssid",
    )),
    (WiFiErrorCode.TIMEOUT, ("timed out", "timeout", "time out")),
    (WiFiErrorCode.ALREADY_CONNECTED, ("already active", "already connected")),
)

# Numeric NetworkManager device-state reasons printed by nmcli, for example
# "Error: Connection activation failed: (7) Secrets were required...".
_REASON_CODES = {
    "7": WiFiErrorCode.SAVED_SECRET_INVALID,   # no-secrets
    "53": WiFiErrorCode.NOT_FOUND,             # ssid-not-found
}


def classify_error(raw_error: str) -> WiFiErrorInfo:
    """
    Maps an nmcli/wpa_cli error string to a catalog entry.

    Never raises and never returns raw stderr: unknown errors fall back to the
    generic UNKNOWN entry so the OLED always shows a clear, short message.
    """
    if not raw_error:
        return ERROR_CATALOG[WiFiErrorCode.UNKNOWN]

    lowered = raw_error.lower()

    for reason, code in _REASON_CODES.items():
        if f"({reason})" in lowered:
            return ERROR_CATALOG[code]

    for code, tokens in _ERROR_RULES:
        for token in tokens:
            if token in lowered:
                return ERROR_CATALOG[code]

    return ERROR_CATALOG[WiFiErrorCode.UNKNOWN]
