"""
REI - Keyboard layout resolution for physical USB keyboards.

This module translates raw evdev scancodes into printable characters for the
layouts supported by the device: US, Spanish (ES) and Latin American (LATAM).
It also exposes helpers to detect the console layout and to classify an evdev
device as a real keyboard.

The implementation deliberately avoids external dependencies beyond evdev. When
evdev is not installed, build_key_map() returns an empty map so the rest of the
application can keep working in simulation mode.
"""

from dataclasses import dataclass, field
import logging
import os
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger("REI.UI.KeyboardLayouts")


SUPPORTED_LAYOUTS = ("us", "es", "latam")

_LAYOUT_ALIASES = {
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
    "latinoamerica": "latam",
    "latinoamericana": "latam",
    "latin-american": "latam",
    "latin_american": "latam",
    "es-419": "latam",
    "es_419": "latam",
}


@dataclass(frozen=True)
class KeyBinding:
    """Printable characters and dead keys associated with a physical key."""

    normal: str = ""
    shift: str = ""
    altgr: str = ""
    altgr_shift: str = ""
    normal_dead: str = ""
    shift_dead: str = ""
    altgr_dead: str = ""
    altgr_shift_dead: str = ""


@dataclass
class KeyboardMap:
    """Scancode map for one keyboard layout."""

    layout: str = "us"
    bindings: Dict[int, KeyBinding] = field(default_factory=dict)


# Dead keys are represented by their XKB names. The combining mark is used to
# compose the next printable character with unicodedata.normalize("NFC", ...).
DEAD_COMBINING: Dict[str, str] = {
    "dead_acute": "\u0301",
    "dead_grave": "\u0300",
    "dead_circumflex": "\u0302",
    "dead_diaeresis": "\u0308",
    "dead_tilde": "\u0303",
    "dead_cedilla": "\u0327",
    "dead_caron": "\u030c",
    "dead_breve": "\u0306",
    "dead_abovering": "\u030a",
    "dead_doubleacute": "\u030b",
    "dead_macron": "\u0304",
    "dead_ogonek": "\u0328",
    "dead_abovedot": "\u0307",
    "dead_belowdot": "\u0323",
}

DEAD_LITERALS: Dict[str, str] = {
    "dead_acute": "´",
    "dead_grave": "`",
    "dead_circumflex": "^",
    "dead_diaeresis": "¨",
    "dead_tilde": "~",
    "dead_cedilla": "¸",
    "dead_caron": "ˇ",
    "dead_breve": "˘",
    "dead_abovering": "˚",
    "dead_doubleacute": "˝",
    "dead_macron": "¯",
    "dead_ogonek": "˛",
    "dead_abovedot": "˙",
    "dead_belowdot": "̣",
}


def normalize_layout_name(name: Optional[str]) -> str:
    """Normalizes a layout name to one of the supported identifiers."""
    if not name:
        return "us"
    clean = str(name).strip().strip('"\'').lower()
    # XKB may specify several layouts separated by commas; the first one is
    # the active group on this device.
    clean = clean.split(",")[0].strip()
    return _LAYOUT_ALIASES.get(clean, "us")


def detect_system_layout(
    default_keyboard_path: str = "/etc/default/keyboard",
    vconsole_path: str = "/etc/vconsole.conf",
) -> str:
    """
    Detects the active console layout.

    Priority:
    1. REI_KEYBOARD_LAYOUT environment variable.
    2. XKBLAYOUT in /etc/default/keyboard (Raspberry Pi OS).
    3. KEYMAP in /etc/vconsole.conf.
    4. US as a safe fallback.
    """
    env_layout = os.environ.get("REI_KEYBOARD_LAYOUT", "").strip()
    if env_layout and env_layout.lower() != "auto":
        return normalize_layout_name(env_layout)

    for path, key in (
        (default_keyboard_path, "XKBLAYOUT"),
        (vconsole_path, "KEYMAP"),
    ):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    match = re.match(
                        rf"\s*{re.escape(key)}\s*=\s*\"?([^\"\s#]+)",
                        line,
                        re.IGNORECASE,
                    )
                    if match:
                        return normalize_layout_name(match.group(1))
        except OSError:
            continue
        except Exception as exc:
            logger.debug(f"Could not read keyboard layout from {path}: {exc}")

    return "us"


