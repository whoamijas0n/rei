"""
REI - Resolución de distribuciones y detección de teclados físicos USB.

Traduce scancodes crudos de Linux evdev a caracteres imprimibles para las
distribuciones soportadas (LATAM, ES y US), incluyendo Shift, CapsLock, AltGr,
tecla ISO 102 (< >), bloque numérico y composición de teclas muertas.
"""

from dataclasses import dataclass, field
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple
import unicodedata

logger = logging.getLogger("REI.UI.KeyboardLayouts")

SUPPORTED_LAYOUTS: Tuple[str, ...] = ("latam", "es", "us")

_LAYOUT_ALIASES: Dict[str, str] = {
    "us": "us",
    "en": "us",
    "en-us": "us",
    "en_us": "us",
    "english": "us",
    "es": "es",
    "es-es": "es",
    "es_es": "es",
    "spanish": "es",
    "esp": "es",
    "latam": "latam",
    "la": "latam",
    "latinoamerica": "latam",
    "latinoamericana": "latam",
    "latin-american": "latam",
    "latin_american": "latam",
    "es-419": "latam",
    "es_419": "latam",
}

_LAYOUT_BADGES: Dict[str, str] = {
    "latam": "LA",
    "es": "ES",
    "us": "US",
}

_NON_KEYBOARD_NAME_TOKENS: Tuple[str, ...] = (
    "vc4-hdmi",
    "hdmi jack",
    "consumer control",
    "system control",
    "pwr_button",
    "power button",
    "video bus",
)

DEAD_COMBINING: Dict[str, str] = {
    "dead_acute": "\u0301",
    "dead_grave": "\u0300",
    "dead_circumflex": "\u0302",
    "dead_diaeresis": "\u0308",
    "dead_tilde": "\u0303",
    "dead_cedilla": "\u0327",
}

DEAD_LITERALS: Dict[str, str] = {
    "dead_acute": "´",
    "dead_grave": "`",
    "dead_circumflex": "^",
    "dead_diaeresis": "¨",
    "dead_tilde": "~",
    "dead_cedilla": "¸",
}


@dataclass(frozen=True)
class KeyBinding:
    """Caracteres y teclas muertas asociadas a una tecla física."""

    normal: str = ""
    shift: str = ""
    altgr: str = ""
    altgr_shift: str = ""
    normal_dead: str = ""
    shift_dead: str = ""
    altgr_dead: str = ""
    altgr_shift_dead: str = ""
    is_letter: bool = False


@dataclass
class KeyboardMap:
    """Mapa resuelto de scancode evdev a KeyBinding para una distribución."""

    layout: str = "us"
    bindings: Dict[int, KeyBinding] = field(default_factory=dict)


def normalize_layout_name(name: Optional[str]) -> str:
    """Normaliza el nombre de una distribución a 'latam', 'es' o 'us'."""
    if not name:
        return "us"
    clean = str(name).strip().strip('"\'').lower()
    clean = clean.split(",")[0].strip()
    return _LAYOUT_ALIASES.get(clean, "us")


def get_layout_badge(layout: Optional[str]) -> str:
    """Devuelve etiqueta corta de 2 caracteres para mostrar en OLED."""
    return _LAYOUT_BADGES.get(normalize_layout_name(layout), "US")


def next_layout_name(current_layout: Optional[str]) -> str:
    """Avanza cíclicamente entre las distribuciones soportadas (latam -> es -> us)."""
    norm = normalize_layout_name(current_layout)
    idx = SUPPORTED_LAYOUTS.index(norm)
    return SUPPORTED_LAYOUTS[(idx + 1) % len(SUPPORTED_LAYOUTS)]


