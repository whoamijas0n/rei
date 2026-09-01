"""
REI - USB HID Rubber Ducky Engine (DuckyInjector)
Emulates USB keyboard reports directly via /dev/hidg0 with multi-layout support (ES/US).
Resolves Dead Keys on ISO Spanish keyboards and supports safe dry-run emulation.
"""

import logging
import os
import time
from typing import Dict, List, Optional, Tuple, Union

logger = logging.getLogger("REI.Core.Ducky")

# USB HID Keycodes (Keyboard/Keypad Page 0x07)
HID_KEY_CODES: Dict[str, int] = {
    'a': 0x04, 'b': 0x05, 'c': 0x06, 'd': 0x07, 'e': 0x08, 'f': 0x09, 'g': 0x0a,
    'h': 0x0b, 'i': 0x0c, 'j': 0x0d, 'k': 0x0e, 'l': 0x0f, 'm': 0x10, 'n': 0x11,
    'o': 0x12, 'p': 0x13, 'q': 0x14, 'r': 0x15, 's': 0x16, 't': 0x17, 'u': 0x18,
    'v': 0x19, 'w': 0x1a, 'x': 0x1b, 'y': 0x1c, 'z': 0x1d,
    '1': 0x1e, '2': 0x1f, '3': 0x20, '4': 0x21, '5': 0x22, '6': 0x23, '7': 0x24,
    '8': 0x25, '9': 0x26, '0': 0x27,
    'enter': 0x28, 'esc': 0x29, 'backspace': 0x2a, 'tab': 0x2b, 'space': 0x2c, ' ': 0x2c,
    '-': 0x2d, '=': 0x2e, '[': 0x2f, ']': 0x30, '\\': 0x31, ';': 0x33, "'": 0x34,
    '`': 0x35, ',': 0x36, '.': 0x37, '/': 0x38,
    'capslock': 0x39, 'f1': 0x3a, 'f2': 0x3b, 'f3': 0x3c, 'f4': 0x3d, 'f5': 0x3e,
    'f6': 0x3f, 'f7': 0x40, 'f8': 0x41, 'f9': 0x42, 'f10': 0x43, 'f11': 0x44,
    'f12': 0x45, 'printscreen': 0x46, 'scrolllock': 0x47, 'pause': 0x48,
    'insert': 0x49, 'home': 0x4a, 'pageup': 0x4b, 'delete': 0x4c, 'end': 0x4d,
    'pagedown': 0x4e, 'right': 0x4f, 'left': 0x50, 'down': 0x51, 'up': 0x52,
    'numlock': 0x53, 'kp/': 0x54, 'kp*': 0x55, 'kp-': 0x56, 'kp+': 0x57,
    'kpenter': 0x58, 'kp1': 0x59, 'kp2': 0x5a, 'kp3': 0x5b, 'kp4': 0x5c,
    'kp5': 0x5d, 'kp6': 0x5e, 'kp7': 0x5f, 'kp8': 0x60, 'kp9': 0x61, 'kp0': 0x62,
    'kp.': 0x63, '102nd': 0x64, 'application': 0x65, 'power': 0x66, 'kp=': 0x67,
    # Modifiers
    'leftctrl': 0xe0, 'leftshift': 0xe1, 'leftalt': 0xe2, 'leftgui': 0xe3,
    'rightctrl': 0xe4, 'rightshift': 0xe5, 'rightalt': 0xe6, 'rightgui': 0xe7,
}

# Modifier bitmasks (Byte 0 in 8-byte HID report)
MOD_BITS: Dict[str, int] = {
    'leftctrl': 0x01, 'ctrl': 0x01, 'leftcontrol': 0x01, 'control': 0x01,
    'leftshift': 0x02, 'shift': 0x02,
    'leftalt': 0x04, 'alt': 0x04,
    'leftgui': 0x08, 'gui': 0x08, 'win': 0x08, 'windows': 0x08, 'super': 0x08,
    'rightctrl': 0x10,
    'rightshift': 0x20,
    'rightalt': 0x40, 'altgr': 0x40,
    'rightgui': 0x80,
}

# Shift characters mapping for US Keyboard layout
SHIFT_CHARS_US: Dict[str, str] = {
    '~': '`', '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
    '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\',
    ':': ';', '"': "'", '<': ',', '>': '.', '?': '/'
}