def compose_dead_key(dead_name: str, char: str) -> str:
    """
    Composes a dead key with the next printable character.

    Returns the composed character when Unicode can express it, or the literal
    dead-key character followed by ``char`` when it cannot. A space after a
    dead key yields the literal character (e.g. dead acute + space -> '´').
    """
    if not char:
        return ""

    literal = DEAD_LITERALS.get(dead_name, "")
    if char == " ":
        return literal or " "

    combining = DEAD_COMBINING.get(dead_name)
    if not combining:
        return literal + char

    import unicodedata

    composed = unicodedata.normalize("NFC", char + combining)
    if composed and len(composed) == 1:
        return composed
    return literal + char


def _layout_bindings(layout: str) -> Dict[str, KeyBinding]:
    """Returns the key-name based binding table for a layout."""
    # Base QWERTY letters shared by all supported layouts.
    table: Dict[str, KeyBinding] = {}
    for letter in "abcdefghijklmnopqrstuvwxyz":
        table[f"KEY_{letter.upper()}"] = KeyBinding(
            normal=letter,
            shift=letter.upper(),
        )

    # Numeric row and punctuation. The default table is US; ES/LATAM override
    # the keys whose symbols differ.
    for digit, shifted in (
        ("1", "!"), ("2", "@"), ("3", "#"), ("4", "$"), ("5", "%"),
        ("6", "^"), ("7", "&"), ("8", "*"), ("9", "("), ("0", ")"),
    ):
        table[f"KEY_{digit}"] = KeyBinding(normal=digit, shift=shifted)

    table.update({
        "KEY_GRAVE": KeyBinding(normal="`", shift="~"),
        "KEY_MINUS": KeyBinding(normal="-", shift="_"),
        "KEY_EQUAL": KeyBinding(normal="=", shift="+"),
        "KEY_LEFTBRACE": KeyBinding(normal="[", shift="{"),
        "KEY_RIGHTBRACE": KeyBinding(normal="]", shift="}"),
        "KEY_BACKSLASH": KeyBinding(normal="\\", shift="|"),
        "KEY_SEMICOLON": KeyBinding(normal=";", shift=":"),
        "KEY_APOSTROPHE": KeyBinding(normal="'", shift='"'),
        "KEY_COMMA": KeyBinding(normal=",", shift="<"),
        "KEY_DOT": KeyBinding(normal=".", shift=">"),
        "KEY_SLASH": KeyBinding(normal="/", shift="?"),
        "KEY_102ND": KeyBinding(normal="<", shift=">"),
        "KEY_SPACE": KeyBinding(normal=" ", shift=" "),
    })

    # Keypad: always independent of the main layout.
    keypad = {
        "KEY_KP0": ("0", "0"), "KEY_KP1": ("1", "1"),
        "KEY_KP2": ("2", "2"), "KEY_KP3": ("3", "3"),
        "KEY_KP4": ("4", "4"), "KEY_KP5": ("5", "5"),
        "KEY_KP6": ("6", "6"), "KEY_KP7": ("7", "7"),
        "KEY_KP8": ("8", "8"), "KEY_KP9": ("9", "9"),
        "KEY_KPDOT": (".", "."), "KEY_KPPLUS": ("+", "+"),
        "KEY_KPMINUS": ("-", "-"), "KEY_KPASTERISK": ("*", "*"),
        "KEY_KPSLASH": ("/", "/"),
    }
    for name, (normal, shifted) in keypad.items():
        table[name] = KeyBinding(normal=normal, shift=shifted)

    if layout == "es":
        table.update({
            "KEY_GRAVE": KeyBinding(normal="º", shift="ª", altgr="\\", altgr_shift="\\"),
            "KEY_1": KeyBinding(normal="1", shift="!", altgr="|"),
            "KEY_2": KeyBinding(normal="2", shift='"', altgr="@"),
            "KEY_3": KeyBinding(normal="3", shift="·", altgr="#"),
            "KEY_4": KeyBinding(normal="4", shift="$", altgr="~"),
            "KEY_5": KeyBinding(normal="5", shift="%"),
            "KEY_6": KeyBinding(normal="6", shift="&", altgr="¬"),
            "KEY_7": KeyBinding(normal="7", shift="/", altgr="{"),
            "KEY_8": KeyBinding(normal="8", shift="(", altgr="["),
            "KEY_9": KeyBinding(normal="9", shift=")", altgr="]"),
            "KEY_0": KeyBinding(normal="0", shift="=", altgr="}"),
            "KEY_MINUS": KeyBinding(normal="'", shift="?", altgr="\\"),
            "KEY_EQUAL": KeyBinding(normal="¡", shift="¿", altgr_dead="dead_cedilla"),
            "KEY_LEFTBRACE": KeyBinding(normal_dead="dead_grave", shift_dead="dead_circumflex", altgr="["),
            "KEY_RIGHTBRACE": KeyBinding(normal="+", shift="*", altgr="]"),
            "KEY_SEMICOLON": KeyBinding(normal="ñ", shift="Ñ", altgr_dead="dead_tilde"),
            "KEY_APOSTROPHE": KeyBinding(normal_dead="dead_acute", shift_dead="dead_diaeresis", altgr="{"),
            "KEY_BACKSLASH": KeyBinding(normal="ç", shift="Ç", altgr="}"),
            "KEY_102ND": KeyBinding(normal="<", shift=">", altgr="\\", altgr_shift="|"),
            "KEY_COMMA": KeyBinding(normal=",", shift=";"),
            "KEY_DOT": KeyBinding(normal=".", shift=":"),
            "KEY_SLASH": KeyBinding(normal="-", shift="_"),
        })
    elif layout == "latam":
        table.update({
            "KEY_GRAVE": KeyBinding(normal="|", shift="°", altgr="¬"),
            "KEY_1": KeyBinding(normal="1", shift="!", altgr="|"),
            "KEY_2": KeyBinding(normal="2", shift='"', altgr="@"),
            "KEY_3": KeyBinding(normal="3", shift="#", altgr="·"),
            "KEY_4": KeyBinding(normal="4", shift="$", altgr="~"),
            "KEY_5": KeyBinding(normal="5", shift="%"),
            "KEY_6": KeyBinding(normal="6", shift="&", altgr="¬"),
            "KEY_7": KeyBinding(normal="7", shift="/", altgr="{"),
            "KEY_8": KeyBinding(normal="8", shift="(", altgr="["),
            "KEY_9": KeyBinding(normal="9", shift=")", altgr="]"),
            "KEY_0": KeyBinding(normal="0", shift="=", altgr="}"),
            "KEY_MINUS": KeyBinding(normal="'", shift="?", altgr="\\"),
            "KEY_EQUAL": KeyBinding(normal="¿", shift="¡", altgr_dead="dead_cedilla"),
            "KEY_LEFTBRACE": KeyBinding(normal_dead="dead_acute", shift_dead="dead_diaeresis", altgr_dead="dead_diaeresis"),
            "KEY_RIGHTBRACE": KeyBinding(normal="+", shift="*", altgr="~"),
            "KEY_SEMICOLON": KeyBinding(normal="ñ", shift="Ñ", altgr="~"),
            "KEY_APOSTROPHE": KeyBinding(normal="{", shift="[", altgr_dead="dead_circumflex"),
            "KEY_BACKSLASH": KeyBinding(normal="}", shift="]", altgr_dead="dead_grave"),
            "KEY_102ND": KeyBinding(normal="<", shift=">", altgr="\\", altgr_shift="|"),
            "KEY_COMMA": KeyBinding(normal=",", shift=";"),
            "KEY_DOT": KeyBinding(normal=".", shift=":"),
            "KEY_SLASH": KeyBinding(normal="-", shift="_"),
        })

    return table


