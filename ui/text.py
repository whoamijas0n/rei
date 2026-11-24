"""
REI - OLED-safe text helpers.

The default Pillow bitmap font (Aileron in Pillow >= 10) has no glyphs outside
ASCII, so accented characters render as corrupted boxes on the 128x64 OLED.
Every UI layer must pass its text through ``to_oled`` (directly or through the
``BaseView`` draw helpers) before drawing it on the panel.
"""

import re
import unicodedata
from typing import List, Optional, Sequence

# Characters without a NFKD decomposition that the font cannot render.
_TRANSLATIONS = {
    "✓": "OK", "✔": "OK",
    "✗": "X", "✘": "X", "×": "X",
    "°": "o", "º": "o", "ª": "a", "·": ".",
    "¿": "?", "¡": "!",
    "—": "-", "–": "-", "−": "-",
    "“": '"', "”": '"', "„": '"',
    "‘": "'", "’": "'",
    "…": "...",
    "→": ">", "←": "<", "↑": "^", "↓": "v",
    "•": "*", "●": "*", "■": "#", "▲": "^", "▼": "v",
    "⚠": "!", "🔥": "*", "📡": "*", "🔒": "*", "🖥": "*",
    "\u00a0": " ",
}

_FALLBACK_GLYPH = "?"

_MULTISPACE = re.compile(r"\s+")


def to_oled(text: object, max_len: Optional[int] = None) -> str:
    """
    Transliterates any string into printable ASCII safe for the OLED font.

    Accented letters lose their diacritic (ñ -> n, á -> a), known symbols map
    to ASCII equivalents and anything else becomes '?'. Control characters and
    repeated whitespace collapse into single spaces.
    """
    if text is None:
        return ""
    if isinstance(text, float) and text.is_integer():
        text = int(text)

    raw = str(text)
    mapped = "".join(_TRANSLATIONS.get(ch, ch) for ch in raw)
    decomposed = unicodedata.normalize("NFKD", mapped)
    stripped = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )

    clean_chars = []
    for ch in stripped:
        if ch.isascii() and ch.isprintable():
            clean_chars.append(ch)
        elif ch in "\t\n\r":
            clean_chars.append(" ")
        else:
            clean_chars.append(_FALLBACK_GLYPH)

    clean = _MULTISPACE.sub(" ", "".join(clean_chars)).strip()
    if max_len is not None:
        clean = clean[:max_len]
    return clean


def _fit_prefix(text: str, measure, max_width: int) -> str:
    """Longest prefix of ``text`` that fits ``max_width`` pixels."""
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if measure(text[:mid]) <= max_width:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def wrap_text(
    text: str,
    max_chars: int = 20,
    max_lines: Optional[int] = None,
) -> List[str]:
    """
    Word-wraps a single logical line at a character limit (no renderer needed).

    Used by tests and by callers without a drawing context; pixel-accurate
    wrapping lives in :func:`wrap_text_px`.
    """
    clean = to_oled(text)
    if not clean:
        return []

    lines: List[str] = []
    for word in clean.split(" "):
        while len(word) > max_chars:
            if lines is not None and (max_lines and len(lines) >= max_lines):
                return lines
            lines.append(word[:max_chars])
            word = word[max_chars:]
        if not lines or len(lines[-1]) + 1 + len(word) > max_chars:
            lines.append(word)
        else:
            lines[-1] = f"{lines[-1]} {word}"
        if max_lines and len(lines) >= max_lines:
            break
    return lines[:max_lines] if max_lines else lines


def wrap_text_px(
    draw,
    font,
    text: str,
    max_width: int,
    max_lines: Optional[int] = None,
) -> List[str]:
    """
    Word-wraps one logical line so each physical line fits ``max_width``
    pixels when rendered with ``font``. Words wider than the limit are hard
    split at pixel boundaries.
    """
    if draw is None or font is None:
        return wrap_text(text, max_chars=max(8, max_width // 6), max_lines=max_lines)

    clean = to_oled(text)
    if not clean:
        return []

    def width(value: str) -> int:
        try:
            return int(draw.textlength(value, font=font))
        except Exception:
            return len(value) * 6

    lines: List[str] = []
    for word in clean.split(" "):
        if width(word) > max_width:
            # Hard split an oversized token (very long SSIDs, hashes...).
            while word:
                prefix = _fit_prefix(word, width, max_width)
                if not prefix:
                    prefix = word[:1]
                lines.append(prefix)
                word = word[len(prefix):]
                if max_lines and len(lines) >= max_lines:
                    return lines
                if width(word) <= max_width:
                    break
            rest = word
        else:
            rest = word
        if rest and lines:
            candidate = f"{lines[-1]} {rest}"
            if width(candidate) <= max_width:
                lines[-1] = candidate
                continue
        if rest:
            lines.append(rest)
        if max_lines and len(lines) >= max_lines:
            return lines
    return lines


def wrap_lines_px(
    draw,
    font,
    lines: Sequence[str],
    max_width: int,
    max_lines: Optional[int] = None,
) -> List[str]:
    """Wraps several logical lines into a single physical line buffer."""
    physical: List[str] = []
    for logical in lines:
        physical.extend(wrap_text_px(draw, font, logical, max_width))
        if max_lines and len(physical) >= max_lines:
            break
    return physical[:max_lines] if max_lines else physical
