"""
REI - Sistema de Diseño y Primitivas Gráficas para OLED 128x64 (1-bit).

Define la gramática visual compartida por todas las vistas de REI:
- Marco perimetral consistente (1, 1) - (126, 62).
- Cabecera unificada (y=2..12) con título ajustado en píxeles y slot derecho.
- Motor tipográfico con medición real en píxeles (compatible con fuentes
  proporcionales de Pillow 10+/13 y fallback seguro a fuente bitmap).
- Badges semánticos invertidos (OK, INFO, AVISO, ERROR), scrollbar, spinner
  animado y banda inferior contextual con micro-glifos.
"""

from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from PIL import ImageDraw, ImageFont

from core.parser import sanitize_oled_text

# ---------------------------------------------------------------------------
# Geometría estándar del lienzo SH1106 (128x64 px, 1-bit)
# ---------------------------------------------------------------------------
OLED_WIDTH: int = 128
OLED_HEIGHT: int = 64

FRAME_BOX: Tuple[int, int, int, int] = (1, 1, 126, 62)

HEADER_TOP: int = 2
HEADER_BOTTOM: int = 12
HEADER_DIVIDER_Y: int = 13

BODY_TOP: int = 15
BODY_BOTTOM: int = 50

FOOTER_DIVIDER_Y: int = 51
FOOTER_TOP: int = 52
FOOTER_BOTTOM: int = 61

CONTENT_LEFT: int = 4
CONTENT_RIGHT: int = 123

# Coordenadas específicas de HeroCardDeckView (sin cabecera superior, máximo aire para el icono)
HERO_ICON_CX: int = 64
HERO_ICON_CY: int = 24
HERO_TITLE_Y: int = 46
HERO_MAX_DOTS: int = 10


# ---------------------------------------------------------------------------
# Fuentes cacheadas y métricas reales en píxeles
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _load_default_sized(size: Optional[int]) -> ImageFont.ImageFont:
    """Carga la fuente integrada de Pillow con tamaño opcional y fallback seguro."""
    if size is not None:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            pass
    return ImageFont.load_default()


def get_body_font() -> ImageFont.ImageFont:
    """Fuente principal nitida en 1-bit (10 px, 8 px de altura real de glifo)."""
    return _load_default_sized(10)


def get_small_font() -> ImageFont.ImageFont:
    """
    Fuente para cabeceras, badges y pies de pagina.
    Usa 10 px porque en renderizado monocromatico 1-bit de Pillow 13 los
    tamanos <= 8 px pierden trazos horizontales en digitos.
    """
    return _load_default_sized(10)


def get_compact_font() -> ImageFont.ImageFont:
    """
    Fuente compacta nitida de 9 px (6 px de altura real de glifo) para vistas
    tecnicas de detalle (DetailCardView), dando mayor aire visual en 128x64.
    """
    return _load_default_sized(9)


def get_hero_font() -> ImageFont.ImageFont:
    """Fuente de gran formato (18 px) para cifras protagonistas en telemetria."""
    return _load_default_sized(18)


def measure_text_px(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Optional[ImageFont.ImageFont] = None,
) -> int:
    """Calcula el ancho exacto en píxeles de una cadena ya sanitizada."""
    active_font = font or get_body_font()
    if not text:
        return 0
    if hasattr(draw, "textbbox"):
        bbox = draw.textbbox((0, 0), text, font=active_font)
        return max(0, int(bbox[2] - bbox[0]))
    if hasattr(draw, "textlength"):
        return max(0, int(draw.textlength(text, font=active_font)))
    return len(text) * 6


def _font_vertical_metrics(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
) -> Tuple[int, int]:
    """Devuelve (top, bottom) de referencia en mayúsculas/ascendentes para alineado estable."""
    if hasattr(draw, "textbbox"):
        bbox = draw.textbbox((0, 0), "A0", font=font)
        return int(bbox[1]), int(bbox[3])
    return 0, 8


def draw_text_vcenter(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y_top: int,
    y_bottom: int,
    font: Optional[ImageFont.ImageFont] = None,
    fill: str = "white",
) -> None:
    """Dibuja texto sanitizado centrado verticalmente dentro de [y_top, y_bottom]."""
    active_font = font or get_body_font()
    clean = sanitize_oled_text(text)
    if not clean:
        return
    ref_top, ref_bottom = _font_vertical_metrics(draw, active_font)
    glyph_h = max(1, ref_bottom - ref_top)
    box_h = max(1, y_bottom - y_top + 1)
    y_pos = y_top + (box_h - glyph_h) // 2 - ref_top
    draw.text((x, y_pos), clean, font=active_font, fill=fill)


def fit_text_px(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Optional[ImageFont.ImageFont],
    max_px: int,
    ellipsis: str = "..",
) -> str:
    """
    Sanitiza y recorta texto por la derecha para ajustarse exactamente a `max_px`.
    Garantiza que ningún título o etiqueta desborde el marco OLED.
    """
    active_font = font or get_body_font()
    clean = sanitize_oled_text(text).strip()
    if max_px <= 0 or not clean:
        return ""
    if measure_text_px(draw, clean, active_font) <= max_px:
        return clean

    ell_w = measure_text_px(draw, ellipsis, active_font) if ellipsis else 0
    if max_px <= ell_w:
        # Sin espacio para elipsis: tomar los caracteres que quepan
        trimmed = clean
        while trimmed and measure_text_px(draw, trimmed, active_font) > max_px:
            trimmed = trimmed[:-1]
        return trimmed

    trimmed = clean
    while trimmed and measure_text_px(draw, trimmed + ellipsis, active_font) > max_px:
        trimmed = trimmed[:-1]
    trimmed = trimmed.rstrip()
    return f"{trimmed}{ellipsis}" if trimmed else ""