def build_key_map(layout: str) -> KeyboardMap:
    """Builds the evdev scancode map for a supported layout."""
    normalized = normalize_layout_name(layout)
    try:
        from evdev import ecodes
    except ImportError:
        logger.warning("python-evdev is not installed; keyboard map is empty.")
        return KeyboardMap(layout=normalized)

    bindings: Dict[int, KeyBinding] = {}
    for key_name, binding in _layout_bindings(normalized).items():
        code = getattr(ecodes, key_name, None)
        if code is not None:
            bindings[code] = binding

    return KeyboardMap(layout=normalized, bindings=bindings)


def is_keyboard_capabilities(caps: Dict[int, list], device_name: str = "") -> bool:
    """
    Returns True when an evdev capability set looks like a real keyboard.

    Requiring the full alphabet and the main modifier/action keys filters out
    HDMI-CEC remotes and power buttons (for example the Pi's vc4-hdmi node),
    which only advertise a small subset such as KEY_ENTER.
    """
    try:
        from evdev import ecodes
    except ImportError:
        return False

    blacklist = ("vc4-hdmi", "hdmi jack", "pwr_button", "power button", "video bus")
    if any(token in (device_name or "").lower() for token in blacklist):
        return False

    keys = set(caps.get(ecodes.EV_KEY, []))
    required = {getattr(ecodes, f"KEY_{letter}") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
    required.update({
        ecodes.KEY_ENTER,
        ecodes.KEY_SPACE,
        ecodes.KEY_BACKSPACE,
        ecodes.KEY_LEFTSHIFT,
    })
    return required.issubset(keys)