# Spanish (ISO) layout mapping: char -> (modifier_mask, physical_key_name, is_dead_key)
# Modifiers: 0 = None, 2 = Shift, 64 (0x40) = AltGr
LAYOUT_ES: Dict[str, Tuple[int, str, bool]] = {
    # Lowercase & numbers without modifiers
    **{chr(c): (0, chr(c), False) for c in range(97, 123) if chr(c) != 'ñ'},
    'ñ': (0, ';', False), ' ': (0, 'space', False),
    '1': (0, '1', False), '2': (0, '2', False), '3': (0, '3', False),
    '4': (0, '4', False), '5': (0, '5', False), '6': (0, '6', False),
    '7': (0, '7', False), '8': (0, '8', False), '9': (0, '9', False), '0': (0, '0', False),

    # Direct Symbols
    "'": (0, '-', False),
    '¡': (0, '=', False),
    '`': (0, '[', True),   # Dead key (Grave accent)
    '+': (0, ']', False),
    'ç': (0, '\\', False),
    '´': (0, "'", True),   # Dead key (Acute accent / Tilde)
    ',': (0, ',', False),
    '.': (0, '.', False),
    '-': (0, '/', False),
    '<': (0, '102nd', False),
    'º': (0, '`', False),

    # Shift Symbols (Mod: 2)
    '!': (2, '1', False),
    '"': (2, '2', False),
    '·': (2, '3', False),
    '$': (2, '4', False),
    '%': (2, '5', False),
    '&': (2, '6', False),
    '/': (2, '7', False),
    '(': (2, '8', False),
    ')': (2, '9', False),
    '=': (2, '0', False),
    '?': (2, '-', False),
    '¿': (2, '=', False),
    '^': (2, '[', True),   # Dead key (Circumflex)
    '*': (2, ']', False),
    'Ç': (2, '\\', False),
    'Ñ': (2, ';', False),
    '¨': (2, "'", True),   # Dead key (Diaeresis)
    ';': (2, ',', False),
    ':': (2, '.', False),
    '_': (2, '/', False),
    '>': (2, '102nd', False),
    'ª': (2, '`', False),

    # AltGr Symbols (Mod: 64 / 0x40)
    '|': (64, '1', False),
    '@': (64, '2', False),
    '#': (64, '3', False),
    '~': (64, '4', True),  # Dead key (Tilde)
    '€': (64, 'e', False),
    '[': (64, '[', False),
    ']': (64, ']', False),
    '{': (64, "'", False),
    '}': (64, '\\', False),
    '\\': (64, '`', False),
}

# Uppercase letters for Spanish ISO
for c in range(65, 91):
    if chr(c) != 'Ñ':
        LAYOUT_ES[chr(c)] = (2, chr(c).lower(), False)

# Common Key Aliases
KEY_ALIASES: Dict[str, str] = {
    'gui': 'leftgui', 'windows': 'leftgui', 'win': 'leftgui', 'super': 'leftgui',
    'ctrl': 'leftctrl', 'control': 'leftctrl',
    'alt': 'leftalt',
    'shift': 'leftshift',
    'enter': 'enter', 'return': 'enter',
    'esc': 'esc', 'escape': 'esc',
    'tab': 'tab',
    'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right',
    'space': 'space',
    'backspace': 'backspace', 'del': 'delete', 'delete': 'delete',
    'caps': 'capslock', 'capslock': 'capslock',
    'print': 'printscreen', 'prtsc': 'printscreen',
    'ins': 'insert', 'insert': 'insert',
    'pgup': 'pageup', 'pgdn': 'pagedown',
    'home': 'home', 'end': 'end',
}