def wrap_text_px(
    draw: ImageDraw.ImageDraw,
    lines: Sequence[str],
    font: Optional[ImageFont.ImageFont],
    max_px: int,
) -> List[str]:
    """
    Envuelve texto por límite de palabras usando el ancho real en píxeles.
    Solo divide una palabra individual cuando excede `max_px` por sí sola.
    """
    active_font = font or get_body_font()
    if max_px <= 8:
        return []

    wrapped: List[str] = []
    for raw_line in lines:
        if raw_line is None:
            continue
        sub_lines = str(raw_line).splitlines() or [""]
        for sub_line in sub_lines:
            clean = " ".join(sanitize_oled_text(sub_line).split())
            if not clean:
                continue
            words = clean.split(" ")
            current = ""
            for word in words:
                if measure_text_px(draw, word, active_font) > max_px:
                    if current:
                        wrapped.append(current)
                        current = ""
                    # Dividir token largo respetando el ancho real en píxeles
                    chunk = ""
                    for ch in word:
                        candidate = chunk + ch
                        if chunk and measure_text_px(draw, candidate, active_font) > max_px:
                            wrapped.append(chunk)
                            chunk = ch
                        else:
                            chunk = candidate
                    current = chunk
                elif not current:
                    current = word
                else:
                    candidate = f"{current} {word}"
                    if measure_text_px(draw, candidate, active_font) <= max_px:
                        current = candidate
                    else:
                        wrapped.append(current)
                        current = word
            if current:
                wrapped.append(current)
    return wrapped


# ---------------------------------------------------------------------------
# Primitivas de marco, cabecera, badges, pie y controles
# ---------------------------------------------------------------------------

def draw_frame(
    draw: ImageDraw.ImageDraw,
    width: int = OLED_WIDTH,
    height: int = OLED_HEIGHT,
) -> None:
    """Dibuja el borde perimetral estándar de 1 px (1, 1) a (126, 62)."""
    draw.rectangle((1, 1, width - 2, height - 2), outline="white", fill="black")


def draw_dotted_hline(
    draw: ImageDraw.ImageDraw,
    x1: int,
    x2: int,
    y: int,
    step: int = 2,
) -> None:
    """Dibuja un separador horizontal punteado sutil para jerarquía secundaria."""
    for x in range(x1, x2 + 1, max(1, step)):
        draw.point((x, y), fill="white")


def draw_inverted_badge(
    draw: ImageDraw.ImageDraw,
    text: str,
    right_x: int,
    y_top: int = HEADER_TOP + 1,
    y_bottom: int = HEADER_BOTTOM - 1,
    font: Optional[ImageFont.ImageFont] = None,
    pad_x: int = 2,
) -> int:
    """
    Dibuja un badge de alto contraste (fondo blanco, texto negro) alineado a la derecha.
    Devuelve la coordenada X izquierda del badge para calcular espacio restante.
    """
    active_font = font or get_small_font()
    clean = sanitize_oled_text(text).strip().upper()
    if not clean:
        return right_x
    text_w = measure_text_px(draw, clean, active_font)
    left_x = max(CONTENT_LEFT + 12, right_x - text_w - (pad_x * 2) + 1)
    draw.rectangle((left_x, y_top, right_x, y_bottom), fill="white")
    draw_text_vcenter(
        draw,
        clean,
        x=left_x + pad_x,
        y_top=y_top,
        y_bottom=y_bottom,
        font=active_font,
        fill="black",
    )
    return left_x


def draw_header_bar(
    draw: ImageDraw.ImageDraw,
    title: str,
    font: Optional[ImageFont.ImageFont] = None,
    right_badge: Optional[str] = None,
    right_text: Optional[str] = None,
    right_reserved_px: int = 0,
    show_divider: bool = True,
    width: int = OLED_WIDTH,
) -> int:
    """
    Renderiza la cabecera unificada de REI (y=2..12 + divisor sólido en y=13).

    - Izquierda: título en mayúsculas recortado exactamente en píxeles.
    - Derecha: badge invertido (`right_badge`), texto compacto (`right_text`)
      o espacio reservado (`right_reserved_px`) para controles propios (puntos,
      spinner o estado del teclado).

    Retorna la coordenada X límite donde comienza la zona derecha.
    """
    active_font = font or get_small_font()
    right_edge = width - 4  # x=124 en 128px
    right_limit = right_edge + 1

    if right_badge:
        badge_left = draw_inverted_badge(
            draw,
            right_badge,
            right_x=right_edge,
            y_top=HEADER_TOP + 1,
            y_bottom=HEADER_BOTTOM - 1,
            font=active_font,
            pad_x=1,
        )
        right_limit = badge_left - 2
    elif right_text:
        clean_rt = sanitize_oled_text(right_text).strip()
        if clean_rt:
            rt_w = measure_text_px(draw, clean_rt, active_font)
            rt_x = max(CONTENT_LEFT + 16, right_edge - rt_w + 1)
            draw_text_vcenter(
                draw,
                clean_rt,
                x=rt_x,
                y_top=HEADER_TOP,
                y_bottom=HEADER_BOTTOM,
                font=active_font,
                fill="white",
            )
            right_limit = rt_x - 3
    elif right_reserved_px > 0:
        right_limit = max(CONTENT_LEFT + 16, right_edge - right_reserved_px)

    max_title_px = max(12, right_limit - CONTENT_LEFT)
    clean_title = fit_text_px(draw, title.upper(), active_font, max_title_px)
    if clean_title:
        draw_text_vcenter(
            draw,
            clean_title,
            x=CONTENT_LEFT,
            y_top=HEADER_TOP,
            y_bottom=HEADER_BOTTOM,
            font=active_font,
            fill="white",
        )

    if show_divider:
        draw.line((2, HEADER_DIVIDER_Y, width - 3, HEADER_DIVIDER_Y), fill="white")

    return right_limit


