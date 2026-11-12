"""
REI - Metric and Output Parser
Utilities for parsing network, hardware, and system telemetry data.
"""

import re
from typing import Dict, List, Any
import unicodedata

_OLED_CHAR_REPLACEMENTS = {
    "✓": "OK",
    "✔": "OK",
    "✗": "X",
    "✘": "X",
    "×": "x",
    "•": "*",
    "·": ".",
    "°": "C",
    "—": "-",
    "–": "-",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "¿": "",
    "¡": "",
    "…": "...",
}


def sanitize_oled_text(text: Any) -> str:
    """
    Normalizes text to printable 7-bit ASCII for the SH1106 128x64 bitmap font.
    Prevents corrupted glyphs on accented characters, 'ñ', or Unicode symbols.
    """
    if text is None:
        return ""
    raw = str(text)
    for src, dst in _OLED_CHAR_REPLACEMENTS.items():
        if src in raw:
            raw = raw.replace(src, dst)

    normalized = unicodedata.normalize("NFKD", raw)
    out_chars: List[str] = []
    for ch in normalized:
        if unicodedata.category(ch) == "Mn":
            continue
        code = ord(ch)
        if ch in ("\r", "\n", "\t"):
            out_chars.append(" ")
        elif 32 <= code <= 126:
            out_chars.append(ch)
    return "".join(out_chars)


def shorten_oled_label(text: Any, max_chars: int = 16) -> str:
    """Sanitizes and truncates a single-line OLED label without breaking encoding."""
    clean = sanitize_oled_text(text).strip()
    if max_chars <= 0:
        return ""
    if len(clean) <= max_chars:
        return clean
    if max_chars >= 4:
        return f"{clean[:max_chars - 2]}.."
    return clean[:max_chars]


def wrap_oled_lines(lines: List[str], max_chars: int = 19) -> List[str]:
    """
    Sanitizes and wraps lines at word boundaries to fit OLED width cleanly.
    Splits single tokens longer than max_chars only when necessary.
    """
    if max_chars <= 0:
        return []

    wrapped: List[str] = []
    for raw_line in lines:
        if raw_line is None:
            continue
        for sub_line in str(raw_line).splitlines() or [""]:
            clean = " ".join(sanitize_oled_text(sub_line).split())
            if not clean:
                continue
            words = clean.split(" ")
            current = ""
            for word in words:
                if len(word) > max_chars:
                    if current:
                        wrapped.append(current)
                        current = ""
                    for idx in range(0, len(word), max_chars):
                        chunk = word[idx : idx + max_chars]
                        if len(chunk) == max_chars and idx + max_chars < len(word):
                            wrapped.append(chunk)
                        else:
                            current = chunk
                elif not current:
                    current = word
                elif len(current) + 1 + len(word) <= max_chars:
                    current = f"{current} {word}"
                else:
                    wrapped.append(current)
                    current = word
            if current:
                wrapped.append(current)
    return wrapped


class MetricParser:
    """Parses raw shell and hardware output into formatted UI metric dictionaries."""

    @staticmethod
    def parse_ip_output(raw_output: str) -> List[Dict[str, str]]:
        """Parses `ip addr` or `ifconfig` output into interface/IP mappings."""
        interfaces = []
        # Match standard Linux ip -brief addr or ifconfig patterns
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 3:
                iface = parts[0]
                status = parts[1]
                ip = parts[2].split("/")[0]
                interfaces.append({"iface": iface, "status": status, "ip": ip})
        return interfaces

    @staticmethod
    def parse_wifi_scan(raw_output: str) -> List[Dict[str, Any]]:
        """Parses `nmcli` or `iwlist` scan outputs into clean SSID lists."""
        networks = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            # Generic parser for SSID and Signal
            match = re.search(r"SSID:\s*(.+?)(?:\s+SIGNAL:\s*(\d+)%)?", line, re.IGNORECASE)
            if match:
                networks.append({
                    "ssid": match.group(1).strip(),
                    "signal": int(match.group(2)) if match.group(2) else 100
                })
        return networks

    @staticmethod
    def format_bytes(bytes_val: int) -> str:
        """Converts raw byte counts to human readable strings."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_val < 1024.0:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.1f} TB"