def detect_system_layout(
    configured_layout: Optional[str] = None,
    default_keyboard_path: str = "/etc/default/keyboard",
    vconsole_path: str = "/etc/vconsole.conf",
) -> str:
    """
    Detecta la distribución activa del teclado físico conectado.

    Prioridad:
    1. Parámetro explícito (settings.json) distinto de 'auto'.
    2. Variable de entorno REI_KEYBOARD_LAYOUT distinta de 'auto'.
    3. XKBLAYOUT en /etc/default/keyboard (Raspberry Pi OS).
    4. KEYMAP en /etc/vconsole.conf.
    5. 'us' como respaldo seguro.
    """
    if configured_layout and str(configured_layout).strip().lower() != "auto":
        return normalize_layout_name(configured_layout)

    env_layout = os.environ.get("REI_KEYBOARD_LAYOUT", "").strip()
    if env_layout and env_layout.lower() != "auto":
        return normalize_layout_name(env_layout)

    for path, key in (
        (default_keyboard_path, "XKBLAYOUT"),
        (vconsole_path, "KEYMAP"),
    ):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    match = re.match(
                        rf"\s*{re.escape(key)}\s*=\s*\"?([^\"\s#]+)",
                        line,
                        re.IGNORECASE,
                    )
                    if match:
                        return normalize_layout_name(match.group(1))
        except OSError:
            continue
        except Exception as ex:
            logger.debug(f"No se pudo leer layout desde {path}: {ex}")

    return "us"


def compose_dead_key(dead_name: str, char: str) -> str:
    """
    Compone una tecla muerta con el siguiente carácter imprimible.

    - Espacio devuelve el símbolo literal de la tecla muerta (ej. '^', '~', '`').
    - Vocales/letras componibles devuelven el carácter acentuado en NFC.
    - Otros caracteres devuelven símbolo literal + carácter para no perder símbolos de claves.
    """
    literal = DEAD_LITERALS.get(dead_name, "")
    if not char or char == " ":
        return literal

    combining = DEAD_COMBINING.get(dead_name)
    if combining:
        composed = unicodedata.normalize("NFC", char + combining)
        if len(composed) == 1 and composed != char:
            return composed

    return f"{literal}{char}"