def draw_scrollbar(
    draw: ImageDraw.ImageDraw,
    total_items: int,
    visible_items: int,
    offset: int,
    top: int = BODY_TOP + 1,
    bottom: int = BODY_BOTTOM - 1,
    x: int = 123,
) -> None:
    """Dibuja una barra de desplazamiento vertical limpia con pista punteada."""
    if total_items <= visible_items or bottom <= top:
        return

    # Pista punteada sutil
    for py in range(top, bottom + 1, 2):
        draw.point((x + 1, py), fill="white")

    track_h = bottom - top + 1
    thumb_h = max(6, int(track_h * (visible_items / float(total_items))))
    thumb_h = min(track_h, thumb_h)
    max_scroll = max(1, total_items - visible_items)
    clamped = max(0, min(offset, max_scroll))
    thumb_range = max(0, track_h - thumb_h)
    thumb_y = top + int(round(thumb_range * (clamped / float(max_scroll))))
    draw.rectangle((x, thumb_y, x + 1, thumb_y + thumb_h - 1), fill="white")


def draw_spinner(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    tick: int,
    radius: int = 4,
) -> None:
    """
    Dibuja un spinner orbital monocromático de 8 posiciones simétrico.
    Muestra 4 segmentos activos girando para máxima claridad en 1-bit.
    """
    if radius <= 3:
        ring: Tuple[Tuple[int, int], ...] = (
            (0, -3),
            (2, -2),
            (3, 0),
            (2, 2),
            (0, 3),
            (-2, 2),
            (-3, 0),
            (-2, -2),
        )
    else:
        ring = (
            (0, -4),
            (3, -3),
            (4, 0),
            (3, 3),
            (0, 4),
            (-3, 3),
            (-4, 0),
            (-3, -3),
        )
    head = tick % len(ring)
    for trail_idx in (0, 1, 2, 3):
        dx, dy = ring[(head - trail_idx) % len(ring)]
        if trail_idx < 2:
            draw.rectangle((cx + dx, cy + dy, cx + dx + 1, cy + dy + 1), fill="white")
        else:
            draw.point((cx + dx, cy + dy), fill="white")


def draw_mini_glyph(
    draw: ImageDraw.ImageDraw,
    name: str,
    x: int,
    y: int,
    fill: str = "white",
) -> int:
    """
    Dibuja micro-glifos consistentes de 5-9 px de ancho para pies y cabeceras.
    Retorna el ancho ocupado en píxeles.
    """
    glyph = name.lower().strip()

    if glyph == "check":
        # 7x5 px
        draw.line((x, y + 3, x + 2, y + 5), fill=fill)
        draw.line((x + 2, y + 5, x + 6, y + 1), fill=fill)
        return 7

    if glyph == "cross":
        # 5x5 px
        draw.line((x, y + 1, x + 4, y + 5), fill=fill)
        draw.line((x + 4, y + 1, x, y + 5), fill=fill)
        return 5

    if glyph == "warn":
        # 7x6 px triángulo con !
        draw.polygon([(x + 3, y), (x, y + 5), (x + 6, y + 5)], outline=fill)
        draw.line((x + 3, y + 2, x + 3, y + 3), fill=fill)
        draw.point((x + 3, y + 4), fill=fill)
        return 7

    if glyph == "info":
        # 5x6 px
        draw.point((x + 2, y), fill=fill)
        draw.line((x + 2, y + 2, x + 2, y + 5), fill=fill)
        draw.line((x + 1, y + 5, x + 3, y + 5), fill=fill)
        return 5

    if glyph == "arrow_left":
        # 5x5 px
        draw.line((x + 4, y + 1, x + 1, y + 3), fill=fill)
        draw.line((x + 1, y + 3, x + 4, y + 5), fill=fill)
        return 5

    if glyph == "arrow_right":
        # 5x5 px
        draw.line((x + 1, y + 1, x + 4, y + 3), fill=fill)
        draw.line((x + 4, y + 3, x + 1, y + 5), fill=fill)
        return 5

    if glyph == "chevron_left":
        # 4x7 px para navegación lateral de carrusel
        draw.line((x + 3, y, x, y + 3), fill=fill)
        draw.line((x, y + 3, x + 3, y + 6), fill=fill)
        return 4

    if glyph == "chevron_right":
        # 4x7 px para navegación lateral de carrusel
        draw.line((x, y, x + 3, y + 3), fill=fill)
        draw.line((x + 3, y + 3, x, y + 6), fill=fill)
        return 4

    if glyph == "arrow_up":
        # 5x3 px
        draw.point((x + 2, y + 1), fill=fill)
        draw.line((x + 1, y + 2, x + 3, y + 2), fill=fill)
        draw.line((x, y + 3, x + 4, y + 3), fill=fill)
        return 5

    if glyph == "arrow_down":
        # 5x3 px
        draw.line((x, y + 1, x + 4, y + 1), fill=fill)
        draw.line((x + 1, y + 2, x + 3, y + 2), fill=fill)
        draw.point((x + 2, y + 3), fill=fill)
        return 5

    if glyph == "scroll":
        # 5x7 px flecha arriba + abajo
        draw.point((x + 2, y), fill=fill)
        draw.line((x + 1, y + 1, x + 3, y + 1), fill=fill)
        draw.line((x + 1, y + 4, x + 3, y + 4), fill=fill)
        draw.point((x + 2, y + 5), fill=fill)
        return 5

    if glyph == "refresh":
        # 6x6 px flecha circular compacta
        draw.arc((x, y, x + 5, y + 5), 40, 320, fill=fill)
        draw.point((x + 4, y + 1), fill=fill)
        draw.point((x + 5, y + 2), fill=fill)
        return 6

    if glyph == "eye_closed":
        # 9x6 px pupila sólida = oculta
        draw.ellipse((x, y, x + 8, y + 5), outline=fill)
        draw.rectangle((x + 3, y + 2, x + 4, y + 3), fill=fill)
        return 9

    if glyph == "eye_open":
        # 9x6 px pupila hueca = visible
        draw.ellipse((x, y, x + 8, y + 5), outline=fill)
        return 9

    if glyph == "bang":
        # 3x6 px signo de exclamacion para cabeceras de error
        draw.rectangle((x + 1, y, x + 1, y + 3), fill=fill)
        draw.point((x + 1, y + 5), fill=fill)
        return 3

    if glyph == "bolt":
        # 5x7 px rayo de carga para telemetria de bateria
        draw.line((x + 3, y, x + 1, y + 3), fill=fill)
        draw.line((x + 1, y + 3, x + 4, y + 3), fill=fill)
        draw.line((x + 3, y + 3, x + 1, y + 6), fill=fill)
        return 5

    return 0