class DuckyInjector:
    """
    High-level USB HID keyboard injector.
    Writes 8-byte USB HID keyboard reports to /dev/hidg0.
    """

    def __init__(
        self,
        hid_device: str = "/dev/hidg0",
        polling_delay_s: float = 0.015,
        dry_run: Optional[bool] = None,
    ):
        self.hid_device = hid_device
        self.polling_delay_s = polling_delay_s
        self.dry_run = (
            dry_run
            if dry_run is not None
            else (os.environ.get("REI_DRY_RUN", "0") == "1")
        )
        if self.dry_run:
            logger.info("DuckyInjector initialized in DRY-RUN mode (hardware output mocked).")
        else:
            logger.info(f"DuckyInjector initialized targeting device: {self.hid_device}")

    def send_hid_report(self, modifier: int, key_code: int) -> None:
        """
        Sends an 8-byte HID keyboard report followed by an 8-byte release report.
        Strictly applies the 15ms atomic polling delay.
        """
        if self.dry_run:
            logger.debug(f"[DRY-RUN HID] Mod: 0x{modifier:02x}, Key: 0x{key_code:02x}")
            return

        if not os.path.exists(self.hid_device):
            raise FileNotFoundError(
                f"USB HID device '{self.hid_device}' not found. "
                "Ensure USB Gadget (g_hid / dwc2) is loaded."
            )

        report = bytes([modifier, 0, key_code, 0, 0, 0, 0, 0])
        release = b"\x00" * 8

        try:
            with open(self.hid_device, "wb") as fd:
                fd.write(report)
                fd.flush()
                time.sleep(self.polling_delay_s)
                fd.write(release)
                fd.flush()
                time.sleep(self.polling_delay_s)
        except PermissionError as pe:
            logger.error(f"Permission denied writing to {self.hid_device}: {pe}")
            raise
        except OSError as oe:
            logger.error(f"I/O error writing to {self.hid_device}: {oe}")
            raise

    def press_key(self, key_name: str) -> None:
        """Presses and releases a single key by name (e.g. 'enter', 'tab', 'a')."""
        normalized = key_name.lower().strip()
        target_key = KEY_ALIASES.get(normalized, normalized)

        if target_key not in HID_KEY_CODES:
            logger.warning(f"Unmapped key requested: '{key_name}'")
            return

        code = HID_KEY_CODES[target_key]
        if code >= 0xE0:
            # Standalone modifier key
            mod_bit = 1 << (code - 0xE0)
            self.send_hid_report(mod_bit, 0)
        else:
            self.send_hid_report(0, code)

    def press_combination(self, modifier: str, key_name: str) -> None:
        """
        Presses a key with one or more modifiers (e.g. 'gui', 'r' or 'ctrl+alt', 't').
        """
        mod_mask = 0
        mod_parts = [m.strip().lower() for m in modifier.split("+") if m.strip()]

        for m in mod_parts:
            m_alias = KEY_ALIASES.get(m, m)
            if m_alias in MOD_BITS:
                mod_mask |= MOD_BITS[m_alias]
            elif m_alias in HID_KEY_CODES and HID_KEY_CODES[m_alias] >= 0xE0:
                mod_mask |= 1 << (HID_KEY_CODES[m_alias] - 0xE0)
            else:
                logger.warning(f"Unknown modifier component: '{m}'")

        target_key = key_name.lower().strip()
        target_key = KEY_ALIASES.get(target_key, target_key)

        if target_key not in HID_KEY_CODES:
            logger.warning(f"Unmapped key in combination: '{key_name}'")
            return

        key_code = HID_KEY_CODES[target_key]
        self.send_hid_report(mod_mask, key_code)

    def write_text(self, text: str, layout: str = "es") -> None:
        """
        Types out an entire text string using the specified layout ('es' or 'us').
        Handles Dead Keys properly in 'es' layout by pressing space right after.
        """
        layout_mode = layout.lower().strip()

        for char in text:
            if char in ("\n", "\r"):
                self.send_hid_report(0, HID_KEY_CODES["enter"])
                continue
            elif char == "\t":
                self.send_hid_report(0, HID_KEY_CODES["tab"])
                continue

            if layout_mode == "es" and char in LAYOUT_ES:
                mod_mask, phys_key, is_dead_key = LAYOUT_ES[char]
                if phys_key not in HID_KEY_CODES:
                    logger.warning(f"Physical key '{phys_key}' not mapped for character '{char}'")
                    continue
                key_code = HID_KEY_CODES[phys_key]

                # Inject character with modifier
                self.send_hid_report(mod_mask, key_code)

                # Dead Key Resolution: Send space to release buffer
                if is_dead_key:
                    self.send_hid_report(0, HID_KEY_CODES["space"])

            else:
                # Default US Layout Logic
                if char.isupper():
                    base = char.lower()
                    if base in HID_KEY_CODES:
                        self.send_hid_report(MOD_BITS["shift"], HID_KEY_CODES[base])
                elif char in SHIFT_CHARS_US:
                    base = SHIFT_CHARS_US[char]
                    if base in HID_KEY_CODES:
                        self.send_hid_report(MOD_BITS["shift"], HID_KEY_CODES[base])
                else:
                    if char in HID_KEY_CODES:
                        self.send_hid_report(0, HID_KEY_CODES[char])
                    else:
                        logger.warning(f"Character '{char}' unsupported in layout '{layout_mode}'")

    def execute_script(self, script_path: str, layout: str = "es") -> None:
        """
        Parses and executes a standard DuckyScript file.
        Supports: REM, STRING, DELAY, combinations (e.g. GUI r), and single keys.
        """
        if not os.path.isfile(script_path):
            raise FileNotFoundError(f"DuckyScript file '{script_path}' not found.")

        with open(script_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        logger.info(f"Executing DuckyScript '{os.path.basename(script_path)}' ({len(lines)} lines)...")

        for line_num, line in enumerate(lines, start=1):
            line = line.strip()
            if not line or line.upper().startswith("REM"):
                continue

            parts = line.split(maxsplit=1)
            cmd = parts[0].upper()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "STRING":
                self.write_text(arg, layout=layout)
            elif cmd == "DELAY":
                try:
                    delay_ms = int(arg)
                    time.sleep(delay_ms / 1000.0)
                except ValueError:
                    logger.warning(f"Invalid DELAY on line {line_num}: {arg}")
            elif " " in line:
                # Combination (e.g., 'GUI r', 'CTRL ALT t')
                tokens = line.split()
                if len(tokens) >= 2:
                    mod = "+".join(tokens[:-1])
                    key = tokens[-1]
                    self.press_combination(mod, key)
            else:
                # Single Key (e.g., 'ENTER', 'GUI', 'F11')
                self.press_key(cmd)