def resolve_key_binding(
    binding: KeyBinding,
    shift: bool = False,
    caps_lock: bool = False,
    altgr: bool = False,
    pending_dead: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Resuelve un KeyBinding dado el estado actual de modificadores y tecla muerta pendiente.

    Retorna (texto_emitido, nueva_tecla_muerta_pendiente).
    """
    use_shift = (shift ^ caps_lock) if (binding.is_letter and not altgr) else shift

    char = ""
    dead = ""
    if altgr and use_shift:
        char = binding.altgr_shift
        dead = binding.altgr_shift_dead
    elif altgr:
        char = binding.altgr
        dead = binding.altgr_dead
    elif use_shift:
        char = binding.shift
        dead = binding.shift_dead
    else:
        char = binding.normal
        dead = binding.normal_dead

    # Respaldo si AltGr+Shift o AltGr no define valor específico en letras
    if not char and not dead and altgr:
        eff_shift = (shift ^ caps_lock) if binding.is_letter else shift
        char = binding.shift if eff_shift else binding.normal
        dead = binding.shift_dead if eff_shift else binding.normal_dead

    if dead:
        if pending_dead == dead:
            return DEAD_LITERALS.get(dead, ""), None
        if pending_dead:
            return DEAD_LITERALS.get(pending_dead, ""), dead
        return "", dead

    if not char:
        return "", pending_dead

    if pending_dead:
        return compose_dead_key(pending_dead, char), None

    return char, None


def _base_us_bindings() -> Dict[str, KeyBinding]:
    """Construye tabla base US ANSI con letras, dígitos, teclado numérico y signos."""
    table: Dict[str, KeyBinding] = {}

    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        table[f"KEY_{letter}"] = KeyBinding(
            normal=letter.lower(),
            shift=letter,
            is_letter=True,
        )

    digits = [
        ("KEY_1", "1", "!"),
        ("KEY_2", "2", "@"),
        ("KEY_3", "3", "#"),
        ("KEY_4", "4", "$"),
        ("KEY_5", "5", "%"),
        ("KEY_6", "6", "^"),
        ("KEY_7", "7", "&"),
        ("KEY_8", "8", "*"),
        ("KEY_9", "9", "("),
        ("KEY_0", "0", ")"),
    ]
    for code_name, norm, sh in digits:
        table[code_name] = KeyBinding(normal=norm, shift=sh)

    keypad = [
        ("KEY_KP0", "0"),
        ("KEY_KP1", "1"),
        ("KEY_KP2", "2"),
        ("KEY_KP3", "3"),
        ("KEY_KP4", "4"),
        ("KEY_KP5", "5"),
        ("KEY_KP6", "6"),
        ("KEY_KP7", "7"),
        ("KEY_KP8", "8"),
        ("KEY_KP9", "9"),
        ("KEY_KPDOT", "."),
        ("KEY_KPPLUS", "+"),
        ("KEY_KPMINUS", "-"),
        ("KEY_KPASTERISK", "*"),
        ("KEY_KPSLASH", "/"),
    ]
    for code_name, ch in keypad:
        table[code_name] = KeyBinding(normal=ch, shift=ch)

    table.update({
        "KEY_SPACE": KeyBinding(normal=" ", shift=" "),
        "KEY_MINUS": KeyBinding(normal="-", shift="_"),
        "KEY_EQUAL": KeyBinding(normal="=", shift="+"),
        "KEY_LEFTBRACE": KeyBinding(normal="[", shift="{"),
        "KEY_RIGHTBRACE": KeyBinding(normal="]", shift="}"),
        "KEY_BACKSLASH": KeyBinding(normal="\\", shift="|"),
        "KEY_SEMICOLON": KeyBinding(normal=";", shift=":"),
        "KEY_APOSTROPHE": KeyBinding(normal="'", shift='"'),
        "KEY_GRAVE": KeyBinding(normal="`", shift="~"),
        "KEY_COMMA": KeyBinding(normal=",", shift="<"),
        "KEY_DOT": KeyBinding(normal=".", shift=">"),
        "KEY_SLASH": KeyBinding(normal="/", shift="?"),
        "KEY_102ND": KeyBinding(normal="<", shift=">", altgr="\\"),
    })
    return table


def _layout_bindings(layout: str) -> Dict[str, KeyBinding]:
    """Devuelve los KeyBindings por nombre de scancode evdev para la distribución dada."""
    normalized = normalize_layout_name(layout)
    table = _base_us_bindings()

    if normalized == "us":
        return table

    # Común a ES y LATAM (verificado con ckbcomp -model pc105)
    table.update({
        "KEY_Q": KeyBinding(normal="q", shift="Q", altgr="@", is_letter=True),
        "KEY_1": KeyBinding(normal="1", shift="!", altgr="|"),
        "KEY_2": KeyBinding(normal="2", shift='"', altgr="@"),
        "KEY_4": KeyBinding(normal="4", shift="$", altgr="~"),
        "KEY_5": KeyBinding(normal="5", shift="%"),
        "KEY_6": KeyBinding(normal="6", shift="&", altgr="¬"),
        "KEY_7": KeyBinding(normal="7", shift="/", altgr="{"),
        "KEY_8": KeyBinding(normal="8", shift="(", altgr="["),
        "KEY_9": KeyBinding(normal="9", shift=")", altgr="]"),
        "KEY_0": KeyBinding(normal="0", shift="=", altgr="}"),
        "KEY_MINUS": KeyBinding(normal="'", shift="?", altgr="\\", altgr_shift="¿"),
        "KEY_COMMA": KeyBinding(normal=",", shift=";"),
        "KEY_DOT": KeyBinding(normal=".", shift=":"),
        "KEY_SLASH": KeyBinding(normal="-", shift="_"),
    })

    if normalized == "es":
        table.update({
            "KEY_3": KeyBinding(normal="3", shift="·", altgr="#"),
            "KEY_EQUAL": KeyBinding(normal="¡", shift="¿", altgr_dead="dead_cedilla"),
            "KEY_GRAVE": KeyBinding(normal="º", shift="ª", altgr="\\", altgr_shift="\\"),
            "KEY_LEFTBRACE": KeyBinding(
                normal_dead="dead_grave",
                shift_dead="dead_circumflex",
                altgr="[",
            ),
            "KEY_RIGHTBRACE": KeyBinding(normal="+", shift="*", altgr="]"),
            "KEY_SEMICOLON": KeyBinding(normal="ñ", shift="Ñ", altgr_dead="dead_tilde", is_letter=True),
            "KEY_APOSTROPHE": KeyBinding(
                normal_dead="dead_acute",
                shift_dead="dead_diaeresis",
                altgr="{",
            ),
            "KEY_BACKSLASH": KeyBinding(normal="ç", shift="Ç", altgr="}", is_letter=True),
            "KEY_102ND": KeyBinding(normal="<", shift=">", altgr="|", altgr_shift="¦"),
        })
    elif normalized == "latam":
        table.update({
            "KEY_3": KeyBinding(normal="3", shift="#", altgr="·"),
            "KEY_EQUAL": KeyBinding(normal="¿", shift="¡", altgr_dead="dead_cedilla"),
            "KEY_GRAVE": KeyBinding(normal="|", shift="°", altgr="¬", altgr_shift="¬"),
            "KEY_LEFTBRACE": KeyBinding(
                normal_dead="dead_acute",
                shift_dead="dead_diaeresis",
                altgr_dead="dead_diaeresis",
            ),
            "KEY_RIGHTBRACE": KeyBinding(normal="+", shift="*", altgr="~"),
            "KEY_SEMICOLON": KeyBinding(normal="ñ", shift="Ñ", altgr="~", is_letter=True),
            "KEY_APOSTROPHE": KeyBinding(
                normal="{",
                shift="[",
                altgr_dead="dead_circumflex",
            ),
            "KEY_BACKSLASH": KeyBinding(
                normal="}",
                shift="]",
                altgr_dead="dead_grave",
            ),
            "KEY_102ND": KeyBinding(normal="<", shift=">", altgr="\\", altgr_shift="|"),
        })

    return table


def build_key_map(layout: str, ecodes_mod: Any = None) -> KeyboardMap:
    """Construye el mapa de scancodes evdev para la distribución solicitada."""
    normalized = normalize_layout_name(layout)
    if ecodes_mod is None:
        try:
            from evdev import ecodes as ecodes_mod
        except ImportError:
            logger.debug("python-evdev no disponible; KeyboardMap vacío.")
            return KeyboardMap(layout=normalized)

    bindings: Dict[int, KeyBinding] = {}
    for key_name, binding in _layout_bindings(normalized).items():
        code = getattr(ecodes_mod, key_name, None)
        if code is not None:
            bindings[code] = binding

    return KeyboardMap(layout=normalized, bindings=bindings)


def is_real_keyboard_capabilities(
    caps: Dict[int, Any],
    device_name: str = "",
    ecodes_mod: Any = None,
) -> bool:
    """
    Determina si un nodo evdev representa un teclado físico alfanumérico real.

    Filtra falsos positivos de Raspberry Pi y receptores compuestos:
    - vc4-hdmi (/dev/input/event0), que solo expone KEY_ENTER.
    - Dispositivos Consumer Control / System Control o ratones puros.
    """
    if ecodes_mod is None:
        try:
            from evdev import ecodes as ecodes_mod
        except ImportError:
            return False

    lower_name = (device_name or "").lower()
    if any(token in lower_name for token in _NON_KEYBOARD_NAME_TOKENS):
        return False

    ev_key = getattr(ecodes_mod, "EV_KEY", 1)
    if ev_key not in caps:
        return False

    keys = set(caps.get(ev_key, []))
    required = {
        getattr(ecodes_mod, f"KEY_{letter}")
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if hasattr(ecodes_mod, f"KEY_{letter}")
    }
    for req_name in ("KEY_ENTER", "KEY_SPACE", "KEY_BACKSPACE", "KEY_LEFTSHIFT"):
        code = getattr(ecodes_mod, req_name, None)
        if code is not None:
            required.add(code)

    return len(required) >= 26 and required.issubset(keys)