def draw_footer_bar(
    draw: ImageDraw.ImageDraw,
    left_text: str = "",
    hints: Optional[Sequence[Tuple[Optional[str], str]]] = None,
    font: Optional[ImageFont.ImageFont] = None,
    show_divider: bool = True,
    width: int = OLED_WIDTH,
) -> None:
    """
    Dibuja la banda inferior contextual unificada (y=52..61) con separador punteado.

    `hints` es una secuencia de tuplas `(glyph_name, label)` dispuestas de derecha
    a izquierda con espaciado uniforme (p. ej. `[("refresh", "K2"), ("arrow_left", "K3")]`).
    """
    active_font = font or get_small_font()

    if show_divider:
        draw_dotted_hline(draw, 4, width - 5, FOOTER_DIVIDER_Y, step=2)

    right_cursor = width - 5
    if hints:
        for glyph_name, label in reversed(hints):
            clean_label = sanitize_oled_text(label).strip()
            if not clean_label:
                continue
            label_w = measure_text_px(draw, clean_label, active_font)
            label_x = right_cursor - label_w + 1
            glyph_w = 0
            if glyph_name:
                # Ancho conocido de glifo
                glyph_widths = {
                    "check": 7,
                    "cross": 5,
                    "warn": 7,
                    "info": 5,
                    "arrow_left": 5,
                    "arrow_right": 5,
                    "arrow_up": 5,
                    "arrow_down": 5,
                    "scroll": 5,
                    "refresh": 6,
                    "eye_open": 9,
                    "eye_closed": 9,
                    "bang": 3,
                    "bolt": 5,
                }
                glyph_w = glyph_widths.get(glyph_name.lower(), 0)

            block_left = label_x - (glyph_w + 2 if glyph_w else 0)
            if block_left < CONTENT_LEFT:
                break

            draw_text_vcenter(
                draw,
                clean_label,
                x=label_x,
                y_top=FOOTER_TOP,
                y_bottom=FOOTER_BOTTOM,
                font=active_font,
                fill="white",
            )
            if glyph_name and glyph_w:
                draw_mini_glyph(draw, glyph_name, x=block_left, y=FOOTER_TOP + 2, fill="white")

            right_cursor = block_left - 6

    if left_text:
        max_left_px = max(12, right_cursor - CONTENT_LEFT)
        fitted_left = fit_text_px(draw, left_text, active_font, max_left_px)
        if fitted_left:
            draw_text_vcenter(
                draw,
                fitted_left,
                x=CONTENT_LEFT,
                y_top=FOOTER_TOP,
                y_bottom=FOOTER_BOTTOM,
                font=active_font,
                fill="white",
            )


def draw_clean_header_bar(
    draw: ImageDraw.ImageDraw,
    title: str,
    font: Optional[ImageFont.ImageFont] = None,
    left_glyph: Optional[str] = None,
    right_text: Optional[str] = None,
    right_reserved_px: int = 0,
    dotted_divider: bool = False,
    show_divider: bool = True,
    width: int = OLED_WIDTH,
) -> int:
    """
    Renderiza una cabecera ligera sin marco exterior (y=2..11 + divisor en y=13).
    Devuelve la coordenada X límite donde comienza la zona derecha.
    """
    active_font = font or get_small_font()
    left_x = 3
    if left_glyph:
        gw = draw_mini_glyph(draw, left_glyph, x=left_x, y=4, fill="white")
        if gw > 0:
            left_x += gw + 3

    right_edge = width - 4
    right_limit = right_edge + 1

    if right_text:
        clean_rt = sanitize_oled_text(right_text).strip()
        if clean_rt:
            rt_w = measure_text_px(draw, clean_rt, active_font)
            rt_x = max(left_x + 12, right_edge - rt_w + 1)
            draw_text_vcenter(
                draw,
                clean_rt,
                x=rt_x,
                y_top=HEADER_TOP,
                y_bottom=HEADER_BOTTOM - 1,
                font=active_font,
                fill="white",
            )
            right_limit = rt_x - 3
    elif right_reserved_px > 0:
        right_limit = max(left_x + 12, right_edge - right_reserved_px)

    max_title_px = max(12, right_limit - left_x)
    clean_title = fit_text_px(draw, title.upper(), active_font, max_title_px)
    if clean_title:
        draw_text_vcenter(
            draw,
            clean_title,
            x=left_x,
            y_top=HEADER_TOP,
            y_bottom=HEADER_BOTTOM - 1,
            font=active_font,
            fill="white",
        )

    if show_divider:
        if dotted_divider:
            draw_dotted_hline(draw, 2, width - 3, HEADER_DIVIDER_Y, step=2)
        else:
            draw.line((2, HEADER_DIVIDER_Y, width - 3, HEADER_DIVIDER_Y), fill="white")

    return right_limit


def draw_inverted_header_bar(
    draw: ImageDraw.ImageDraw,
    title: str,
    font: Optional[ImageFont.ImageFont] = None,
    icon_glyph: Optional[str] = "bang",
    right_text: Optional[str] = None,
    width: int = OLED_WIDTH,
) -> None:
    """
    Renderiza una franja de cabecera invertida de alto contraste (blanco/negro)
    respetando estrictamente el margen exterior de 1 px (y=1..12, x=2..width-3).
    """
    active_font = font or get_small_font()
    draw.rectangle((2, 1, width - 3, 12), fill="white")

    left_x = 5
    if icon_glyph:
        gw = draw_mini_glyph(draw, icon_glyph, x=left_x, y=4, fill="black")
        if gw > 0:
            left_x += gw + 3

    right_edge = width - 5
    right_limit = right_edge + 1
    if right_text:
        clean_rt = sanitize_oled_text(right_text).strip().upper()
        if clean_rt:
            rt_w = measure_text_px(draw, clean_rt, active_font)
            rt_x = max(left_x + 12, right_edge - rt_w + 1)
            draw_text_vcenter(
                draw,
                clean_rt,
                x=rt_x,
                y_top=1,
                y_bottom=12,
                font=active_font,
                fill="black",
            )
            right_limit = rt_x - 4

    max_title_px = max(12, right_limit - left_x)
    clean_title = fit_text_px(draw, title.upper(), active_font, max_title_px)
    if clean_title:
        draw_text_vcenter(
            draw,
            clean_title,
            x=left_x,
            y_top=1,
            y_bottom=12,
            font=active_font,
            fill="black",
        )


def draw_kv_row(
    draw: ImageDraw.ImageDraw,
    label: str,
    value: str,
    y: int,
    left_x: int = 3,
    right_x: int = 124,
    font: Optional[ImageFont.ImageFont] = None,
    min_gap: int = 6,
) -> None:
    """
    Dibuja una fila tabular de dos columnas: etiqueta a la izquierda y valor
    alineado a la derecha, garantizando separacion minima y cero solapamiento.
    """
    active_font = font or get_body_font()
    total_px = max(16, right_x - left_x + 1)
    clean_label = sanitize_oled_text(label).strip()
    clean_value = sanitize_oled_text(value).strip()

    if not clean_label and not clean_value:
        return

    if not clean_value:
        fitted = fit_text_px(draw, clean_label, active_font, total_px)
        if fitted:
            draw.text((left_x, y), fitted, font=active_font, fill="white")
        return

    if not clean_label:
        fitted = fit_text_px(draw, clean_value, active_font, total_px)
        if fitted:
            val_w = measure_text_px(draw, fitted, active_font)
            draw.text((max(left_x, right_x - val_w + 1), y), fitted, font=active_font, fill="white")
        return

    # Reservar espacio suficiente para etiqueta corta y valor a la derecha
    max_val_px = max(16, total_px - min_gap - 12)
    val_w = measure_text_px(draw, clean_value, active_font)
    lbl_w = measure_text_px(draw, clean_label, active_font)

    if lbl_w + min_gap + val_w <= total_px:
        fitted_lbl = clean_label
        fitted_val = clean_value
    else:
        # Priorizar mostrar la etiqueta completa si es corta (<= 55% del ancho)
        lbl_budget = min(lbl_w, int(total_px * 0.45))
        fitted_lbl = fit_text_px(draw, clean_label, active_font, lbl_budget)
        fitted_lbl_w = measure_text_px(draw, fitted_lbl, active_font)
        val_budget = max(12, total_px - fitted_lbl_w - min_gap)
        fitted_val = fit_text_px(draw, clean_value, active_font, min(max_val_px, val_budget))

    if fitted_lbl:
        draw.text((left_x, y), fitted_lbl, font=active_font, fill="white")
    if fitted_val:
        final_val_w = measure_text_px(draw, fitted_val, active_font)
        draw.text((max(left_x, right_x - final_val_w + 1), y), fitted_val, font=active_font, fill="white")


def draw_gauge_bar(
    draw: ImageDraw.ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    ratio: float,
) -> None:
    """Dibuja una barra medidora horizontal nitida de 1 bit con marco fino."""
    clamped = max(0.0, min(1.0, float(ratio)))
    draw.rectangle((x1, y1, x2, y2), outline="white", fill="black")
    inner_left = x1 + 2
    inner_right = x2 - 2
    inner_top = y1 + 2
    inner_bottom = y2 - 2
    if inner_right < inner_left or inner_bottom < inner_top:
        return
    inner_max = inner_right - inner_left + 1
    fill_w = int(round(inner_max * clamped))
    if clamped > 0.02 and fill_w < 1:
        fill_w = 1
    if fill_w > 0:
        draw.rectangle(
            (inner_left, inner_top, min(inner_right, inner_left + fill_w - 1), inner_bottom),
            fill="white",
        )


def draw_battery_gauge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    percentage: int,
    segments: int = 5,
) -> None:
    """
    Dibuja un icono grafico de bateria de 1 bit con terminal positivo y segmentos
    encendidos proporcionales al porcentaje de carga (0..100).
    """
    pct = max(0, min(100, int(percentage)))
    body_right = x + max(14, width) - 4
    body_bottom = y + max(10, height) - 1
    draw.rectangle((x, y, body_right, body_bottom), outline="white", fill="black")

    # Terminal positivo derecho
    nub_top = y + max(2, height // 4)
    nub_bottom = body_bottom - max(2, height // 4)
    if nub_bottom < nub_top:
        nub_bottom = nub_top
    draw.rectangle((body_right + 1, nub_top, x + width - 1, nub_bottom), fill="white")

    # Interior segmentado con 2 px de margen respecto al contorno
    in_left = x + 2
    in_right = body_right - 2
    in_top = y + 2
    in_bottom = body_bottom - 2
    in_w = in_right - in_left + 1
    if in_w <= 2 or in_bottom < in_top:
        return

    seg_count = max(1, segments)
    gap = 1
    usable_w = in_w - (gap * (seg_count - 1))
    if usable_w < seg_count:
        draw_gauge_bar(draw, x, y, body_right, body_bottom, pct / 100.0)
        return

    lit_segments = int(round((pct / 100.0) * seg_count))
    if pct > 0 and lit_segments == 0:
        lit_segments = 1

    seg_w = usable_w // seg_count
    extra = usable_w % seg_count
    cur_x = in_left
    for idx in range(seg_count):
        w_this = seg_w + (1 if idx < extra else 0)
        if idx < lit_segments:
            draw.rectangle((cur_x, in_top, cur_x + w_this - 1, in_bottom), fill="white")
        cur_x += w_this + gap


def draw_scroll_arrows(
    draw: ImageDraw.ImageDraw,
    can_up: bool,
    can_down: bool,
    x: int = 120,
    top_y: int = 16,
    bottom_y: int = 57,
) -> None:
    """Dibuja indicadores discretos arriba/abajo cuando hay contenido desplazable."""
    if can_up:
        draw_mini_glyph(draw, "arrow_up", x=x, y=top_y, fill="white")
    if can_down:
        draw_mini_glyph(draw, "arrow_down", x=x, y=bottom_y, fill="white")


def draw_status_badge_icon(
    draw: ImageDraw.ImageDraw,
    kind: str,
    x: int,
    y: int,
) -> None:
    """
    Dibuja un icono semantico nitido de 12x12 px para cabeceras ligeras de avisos:
    - "ok": circulo con check
    - "info": circulo con i
    - "warn": triangulo con !
    - "warn_solid": triangulo solido blanco con ! negro
    - "error": circulo con x
    """
    token = kind.lower().strip()
    if token == "warn_solid":
        draw.polygon([(x + 5, y), (x, y + 9), (x + 10, y + 9)], fill="white")
        draw.line((x + 5, y + 3, x + 5, y + 6), fill="black")
        draw.point((x + 5, y + 8), fill="black")
        return

    if token == "warn":
        draw.polygon([(x + 5, y), (x, y + 10), (x + 10, y + 10)], outline="white")
        draw.line((x + 5, y + 3, x + 5, y + 6), fill="white")
        draw.point((x + 5, y + 8), fill="white")
        return

    draw.ellipse((x, y, x + 10, y + 10), outline="white")
    if token == "ok":
        draw.line((x + 2, y + 5, x + 4, y + 7), fill="white")
        draw.line((x + 4, y + 7, x + 8, y + 3), fill="white")
    elif token == "error":
        draw.line((x + 3, y + 3, x + 7, y + 7), fill="white")
        draw.line((x + 7, y + 3, x + 3, y + 7), fill="white")
    else:
        draw.point((x + 5, y + 3), fill="white")
        draw.line((x + 5, y + 5, x + 5, y + 8), fill="white")


def draw_titled_modal_box(
    draw: ImageDraw.ImageDraw,
    title: str,
    icon_glyph: Optional[str] = None,
    bottom_left_text: Optional[str] = None,
    bottom_right_text: Optional[str] = None,
    font: Optional[ImageFont.ImageFont] = None,
    width: int = OLED_WIDTH,
    height: int = OLED_HEIGHT,
) -> Tuple[int, int, int, int]:
    """
    Dibuja un marco modal TUI estilo W3 con titulo e icono incrustados en el borde
    superior y ayudas incrustadas en el borde inferior.
    Devuelve los limites interiores seguros `(x1, y1, x2, y2)`.
    """
    active_font = font or get_small_font()
    x_left = 2
    y_top = 6
    x_right = width - 3
    y_bottom = height - 7

    draw.rectangle((x_left, y_top, x_right, y_bottom), outline="white", fill="black")

    # Segmento superior incrustado: icono + titulo en mayusculas
    glyph_widths = {
        "bang": 3,
        "check": 7,
        "cross": 5,
        "info": 5,
        "warn": 7,
    }
    glyph_name = icon_glyph.lower().strip() if icon_glyph else ""
    gw = glyph_widths.get(glyph_name, 0)

    title_x = x_left + 4
    text_x = title_x + (gw + 3 if gw > 0 else 0)
    max_title_px = max(12, (x_right - 8) - text_x)
    clean_title = fit_text_px(draw, title.upper(), active_font, max_title_px)
    title_w = measure_text_px(draw, clean_title, active_font) if clean_title else 0

    end_x = (text_x + title_w + 1) if clean_title else (title_x + gw + 1)
    draw.line((title_x - 2, y_top, min(x_right - 3, end_x), y_top), fill="black")

    if gw > 0:
        draw_mini_glyph(draw, glyph_name, x=title_x, y=3, fill="white")
    if clean_title:
        draw_text_vcenter(
            draw,
            clean_title,
            x=text_x,
            y_top=2,
            y_bottom=10,
            font=active_font,
            fill="white",
        )

    # Ayudas incrustadas en el borde inferior
    right_limit = x_right - 4
    if bottom_right_text:
        clean_br = fit_text_px(draw, bottom_right_text.upper(), active_font, max(12, x_right - x_left - 12))
        if clean_br:
            rw = measure_text_px(draw, clean_br, active_font)
            rx = x_right - 5 - rw + 1
            draw.line((max(x_left + 3, rx - 2), y_bottom, min(x_right - 3, rx + rw + 1), y_bottom), fill="black")
            draw_text_vcenter(
                draw,
                clean_br,
                x=rx,
                y_top=53,
                y_bottom=61,
                font=active_font,
                fill="white",
            )
            right_limit = rx - 6

    if bottom_left_text:
        lx = x_left + 6
        max_bl = max(12, right_limit - lx)
        clean_bl = fit_text_px(draw, bottom_left_text.upper(), active_font, max_bl)
        if clean_bl:
            lw = measure_text_px(draw, clean_bl, active_font)
            draw.line((lx - 2, y_bottom, min(right_limit - 1, lx + lw + 1), y_bottom), fill="black")
            draw_text_vcenter(
                draw,
                clean_bl,
                x=lx,
                y_top=53,
                y_bottom=61,
                font=active_font,
                fill="white",
            )

    return (x_left + 4, 14, x_right - 4, 50)


# ---------------------------------------------------------------------------
# Primitivas del sistema unificado de Ficha Tecnica (Hibrido P1+P2+P3)
# ---------------------------------------------------------------------------

_MINI_GLYPH_WIDTHS = {
    "check": 7,
    "cross": 5,
    "warn": 7,
    "info": 5,
    "arrow_left": 5,
    "arrow_right": 5,
    "chevron_left": 4,
    "chevron_right": 4,
    "arrow_up": 5,
    "arrow_down": 5,
    "scroll": 5,
    "refresh": 6,
    "eye_open": 9,
    "eye_closed": 9,
    "bang": 3,
    "bolt": 5,
}


def draw_status_dot(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    active: bool = True,
    radius: int = 2,
) -> None:
    """Dibuja un indicador circular compacto de estado (solido=activo, hueco=inactivo)."""
    r = max(1, radius)
    box = (cx - r, cy - r, cx + r, cy + r)
    if active:
        draw.ellipse(box, fill="white", outline="white")
    else:
        draw.ellipse(box, fill="black", outline="white")


def draw_signal_bars(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    signal_pct: int,
    bars: int = 4,
) -> int:
    """
    Dibuja un medidor compacto de intensidad Wi-Fi (4 barras ascendentes, 11x8 px).
    Las barras apagadas muestran una marca base de 1 px para lectura nitida en 1-bit.
    Devuelve el ancho total ocupado en pixeles.
    """
    pct = max(0, min(100, int(signal_pct)))
    count = max(1, bars)
    lit = 0
    if pct > 0:
        lit = min(count, max(1, int((pct + (100 / count) - 1) // (100 / count))))

    heights = (2, 4, 6, 8) if count == 4 else tuple(max(2, int(round(8 * (i + 1) / count))) for i in range(count))
    base_y = y + 7
    cur_x = x
    for idx in range(count):
        h = heights[idx]
        if idx < lit:
            draw.rectangle((cur_x, base_y - h + 1, cur_x + 1, base_y), fill="white")
        else:
            draw.line((cur_x, base_y, cur_x + 1, base_y), fill="white")
        cur_x += 3
    return (count * 3) - 1


def draw_dotted_vline(
    draw: ImageDraw.ImageDraw,
    x: int,
    y1: int,
    y2: int,
    step: int = 2,
) -> None:
    """Dibuja una guia vertical punteada sutil para columnas tabulares en 1-bit."""
    for py in range(y1, y2 + 1, max(1, step)):
        draw.point((x, py), fill="white")


def draw_hero_metric_row(
    draw: ImageDraw.ImageDraw,
    tag: str,
    value: str,
    y: int = 15,
    left_x: int = 6,
    right_x: int = 121,
    font: Optional[ImageFont.ImageFont] = None,
    small_font: Optional[ImageFont.ImageFont] = None,
    status_active: Optional[bool] = None,
    signal_pct: Optional[int] = None,
) -> None:
    """
    Renderiza la fila protagonista limpia (Propuesta A) en fuente estandar (10 px):
    - Sin badges invertidos ni puntos decorativos que sobrecarguen el OLED.
    - Con `signal_pct`: texto alineado a la izquierda y barras de senal a la derecha.
    - Sin widget lateral: valor protagonista centrado limpiamente en el marco.
    """
    body_font = font or get_body_font()
    row_bottom = y + 9

    if signal_pct is not None:
        bars_w = 11
        bars_x = right_x - bars_w + 1
        draw_signal_bars(draw, x=bars_x, y=y + 1, signal_pct=signal_pct, bars=4)
        max_val_px = max(12, (bars_x - 4) - left_x + 1)
        fitted_val = fit_text_px(draw, value, body_font, max_val_px)
        if fitted_val:
            draw_text_vcenter(
                draw,
                fitted_val,
                x=left_x,
                y_top=y,
                y_bottom=row_bottom,
                font=body_font,
                fill="white",
            )
        return

    total_px = max(12, right_x - left_x + 1)
    fitted_val = fit_text_px(draw, value, body_font, total_px)
    if fitted_val:
        val_w = measure_text_px(draw, fitted_val, body_font)
        val_x = left_x + max(0, (total_px - val_w) // 2)
        draw_text_vcenter(
            draw,
            fitted_val,
            x=val_x,
            y_top=y,
            y_bottom=row_bottom,
            font=body_font,
            fill="white",
        )


def draw_tabular_kv_row(
    draw: ImageDraw.ImageDraw,
    label: str,
    value: str,
    y: int,
    left_x: int = 6,
    gutter_x: int = 36,
    right_x: int = 121,
    font: Optional[ImageFont.ImageFont] = None,
    status_active: Optional[bool] = None,
) -> None:
    """
    Dibuja una fila tabular de columna fija con guia vertical punteada (P3):
    etiqueta corta fija a la izquierda + separador punteado + valor alineado.
    """
    active_font = font or get_compact_font()
    row_bottom = y + 7

    lbl_max_px = max(10, gutter_x - left_x - 3)
    fitted_lbl = fit_text_px(draw, label.upper(), active_font, lbl_max_px, ellipsis="")
    if fitted_lbl:
        draw_text_vcenter(
            draw,
            fitted_lbl,
            x=left_x,
            y_top=y,
            y_bottom=row_bottom,
            font=active_font,
            fill="white",
        )

    draw_dotted_vline(draw, gutter_x, y + 1, y + 6, step=2)

    val_left = gutter_x + 4
    val_right = right_x
    if status_active is not None:
        dot_cx = right_x - 2
        draw_status_dot(draw, cx=dot_cx, cy=y + 3, active=status_active, radius=2)
        val_right = dot_cx - 6

    val_max_px = max(12, val_right - val_left + 1)
    fitted_val = fit_text_px(draw, value, active_font, val_max_px)
    if fitted_val:
        draw_text_vcenter(
            draw,
            fitted_val,
            x=val_left,
            y_top=y,
            y_bottom=row_bottom,
            font=active_font,
            fill="white",
        )


def draw_instrument_frame(
    draw: ImageDraw.ImageDraw,
    title: str,
    icon_glyph: Optional[str] = None,
    right_text: Optional[str] = None,
    page_index: int = 0,
    total_pages: int = 1,
    is_loading: bool = False,
    loading_tick: int = 0,
    bottom_left: Optional[Tuple[Optional[str], str]] = None,
    bottom_right: Optional[Tuple[Optional[str], str]] = None,
    font: Optional[ImageFont.ImageFont] = None,
    width: int = OLED_WIDTH,
    height: int = OLED_HEIGHT,
) -> Tuple[int, int, int, int]:
    """
    Marco tecnico unificado estilo instrumento (P2) para vistas de detalle:
    - Borde fino de 1 px (2, 6) - (width-3, height-7) coherente con NoticeView.
    - Borde superior incrustado: micro-icono + titulo a la izquierda y puntos de
      paginacion / estado / actividad a la derecha.
    - Borde inferior incrustado: atajos contextuales con micro-glifos (K2 / K3).
    Devuelve el rectangulo interior seguro `(x1, y1, x2, y2)`.
    """
    active_font = font or get_small_font()
    x_left = 2
    y_top = 6
    x_right = width - 3
    y_bottom = height - 7

    draw.rectangle((x_left, y_top, x_right, y_bottom), outline="white", fill="black")

    # --- Slot superior derecho: actividad, paginacion o estado ---
    top_right_limit = x_right - 6
    if is_loading:
        # 3 puntos animados discretos incrustados en el borde superior derecho
        dots_w = 11
        start_x = x_right - 5 - dots_w + 1
        draw.line((start_x - 2, y_top, min(x_right - 3, start_x + dots_w + 1), y_top), fill="black")
        active_dot = loading_tick % 3
        for idx in range(3):
            dx = start_x + (idx * 4)
            if idx == active_dot:
                draw.rectangle((dx, y_top - 1, dx + 2, y_top + 1), fill="white")
            else:
                draw.point((dx + 1, y_top), fill="white")
        top_right_limit = start_x - 5
    elif total_pages > 1:
        if total_pages <= 6:
            dots_w = (total_pages * 5) - 2
            start_x = x_right - 5 - dots_w + 1
            draw.line((start_x - 2, y_top, min(x_right - 3, start_x + dots_w + 1), y_top), fill="black")
            clamped_idx = max(0, min(page_index, total_pages - 1))
            for idx in range(total_pages):
                dx = start_x + (idx * 5)
                if idx == clamped_idx:
                    draw.rectangle((dx, y_top - 1, dx + 2, y_top + 1), fill="white")
                else:
                    draw.point((dx + 1, y_top), fill="white")
            top_right_limit = start_x - 5
        else:
            counter = f"{max(1, min(page_index + 1, total_pages))}/{total_pages}"
            cw = measure_text_px(draw, counter, active_font)
            cx = x_right - 5 - cw + 1
            draw.line((max(x_left + 16, cx - 2), y_top, min(x_right - 3, cx + cw + 1), y_top), fill="black")
            draw_text_vcenter(draw, counter, x=cx, y_top=2, y_bottom=10, font=active_font, fill="white")
            top_right_limit = cx - 5
    elif right_text:
        clean_rt = fit_text_px(draw, right_text.upper(), active_font, max_px=34, ellipsis="")
        if clean_rt:
            rw = measure_text_px(draw, clean_rt, active_font)
            rx = x_right - 5 - rw + 1
            draw.line((max(x_left + 16, rx - 2), y_top, min(x_right - 3, rx + rw + 1), y_top), fill="black")
            draw_text_vcenter(draw, clean_rt, x=rx, y_top=2, y_bottom=10, font=active_font, fill="white")
            top_right_limit = rx - 5

    # --- Slot superior izquierdo: micro-icono opcional + titulo ---
    glyph_name = icon_glyph.lower().strip() if icon_glyph else ""
    gw = _MINI_GLYPH_WIDTHS.get(glyph_name, 0)
    title_x = x_left + 4
    text_x = title_x + (gw + 3 if gw > 0 else 0)
    max_title_px = max(12, top_right_limit - text_x)
    clean_title = fit_text_px(draw, title.upper(), active_font, max_title_px)
    title_w = measure_text_px(draw, clean_title, active_font) if clean_title else 0

    end_x = (text_x + title_w + 1) if clean_title else (title_x + gw + 1)
    if clean_title or gw > 0:
        draw.line((title_x - 2, y_top, min(top_right_limit, end_x), y_top), fill="black")
    if gw > 0:
        draw_mini_glyph(draw, glyph_name, x=title_x, y=3, fill="white")
    if clean_title:
        draw_text_vcenter(draw, clean_title, x=text_x, y_top=2, y_bottom=10, font=active_font, fill="white")

    # --- Borde inferior incrustado: ayudas contextuales ---
    bottom_right_limit = x_right - 4
    if bottom_right is not None:
        br_glyph, br_label = bottom_right
        clean_br = fit_text_px(draw, br_label.upper(), active_font, max_px=48)
        if clean_br:
            br_gname = br_glyph.lower().strip() if br_glyph else ""
            br_gw = _MINI_GLYPH_WIDTHS.get(br_gname, 0)
            lw = measure_text_px(draw, clean_br, active_font)
            total_w = lw + (br_gw + 2 if br_gw > 0 else 0)
            start_x = x_right - 5 - total_w + 1
            draw.line(
                (max(x_left + 3, start_x - 2), y_bottom, min(x_right - 3, start_x + total_w + 1), y_bottom),
                fill="black",
            )
            if br_gw > 0:
                draw_mini_glyph(draw, br_gname, x=start_x, y=54, fill="white")
                lbl_x = start_x + br_gw + 2
            else:
                lbl_x = start_x
            draw_text_vcenter(draw, clean_br, x=lbl_x, y_top=53, y_bottom=61, font=active_font, fill="white")
            bottom_right_limit = start_x - 6

    if bottom_left is not None:
        bl_glyph, bl_label = bottom_left
        bl_gname = bl_glyph.lower().strip() if bl_glyph else ""
        bl_gw = _MINI_GLYPH_WIDTHS.get(bl_gname, 0)
        start_x = x_left + 6
        max_bl_px = max(10, bottom_right_limit - start_x - (bl_gw + 2 if bl_gw > 0 else 0))
        clean_bl = fit_text_px(draw, bl_label.upper(), active_font, max_px=max_bl_px)
        if clean_bl:
            lw = measure_text_px(draw, clean_bl, active_font)
            total_w = lw + (bl_gw + 2 if bl_gw > 0 else 0)
            draw.line(
                (start_x - 2, y_bottom, min(bottom_right_limit - 1, start_x + total_w + 1), y_bottom),
                fill="black",
            )
            if bl_gw > 0:
                draw_mini_glyph(draw, bl_gname, x=start_x, y=54, fill="white")
                lbl_x = start_x + bl_gw + 2
            else:
                lbl_x = start_x
            draw_text_vcenter(draw, clean_bl, x=lbl_x, y_top=53, y_bottom=61, font=active_font, fill="white")

    return (x_left + 4, 13, x_right - 4, 51)
