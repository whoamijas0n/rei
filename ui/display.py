"""
REI - Motor de Visualización y Jerarquía de Vistas OLED (128x64 px, 1-bit).

Implementa un sistema visual unificado para el HAT OLED Waveshare 1.3" SH1106
(SPI: DC=GPIO24, RST=GPIO25, rotate=2) con:
- Cabecera contextual y pies de página homogéneos en todas las vistas.
- Librería de iconos pixel-art 20x20 de alto contraste (ui/icons.py).
- Ajuste de texto exacto en píxeles (sin desbordes con fuentes proporcionales).
- Vista unificada de avisos y errores (NoticeView) con scroll y badges semánticos.
- Renderizado diferencial en ScreenManager para minimizar tráfico SPI a 30 FPS.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import urllib.parse

from PIL import Image, ImageDraw, ImageFont

from core.errors import NoticeLevel, sanitize_detail_lines, severity_to_notice_level
from core.parser import sanitize_oled_text
from .icons import IconRenderer
from .input_handler import InputEvent
from .theme import (
    BODY_BOTTOM,
    BODY_TOP,
    CONTENT_LEFT,
    FOOTER_BOTTOM,
    FOOTER_DIVIDER_Y,
    FOOTER_TOP,
    HEADER_BOTTOM,
    HEADER_DIVIDER_Y,
    HEADER_TOP,
    HERO_ICON_CX,
    HERO_ICON_CY,
    HERO_MAX_DOTS,
    HERO_TITLE_Y,
    OLED_HEIGHT,
    OLED_WIDTH,
    draw_battery_gauge,
    draw_clean_header_bar,
    draw_dotted_hline,
    draw_footer_bar,
    draw_frame,
    draw_gauge_bar,
    draw_header_bar,
    draw_hero_metric_row,
    draw_instrument_frame,
    draw_inverted_badge,
    draw_inverted_header_bar,
    draw_kv_row,
    draw_mini_glyph,
    draw_scroll_arrows,
    draw_scrollbar,
    draw_spinner,
    draw_status_badge_icon,
    draw_status_dot,
    draw_tabular_kv_row,
    draw_text_vcenter,
    draw_titled_modal_box,
    fit_text_px,
    get_body_font,
    get_hero_font,
    get_small_font,
    measure_text_px,
    wrap_text_px,
)

logger = logging.getLogger("REI.UI.Display")


class ViewActionType(Enum):
    """Acciones de navegación emitidas por las vistas hacia ScreenManager."""

    NONE = auto()
    PUSH_VIEW = auto()
    POP_VIEW = auto()
    POP_TO_ROOT = auto()
    REPLACE_VIEW = auto()
    EXECUTE_TASK = auto()


@dataclass
class ViewAction:
    """Carga útil devuelta por los manejadores de eventos de cada vista."""

    action_type: ViewActionType
    target_view: Optional["BaseView"] = None
    task_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)


class BaseView(ABC):
    """Clase base abstracta para todas las vistas OLED de REI."""

    def __init__(self, title: str = ""):
        self.title = title
        self._font: Optional[ImageFont.ImageFont] = None
        self._small_font: Optional[ImageFont.ImageFont] = None

    @property
    def font(self) -> ImageFont.ImageFont:
        """Devuelve la fuente principal del cuerpo (10 px, cargada perezosamente)."""
        if self._font is None:
            self._font = get_body_font()
        return self._font

    @property
    def small_font(self) -> ImageFont.ImageFont:
        """Devuelve la fuente compacta para cabeceras, badges y pies (9 px)."""
        if self._small_font is None:
            self._small_font = get_small_font()
        return self._small_font

    def get_text_width(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: Optional[ImageFont.ImageFont] = None,
    ) -> int:
        """Calcula el ancho exacto en píxeles de una cadena."""
        return measure_text_px(draw, text, font or self.font)

    def draw_centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        y: int,
        screen_width: int = OLED_WIDTH,
        fill: str = "white",
        font: Optional[ImageFont.ImageFont] = None,
        max_px: Optional[int] = None,
    ) -> None:
        """
        Renderiza texto ASCII seguro centrado horizontalmente en la coordenada Y.
        Si se supera `max_px`, recorta limpiamente con elipsis para evitar desbordes.
        """
        active_font = font or self.font
        limit_px = max_px if max_px is not None else max(16, screen_width - 10)
        clean_text = fit_text_px(draw, text, active_font, limit_px)
        if not clean_text:
            return
        text_w = measure_text_px(draw, clean_text, active_font)
        text_x = max(3, (screen_width - text_w) // 2)
        draw.text((text_x, y), clean_text, font=active_font, fill=fill)

    def draw_perimeter_border(self, draw: ImageDraw.ImageDraw) -> None:
        """Dibuja el borde perimetral continuo de 1 px: (1, 1) a (126, 62)."""
        draw_frame(draw)

    @abstractmethod
    def render(self, draw: ImageDraw.ImageDraw, width: int = OLED_WIDTH, height: int = OLED_HEIGHT) -> None:
        """Renderiza el contenido de la vista sobre el canvas PIL de 1 bit."""
        pass

    @abstractmethod
    def handle_input(self, event: InputEvent) -> ViewAction:
        """Procesa un evento de entrada y retorna una acción de navegación."""
        pass

    def update(self) -> None:
        """Gancho opcional ejecutado en cada fotograma (30 FPS)."""
        pass


@dataclass
class HeroCard:
    """Representación de una tarjeta individual dentro de un HeroCardDeckView."""

    title: str
    icon_name: str
    submenu: Optional[BaseView] = None
    action_task_id: Optional[str] = None
    on_select: Optional[Callable[[], None]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class HeroCardDeckView(BaseView):
    """
    Carrusel de tarjetas minimalistas (Paginación discreta + Icono central + Título inferior).

    Sin barra de cabecera superior para dar máximo aire visual al icono 20x20:
    - Paginación discreta en la esquina superior derecha (puntos hasta 10 tarjetas o N/M).
    - Icono pixel-art 20x20 de trazo fino centrado en (64, 24).
    - Flechas laterales discretas `‹ ›` cuando existen varias tarjetas.
    - Título en mayúsculas centrado en la parte inferior con ajuste exacto en píxeles.
    """

    def __init__(self, title: str, cards: Optional[List[HeroCard]] = None):
        super().__init__(title=title)
        self.cards: List[HeroCard] = cards or []
        self.active_index: int = 0

    def add_card(self, card: HeroCard) -> "HeroCardDeckView":
        """Agrega una tarjeta al mazo y devuelve el propio mazo."""
        self.cards.append(card)
        return self

    def render(self, draw: ImageDraw.ImageDraw, width: int = OLED_WIDTH, height: int = OLED_HEIGHT) -> None:
        # 1. Marco perimetral estándar
        draw_frame(draw, width, height)

        total = len(self.cards)

        if total == 0:
            IconRenderer.draw_icon(draw, "INFO", cx=HERO_ICON_CX, cy=HERO_ICON_CY)
            self.draw_centered_text(draw, "SIN ELEMENTOS", y=HERO_TITLE_Y, screen_width=width, max_px=width - 12)
            return

        self.active_index = max(0, min(self.active_index, total - 1))
        active_card = self.cards[self.active_index]

        # 2. Paginación discreta arriba a la derecha (sin cabecera ni línea divisoria)
        if 1 < total <= HERO_MAX_DOTS:
            dots_w = (total * 5) - 2
            start_x = (width - 5) - dots_w + 1
            for i in range(total):
                dot_x = start_x + (i * 5)
                if i == self.active_index:
                    draw.rectangle((dot_x, 4, dot_x + 2, 6), fill="white")
                else:
                    draw.point((dot_x + 1, 5), fill="white")
        elif total > HERO_MAX_DOTS:
            counter_txt = f"{self.active_index + 1}/{total}"
            counter_w = measure_text_px(draw, counter_txt, self.small_font)
            draw.text((max(4, width - 5 - counter_w), 2), counter_txt, font=self.small_font, fill="white")

        # 3. Indicadores laterales discretos de carrusel cuando hay más de una opción
        if total > 1:
            draw_mini_glyph(draw, "chevron_left", x=6, y=HERO_ICON_CY - 3)
            draw_mini_glyph(draw, "chevron_right", x=width - 10, y=HERO_ICON_CY - 3)

        # 4. Icono pixel-art 20x20 centrado con amplio espacio libre
        IconRenderer.draw_icon(draw, active_card.icon_name, cx=HERO_ICON_CX, cy=HERO_ICON_CY)

        # 5. Título en mayúsculas centrado en la parte inferior con recorte exacto en píxeles
        title_text = sanitize_oled_text(active_card.title).strip().upper()
        self.draw_centered_text(
            draw,
            title_text,
            y=HERO_TITLE_Y,
            screen_width=width,
            font=self.font,
            max_px=width - 12,
        )

    def handle_input(self, event: InputEvent) -> ViewAction:
        if not self.cards:
            if event in (InputEvent.KEY3, InputEvent.KEY1, InputEvent.BACK, InputEvent.ESCAPE):
                return ViewAction(ViewActionType.POP_VIEW)
            return ViewAction(ViewActionType.NONE)

        total_cards = len(self.cards)

        # Navegación izquierda / derecha en carrusel circular
        if event == InputEvent.RIGHT:
            self.active_index = (self.active_index + 1) % total_cards
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.LEFT:
            self.active_index = (self.active_index - 1 + total_cards) % total_cards
            return ViewAction(ViewActionType.NONE)

        # Selección: joystick PRESS, KEY1 o Enter
        if event in (InputEvent.PRESS, InputEvent.KEY1, InputEvent.ENTER):
            current_card = self.cards[self.active_index]
            if current_card.on_select:
                current_card.on_select()
            if current_card.submenu:
                return ViewAction(ViewActionType.PUSH_VIEW, target_view=current_card.submenu)
            if current_card.action_task_id:
                return ViewAction(
                    ViewActionType.EXECUTE_TASK,
                    task_id=current_card.action_task_id,
                    payload={"card_title": current_card.title},
                )
            return ViewAction(ViewActionType.NONE)

        # Retroceso: KEY3, BACK o Escape
        if event in (InputEvent.KEY3, InputEvent.BACK, InputEvent.ESCAPE):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)


def _build_structured_rows(
    draw: ImageDraw.ImageDraw,
    raw_lines: Sequence[str],
    font: ImageFont.ImageFont,
    max_px: int,
) -> List[Tuple[Optional[str], str]]:
    """
    Convierte lineas de texto en filas estructuradas:
    - `(clave, valor)` cuando la linea tiene formato `Clave: Valor` y cabe en 2 columnas.
    - `(None, texto)` envuelto en pixeles para prosa o valores largos.
    """
    rows: List[Tuple[Optional[str], str]] = []
    for raw in raw_lines:
        clean = sanitize_oled_text(raw).strip()
        if not clean:
            continue
        if ":" in clean:
            key_raw, val_raw = clean.split(":", 1)
            key = " ".join(key_raw.split())
            val = " ".join(val_raw.split())
            if (
                key
                and val
                and len(key) <= 12
                and "." not in key
                and not val_raw.lstrip().startswith("//")
                and (measure_text_px(draw, key, font) + 6 + measure_text_px(draw, val, font)) <= max_px
            ):
                rows.append((key, val))
                continue
        for wline in wrap_text_px(draw, [clean], font, max_px=max_px):
            rows.append((None, wline))
    return rows


@dataclass(frozen=True)
class DetailRow:
    """Fila tabular clave-valor de columna fija para el sistema de fichas tecnicas."""

    label: str
    value: str
    status_active: Optional[bool] = None


@dataclass(frozen=True)
class DetailEntity:
    """Entidad tecnica de 2 lineas (ej. interfaz de red o nodo SNMP) con estado."""

    title: str
    subtitle: str = ""
    badge: str = ""
    status_active: Optional[bool] = None


@dataclass(frozen=True)
class DetailHero:
    """Bloque protagonista superior (P1) con micro-badge invertido y widget opcional."""

    tag: str
    value: str
    status_active: Optional[bool] = None
    signal_pct: Optional[int] = None


@dataclass(frozen=True)
class DetailPage:
    """Pagina logica individual dentro de DetailCardView (hibrido P1+P2+P3)."""

    title: Optional[str] = None
    hero: Optional[DetailHero] = None
    rows: Tuple[DetailRow, ...] = ()
    entities: Tuple[DetailEntity, ...] = ()
    prose: Tuple[str, ...] = ()


_COMPACT_LABEL_MAP: Dict[str, str] = {
    "IPV4": "IPV4",
    "IP": "IP",
    "HOST": "HOST",
    "HOSTNAME": "HOST",
    "PUERTO": "PORT",
    "PORT": "PORT",
    "BAUD": "BAUD",
    "TARGET": "DEST",
    "DESTINO": "DEST",
    "CIPHER": "CIFR",
    "CIFRADO": "CIFR",
    "AUTH": "AUTH",
    "SUBNET": "SUBN",
    "SUBRED": "SUBN",
    "COMUNIDAD": "COM",
    "SENAL": "SNL",
    "SEÑAL": "SNL",
    "SEG": "SEG",
    "SEGURIDAD": "SEG",
    "ESTADO": "EST",
    "RED": "RED",
    "VAULT": "SEC",
    "LLAVES": "KEY",
    "HSM": "HSM",
    "IFACE": "IFAZ",
    "DRIVER": "DRV",
    "USER": "USR",
    "SHELL": "SH",
    "TEMP CPU": "TEMP",
    "CARGA": "CPU",
    "RAM": "RAM",
    "KERNEL": "KERN",
    "ALMACENAMIENTO": "DISK",
    "PROMPT": ">",
}


def _compact_detail_label(raw_key: str, max_len: int = 4) -> str:
    """Normaliza una etiqueta tecnica a un identificador tabular breve en mayusculas."""
    clean = " ".join(sanitize_oled_text(raw_key).strip().upper().split())
    if not clean:
        return "DATO"
    mapped = _COMPACT_LABEL_MAP.get(clean)
    if mapped:
        return mapped
    first_token = clean.split(" ", 1)[0]
    if len(first_token) <= max_len:
        return first_token
    return first_token[:max_len]


def _coerce_detail_page(raw: Any) -> Optional[DetailPage]:
    """Convierte un diccionario o instancia DetailPage en un objeto DetailPage seguro."""
    if isinstance(raw, DetailPage):
        return raw
    if not isinstance(raw, dict):
        return None

    hero: Optional[DetailHero] = None
    raw_hero = raw.get("hero")
    if isinstance(raw_hero, DetailHero):
        hero = raw_hero
    elif isinstance(raw_hero, dict) and str(raw_hero.get("value", "")).strip():
        sig_raw = raw_hero.get("signal_pct")
        sig_val = int(sig_raw) if isinstance(sig_raw, (int, float)) else None
        act_raw = raw_hero.get("status_active")
        act_val = bool(act_raw) if act_raw is not None else None
        hero = DetailHero(
            tag=str(raw_hero.get("tag", "")),
            value=str(raw_hero.get("value", "")),
            status_active=act_val,
            signal_pct=sig_val,
        )

    rows: List[DetailRow] = []
    for item in raw.get("rows") or ():
        if isinstance(item, DetailRow):
            rows.append(item)
        elif isinstance(item, dict):
            lbl = str(item.get("label", "")).strip()
            val = str(item.get("value", "")).strip()
            if lbl or val:
                act_raw = item.get("status_active")
                rows.append(
                    DetailRow(
                        label=lbl,
                        value=val,
                        status_active=bool(act_raw) if act_raw is not None else None,
                    )
                )
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            rows.append(DetailRow(label=str(item[0]), value=str(item[1])))

    entities: List[DetailEntity] = []
    for ent in raw.get("entities") or ():
        if isinstance(ent, DetailEntity):
            entities.append(ent)
        elif isinstance(ent, dict):
            title = str(ent.get("title", "")).strip()
            if title:
                act_raw = ent.get("status_active")
                entities.append(
                    DetailEntity(
                        title=title,
                        subtitle=str(ent.get("subtitle", "")).strip(),
                        badge=str(ent.get("badge", "")).strip(),
                        status_active=bool(act_raw) if act_raw is not None else None,
                    )
                )

    prose = tuple(
        sanitize_oled_text(str(line)).strip()
        for line in (raw.get("prose") or ())
        if sanitize_oled_text(str(line)).strip()
    )

    if hero is None and not rows and not entities and not prose:
        return None

    page_title = str(raw["title"]).strip() if raw.get("title") else None
    return DetailPage(
        title=page_title,
        hero=hero,
        rows=tuple(rows),
        entities=tuple(entities),
        prose=prose,
    )


def _auto_build_detail_pages(
    draw: ImageDraw.ImageDraw,
    raw_lines: Sequence[str],
    font: ImageFont.ImageFont,
    max_px: int = 116,
) -> List[DetailPage]:
    """
    Compila automaticamente lineas genericas en paginas P1+P2+P3:
    - Si las lineas son pares `Clave: Valor` (>= 2), construye Pagina 0 con
      bloque protagonista + 2 filas tabulares y paginas siguientes de 3 filas.
    - En caso de prosa libre, envuelve por pixeles y pagina en bloques de 3 lineas.
    """
    cleaned = [sanitize_oled_text(line).strip() for line in raw_lines if sanitize_oled_text(line).strip()]
    if not cleaned:
        return []

    kv_pairs: List[Tuple[str, str]] = []
    for line in cleaned:
        if ":" in line:
            k_raw, v_raw = line.split(":", 1)
            k = " ".join(k_raw.split())
            v = " ".join(v_raw.split())
            if k and v and len(k) <= 16 and "." not in k and not v_raw.lstrip().startswith("//"):
                kv_pairs.append((k, v))
                continue
        kv_pairs = []
        break

    if len(kv_pairs) >= 2:
        pages: List[DetailPage] = []
        hero_key, hero_val = kv_pairs[0]
        hero = DetailHero(tag=_compact_detail_label(hero_key), value=hero_val)
        rest_rows = [DetailRow(label=k, value=v) for k, v in kv_pairs[1:]]
        pages.append(DetailPage(hero=hero, rows=tuple(rest_rows[:2])))
        for idx in range(2, len(rest_rows), 3):
            pages.append(DetailPage(rows=tuple(rest_rows[idx : idx + 3])))
        return pages

    wrapped = wrap_text_px(draw, cleaned, font, max_px=max_px)
    if not wrapped:
        return []
    return [DetailPage(prose=tuple(wrapped[idx : idx + 3])) for idx in range(0, len(wrapped), 3)]


class DetailCardView(BaseView):
    """
    Vista sistematica de ficha tecnica (Propuesta A) en fuente estandar de 10 px:
    - Marco fino de instrumento (2, 6)-(125, 57) con cabecera superior incrustada,
      puntos de paginacion discretos y borde inferior completamente limpio.
    - Pagina principal: valor protagonista despejado (con barras de senal en Wi-Fi),
      un unico divisor punteado sutil y 2 filas limpias Clave / Valor.
    - Paginas secundarias navegables con LEFT/RIGHT (y UP/DOWN), sin wrapping roto.
    """

    VISIBLE_LINES: int = 3

    def __init__(
        self,
        title: str,
        initial_lines: Optional[List[str]] = None,
        on_refresh: Optional[Callable[[], None]] = None,
        pop_to_root_on_key1: bool = False,
        level: Optional[NoticeLevel] = None,
        metrics: Optional[Dict[str, Any]] = None,
        pages: Optional[Sequence[DetailPage]] = None,
    ):
        super().__init__(title=title)
        self.lines: List[str] = list(initial_lines) if initial_lines else []
        self.on_refresh = on_refresh
        self.pop_to_root_on_key1 = pop_to_root_on_key1
        self.status_text: str = "LISTO"
        self.level: Optional[NoticeLevel] = level
        self.metrics: Dict[str, Any] = dict(metrics) if metrics else {}
        self._explicit_pages: Optional[Tuple[DetailPage, ...]] = (
            tuple(pages) if pages is not None else None
        )
        self.is_loading: bool = False
        self.page_index: int = 0
        self._spinner_tick: int = 0
        self._pages_cache_key: Optional[Tuple[Any, ...]] = None
        self._pages_cache: List[DetailPage] = []

    @property
    def scroll_offset(self) -> int:
        """Alias sincronizado con `page_index` para retrocompatibilidad."""
        return self.page_index

    @scroll_offset.setter
    def scroll_offset(self, value: int) -> None:
        self.page_index = max(0, int(value))

    @property
    def total_pages(self) -> int:
        """Numero total de paginas compiladas actualmente."""
        return max(1, len(self._get_pages()))

    def _get_pages(
        self,
        draw: Optional[ImageDraw.ImageDraw] = None,
        max_px: int = 116,
    ) -> List[DetailPage]:
        """Resuelve y cachea las paginas estructuradas desde spec explicita, metricas o lineas."""
        if self._explicit_pages is not None:
            return list(self._explicit_pages)

        raw_metric_pages = self.metrics.get("detail_pages") if isinstance(self.metrics, dict) else None
        if isinstance(raw_metric_pages, (list, tuple)) and raw_metric_pages:
            parsed = [p for p in (_coerce_detail_page(item) for item in raw_metric_pages) if p is not None]
            if parsed:
                return parsed

        raw_tuple = tuple(str(x) for x in self.lines)
        cache_key = (raw_tuple, max_px)
        if cache_key != self._pages_cache_key:
            active_draw = draw or ImageDraw.Draw(Image.new("1", (OLED_WIDTH, OLED_HEIGHT), "black"))
            self._pages_cache = _auto_build_detail_pages(active_draw, raw_tuple, self.font, max_px=max_px)
            self._pages_cache_key = cache_key
        return self._pages_cache

    def set_content(
        self,
        lines: List[str],
        status: str = "OK",
        is_loading: bool = False,
        level: Optional[NoticeLevel] = None,
        metrics: Optional[Dict[str, Any]] = None,
        pages: Optional[Sequence[DetailPage]] = None,
    ) -> None:
        """Actualiza contenido, estado semantico, metricas, paginas e indicador de carga."""
        self.lines = list(lines) if lines else []
        self.status_text = status
        self.level = level
        self.metrics = dict(metrics) if metrics is not None else {}
        self._explicit_pages = tuple(pages) if pages is not None else None
        self.is_loading = is_loading
        self.page_index = 0
        self._pages_cache_key = None

    def append_line(self, line: str) -> None:
        """Agrega una nueva linea al bufer de detalle."""
        self.lines.append(line)
        self._pages_cache_key = None

    def update(self) -> None:
        """Avanza el contador de animacion del spinner a 30 FPS."""
        if self.is_loading:
            self._spinner_tick = (self._spinner_tick + 1) % 48

    def _resolve_level(self) -> Optional[NoticeLevel]:
        """Determina el nivel semantico activo de la vista."""
        if self.is_loading:
            return None
        if self.level is not None:
            return self.level
        token = (self.status_text or "").strip().upper()
        if not token or token == "LISTO":
            return None
        return severity_to_notice_level(token)

    def _frame_icon_and_status(self, active_level: Optional[NoticeLevel]) -> Tuple[Optional[str], Optional[str]]:
        """Mapea el nivel semantico al micro-icono superior izquierdo y etiqueta derecha."""
        if active_level == NoticeLevel.ERROR:
            return "cross", "ERR"
        if active_level == NoticeLevel.WARNING:
            return "warn", "AVISO"
        if active_level == NoticeLevel.OK:
            return None, "OK"
        return None, None

    def _render_entities(
        self,
        draw: ImageDraw.ImageDraw,
        entities: Sequence[DetailEntity],
        inner_x1: int,
        inner_x2: int,
    ) -> None:
        """Renderiza hasta 2 entidades limpias de 2 lineas separadas por un unico divisor punteado."""
        visible = list(entities[:2])
        if len(visible) == 1:
            slots = [(20, 32)]
        else:
            slots = [(13, 23), (36, 46)]

        for idx, ent in enumerate(visible):
            y_title, y_sub = slots[idx]
            if idx == 1:
                draw_dotted_hline(draw, inner_x1, inner_x2, 33, step=2)

            title_right = inner_x2
            clean_badge = sanitize_oled_text(ent.badge).strip().upper()
            if clean_badge:
                bw = measure_text_px(draw, clean_badge, self.small_font)
                bx = max(inner_x1 + 16, inner_x2 - bw + 1)
                draw_text_vcenter(
                    draw,
                    clean_badge,
                    x=bx,
                    y_top=y_title,
                    y_bottom=y_title + 8,
                    font=self.small_font,
                    fill="white",
                )
                title_right = bx - 4

            fitted_title = fit_text_px(draw, ent.title, self.font, max(12, title_right - inner_x1 + 1))
            if fitted_title:
                draw_text_vcenter(
                    draw,
                    fitted_title,
                    x=inner_x1,
                    y_top=y_title,
                    y_bottom=y_title + 8,
                    font=self.font,
                    fill="white",
                )

            if ent.subtitle:
                fitted_sub = fit_text_px(draw, ent.subtitle, self.font, max(12, inner_x2 - inner_x1 + 1))
                if fitted_sub:
                    draw_text_vcenter(
                        draw,
                        fitted_sub,
                        x=inner_x1,
                        y_top=y_sub,
                        y_bottom=y_sub + 8,
                        font=self.font,
                        fill="white",
                    )

    def _render_page_body(
        self,
        draw: ImageDraw.ImageDraw,
        page: DetailPage,
        inner_x1: int,
        inner_x2: int,
    ) -> None:
        """Renderiza el cuerpo interior limpio (Propuesta A) en fuente estandar de 10 px."""
        if page.hero is not None:
            draw_hero_metric_row(
                draw,
                tag=page.hero.tag,
                value=page.hero.value,
                y=15,
                left_x=inner_x1,
                right_x=inner_x2,
                font=self.font,
                small_font=self.small_font,
                signal_pct=page.hero.signal_pct,
            )
            draw_dotted_hline(draw, inner_x1, inner_x2, 28, step=2)

            if page.rows:
                y_positions = (32, 44)
                for row, y in zip(page.rows[:2], y_positions):
                    draw_kv_row(
                        draw,
                        label=row.label,
                        value=row.value,
                        y=y,
                        left_x=inner_x1,
                        right_x=inner_x2,
                        font=self.font,
                    )
            elif page.prose:
                y_positions = (32, 44)
                max_px = max(16, inner_x2 - inner_x1 + 1)
                for line, y in zip(page.prose[:2], y_positions):
                    fitted = fit_text_px(draw, line, self.font, max_px)
                    if fitted:
                        draw_text_vcenter(draw, fitted, x=inner_x1, y_top=y, y_bottom=y + 8, font=self.font)
            return

        if page.entities:
            self._render_entities(draw, page.entities, inner_x1, inner_x2)
            return

        if page.rows:
            visible_rows = page.rows[:3]
            if len(visible_rows) == 1:
                y_positions = (29,)
            elif len(visible_rows) == 2:
                y_positions = (22, 36)
            else:
                y_positions = (15, 29, 43)
            for row, y in zip(visible_rows, y_positions):
                draw_kv_row(
                    draw,
                    label=row.label,
                    value=row.value,
                    y=y,
                    left_x=inner_x1,
                    right_x=inner_x2,
                    font=self.font,
                )
            return

        if page.prose:
            visible_lines = page.prose[:3]
            if len(visible_lines) == 1:
                y_positions = (29,)
            elif len(visible_lines) == 2:
                y_positions = (22, 36)
            else:
                y_positions = (15, 28, 41)
            max_px = max(16, inner_x2 - inner_x1 + 1)
            for line, y in zip(visible_lines, y_positions):
                fitted = fit_text_px(draw, line, self.font, max_px)
                if fitted:
                    draw_text_vcenter(draw, fitted, x=inner_x1, y_top=y, y_bottom=y + 8, font=self.font)

    def render(self, draw: ImageDraw.ImageDraw, width: int = OLED_WIDTH, height: int = OLED_HEIGHT) -> None:
        if self.is_loading:
            draw_instrument_frame(
                draw,
                title=self.title,
                is_loading=True,
                loading_tick=self._spinner_tick // 5,
                font=self.small_font,
                width=width,
                height=height,
            )
            draw_spinner(draw, cx=width // 2, cy=28, tick=self._spinner_tick // 3, radius=4)
            loading_msg = self.lines[0] if self.lines else "Consultando..."
            self.draw_centered_text(
                draw,
                loading_msg,
                y=41,
                screen_width=width,
                font=self.font,
                max_px=width - 16,
            )
            return

        inner_max_px = max(24, width - 12)
        pages = self._get_pages(draw, max_px=inner_max_px)
        total = max(1, len(pages))
        self.page_index = max(0, min(self.page_index, total - 1))
        active_page = pages[self.page_index] if pages else None

        active_level = self._resolve_level()
        icon_glyph, status_tag = self._frame_icon_and_status(active_level)
        frame_title = (active_page.title if active_page and active_page.title else self.title)

        bottom_left: Optional[Tuple[Optional[str], str]] = (
            (None, "ENT INICIO") if self.pop_to_root_on_key1 else None
        )

        inner_x1, _, inner_x2, _ = draw_instrument_frame(
            draw,
            title=frame_title,
            icon_glyph=icon_glyph,
            right_text=status_tag if total == 1 else None,
            page_index=self.page_index,
            total_pages=total,
            bottom_left=bottom_left,
            bottom_right=None,
            font=self.small_font,
            width=width,
            height=height,
        )

        if active_page is None:
            self.draw_centered_text(
                draw,
                "Sin datos disponibles",
                y=29,
                screen_width=width,
                font=self.font,
                max_px=max(16, inner_x2 - inner_x1 + 1),
            )
            return

        self._render_page_body(draw, active_page, inner_x1, inner_x2)

    def handle_input(self, event: InputEvent, **kwargs: Any) -> ViewAction:
        pages = self._get_pages(max_px=OLED_WIDTH - 12)
        total = max(1, len(pages))
        max_page = total - 1

        if event == InputEvent.RIGHT:
            if total > 1:
                self.page_index = (self.page_index + 1) % total
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.LEFT:
            if total > 1:
                self.page_index = (self.page_index - 1 + total) % total
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.UP:
            self.page_index = max(0, self.page_index - 1)
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.DOWN:
            self.page_index = min(max_page, self.page_index + 1)
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.KEY2:
            if self.on_refresh:
                self.is_loading = True
                self.page_index = 0
                self.on_refresh()
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.KEY1, InputEvent.PRESS, InputEvent.ENTER):
            if self.pop_to_root_on_key1:
                return ViewAction(ViewActionType.POP_TO_ROOT)
            return ViewAction(ViewActionType.POP_VIEW)

        if event in (InputEvent.KEY3, InputEvent.BACK, InputEvent.ESCAPE):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)


class BatteryTelemetryView(DetailCardView):
    """
    Vista dedicada de telemetria de bateria (PiSugar 3) alineada a la Propuesta A:
    - Marco fino de instrumento (2, 6)-(125, 57) con estado electrico en cabecera.
    - Bloque protagonista con icono de bateria proporcional + porcentaje grande.
    - Unico divisor punteado y 2 filas clave-valor limpias (Voltaje y Consumo).
    """

    def render(self, draw: ImageDraw.ImageDraw, width: int = OLED_WIDTH, height: int = OLED_HEIGHT) -> None:
        if self.is_loading or not self.metrics or "percentage" not in self.metrics:
            super().render(draw, width, height)
            return

        pct = max(0, min(100, int(self.metrics.get("percentage", 0))))
        voltage = float(self.metrics.get("voltage", 0.0))
        current_ma = int(self.metrics.get("current_ma", 0))
        power_w = float(self.metrics.get("power_w", 0.0))
        is_charging = bool(self.metrics.get("is_charging", False))
        is_plugged = bool(self.metrics.get("is_power_plugged", False))
        is_low = (not is_charging and not is_plugged and pct <= 15)

        if is_low:
            state_badge = "BAJA"
        elif is_charging:
            state_badge = "CARGA"
        elif is_plugged and pct >= 98:
            state_badge = "LLENA"
        elif is_plugged:
            state_badge = "EXT"
        else:
            state_badge = "BAT"

        inner_x1, _, inner_x2, _ = draw_instrument_frame(
            draw,
            title="BATERIA",
            icon_glyph="warn" if is_low else None,
            right_text=state_badge,
            font=self.small_font,
            width=width,
            height=height,
        )

        # Bloque protagonista: icono grafico de bateria + cifra grande
        draw_battery_gauge(draw, x=inner_x1, y=15, width=40, height=14, percentage=pct)

        hero_font = get_hero_font()
        pct_text = f"{pct}%"
        pct_w = measure_text_px(draw, pct_text, hero_font)
        pct_x = max(inner_x1 + 48, inner_x2 - pct_w + 1)

        if hasattr(draw, "textbbox"):
            hb = draw.textbbox((0, 0), pct_text, font=hero_font)
            glyph_h = max(1, int(hb[3] - hb[1]))
            pct_y = 14 + (16 - glyph_h) // 2 - int(hb[1])
        else:
            pct_y = 16
        draw.text((pct_x, pct_y), pct_text, font=hero_font, fill="white")

        if is_charging:
            draw_mini_glyph(draw, "bolt", x=max(inner_x1 + 43, pct_x - 8), y=19, fill="white")

        # Unico divisor punteado sutil y 2 filas clave-valor limpias
        draw_dotted_hline(draw, inner_x1, inner_x2, 31, step=2)
        draw_kv_row(draw, "Voltaje", f"{voltage:.2f}V", y=34, left_x=inner_x1, right_x=inner_x2, font=self.font)
        cons_str = (
            f"+{current_ma}mA {power_w:.1f}W"
            if current_ma > 0
            else f"{abs(current_ma)}mA {power_w:.1f}W"
        )
        cons_label = "Carga" if current_ma > 0 else "Cons."
        draw_kv_row(draw, cons_label, cons_str, y=45, left_x=inner_x1, right_x=inner_x2, font=self.font)


class SystemTelemetryView(DetailCardView):
    """
    Vista paginada de telemetria del sistema alineada a la Propuesta A (LEFT/RIGHT):
    - Marco fino de instrumento con puntos de pagina incrustados en cabecera.
    - Pagina 0: 3 barras medidoras aireadas (TEMP, RAM, CPU).
    - Pagina 1: 3 filas limpias Clave / Valor (RAM, Uptime, Kernel).
    """

    TOTAL_PAGES: int = 2

    def __init__(
        self,
        title: str = "SISTEMA",
        initial_lines: Optional[List[str]] = None,
        on_refresh: Optional[Callable[[], None]] = None,
        level: Optional[NoticeLevel] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            title=title,
            initial_lines=initial_lines,
            on_refresh=on_refresh,
            level=level,
            metrics=metrics,
        )
        self.page_index: int = 0

    def set_content(
        self,
        lines: List[str],
        status: str = "OK",
        is_loading: bool = False,
        level: Optional[NoticeLevel] = None,
        metrics: Optional[Dict[str, Any]] = None,
        pages: Optional[Sequence[DetailPage]] = None,
    ) -> None:
        super().set_content(
            lines=lines,
            status=status,
            is_loading=is_loading,
            level=level,
            metrics=metrics,
            pages=pages,
        )
        self.page_index = 0

    def render(self, draw: ImageDraw.ImageDraw, width: int = OLED_WIDTH, height: int = OLED_HEIGHT) -> None:
        if self.is_loading or not self.metrics or "temp" not in self.metrics:
            super().render(draw, width, height)
            return

        temp_c = float(self.metrics.get("temp", 0.0))
        load1 = float(self.metrics.get("load", 0.0))
        cpu_count = max(1, int(self.metrics.get("cpu_count", 4)))
        ram_used_mb = int(self.metrics.get("ram_used_mb", 0))
        ram_total_mb = max(1, int(self.metrics.get("ram_total_mb", 512)))
        ram_pct = max(0, min(100, int(self.metrics.get("ram_pct", round((ram_used_mb / float(ram_total_mb)) * 100)))))
        kernel = str(self.metrics.get("kernel", "--"))
        uptime = str(self.metrics.get("uptime", "--"))

        is_warn = (temp_c >= 75.0 or ram_pct >= 90 or self._resolve_level() == NoticeLevel.WARNING)

        inner_x1, _, inner_x2, _ = draw_instrument_frame(
            draw,
            title="SISTEMA",
            icon_glyph="warn" if is_warn else None,
            page_index=self.page_index,
            total_pages=self.TOTAL_PAGES,
            font=self.small_font,
            width=width,
            height=height,
        )

        if self.page_index == 0:
            gauge_x1 = inner_x1 + 32
            gauge_x2 = inner_x2 - 34
            rows = [
                ("TEMP", (temp_c - 20.0) / 65.0, f"{temp_c:.1f}C", 15),
                ("RAM", ram_pct / 100.0, f"{ram_pct}%", 29),
                ("CPU", load1 / float(cpu_count), f"{load1:.2f}", 43),
            ]
            for label, ratio, val_txt, y in rows:
                draw_text_vcenter(draw, label, x=inner_x1, y_top=y, y_bottom=y + 8, font=self.font, fill="white")
                draw_gauge_bar(draw, gauge_x1, y + 1, gauge_x2, y + 7, ratio=ratio)
                val_w = measure_text_px(draw, val_txt, self.font)
                val_x = max(gauge_x2 + 4, inner_x2 - val_w + 1)
                draw_text_vcenter(draw, val_txt, x=val_x, y_top=y, y_bottom=y + 8, font=self.font, fill="white")
        else:
            kv_rows = [
                ("RAM", f"{ram_used_mb} / {ram_total_mb}MB"),
                ("Uptime", uptime),
                ("Kernel", kernel),
            ]
            for (lbl, val), y in zip(kv_rows, (15, 29, 43)):
                draw_kv_row(draw, lbl, val, y=y, left_x=inner_x1, right_x=inner_x2, font=self.font)

    def handle_input(self, event: InputEvent, **kwargs: Any) -> ViewAction:
        if self.is_loading or not self.metrics or "temp" not in self.metrics:
            return super().handle_input(event, **kwargs)

        if event in (InputEvent.RIGHT, InputEvent.DOWN):
            self.page_index = (self.page_index + 1) % self.TOTAL_PAGES
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.LEFT, InputEvent.UP):
            self.page_index = (self.page_index - 1 + self.TOTAL_PAGES) % self.TOTAL_PAGES
            return ViewAction(ViewActionType.NONE)

        return super().handle_input(event, **kwargs)


class NoticeView(BaseView):
    """
    Vista unificada para anuncios, confirmaciones, advertencias y errores.

    Todas las severidades (OK, INFO, AVISO y ERROR) comparten el formato modal
    W3 (caja con titulo e icono incrustados en el borde superior y ayuda en el
    borde inferior), diferenciandose unicamente por su micro-icono semantico.
    """

    VISIBLE_LINES: int = 3
    LINE_PITCH: int = 11

    def __init__(
        self,
        title: str,
        lines: Sequence[str],
        level: NoticeLevel = NoticeLevel.INFO,
        action_label: Optional[str] = None,
        on_action: Optional[Callable[[], None]] = None,
        pop_to_root_on_ok: bool = False,
    ):
        super().__init__(title=title)
        self.lines: List[str] = sanitize_detail_lines(list(lines))
        self.level: NoticeLevel = severity_to_notice_level(level)
        self.action_label: Optional[str] = action_label
        self.on_action: Optional[Callable[[], None]] = on_action
        self.pop_to_root_on_ok: bool = pop_to_root_on_ok
        self.scroll_offset: int = 0
        self._rows_cache_key: Optional[Tuple[Tuple[str, ...], int]] = None
        self._rows_cache: List[Tuple[Optional[str], str]] = []

    def _get_rows(
        self,
        draw: Optional[ImageDraw.ImageDraw] = None,
        max_px: int = 114,
    ) -> List[Tuple[Optional[str], str]]:
        raw_tuple = tuple(self.lines)
        key = (raw_tuple, max_px)
        if key != self._rows_cache_key:
            active_draw = draw or ImageDraw.Draw(Image.new("1", (OLED_WIDTH, OLED_HEIGHT), "black"))
            self._rows_cache = _build_structured_rows(active_draw, raw_tuple, self.font, max_px=max_px)
            self._rows_cache_key = key
        return self._rows_cache

    def _w3_glyph(self) -> str:
        if self.level == NoticeLevel.ERROR:
            return "cross"
        if self.level == NoticeLevel.WARNING:
            return "bang"
        if self.level == NoticeLevel.OK:
            return "check"
        return "info"

    def render(self, draw: ImageDraw.ImageDraw, width: int = OLED_WIDTH, height: int = OLED_HEIGHT) -> None:
        if self.on_action:
            bl_txt: Optional[str] = f"ENT {(self.action_label or 'OK').upper()}"
            br_txt = "K3 NO"
        else:
            bl_txt = None
            br_txt = "K3 OK"

        inner_x1, _, inner_x2, _ = draw_titled_modal_box(
            draw,
            title=self.title,
            icon_glyph=self._w3_glyph(),
            bottom_left_text=bl_txt,
            bottom_right_text=br_txt,
            font=self.small_font,
            width=width,
            height=height,
        )
        body_start_y = 16
        left_pad = inner_x1
        base_max_px = max(24, inner_x2 - inner_x1 + 1)
        scroll_max_px = max(20, base_max_px - 7)
        row_right_plain = inner_x2
        row_right_scroll = inner_x2 - 7
        arrow_x = width - 11

        rows = self._get_rows(draw, max_px=base_max_px)
        needs_scroll = len(rows) > self.VISIBLE_LINES
        if needs_scroll:
            rows = self._get_rows(draw, max_px=scroll_max_px)

        max_scroll = max(0, len(rows) - self.VISIBLE_LINES)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))
        visible = rows[self.scroll_offset : self.scroll_offset + self.VISIBLE_LINES]

        if not visible:
            self.draw_centered_text(draw, "Operacion finalizada", y=28, screen_width=width)
        else:
            if not needs_scroll and len(visible) == 1:
                y = body_start_y + 10
            elif not needs_scroll and len(visible) == 2:
                y = body_start_y + 4
            else:
                y = body_start_y

            row_right_x = row_right_scroll if needs_scroll else row_right_plain
            for key, val in visible:
                if key is not None:
                    draw_kv_row(
                        draw,
                        label=key,
                        value=val,
                        y=y,
                        left_x=left_pad,
                        right_x=row_right_x,
                        font=self.font,
                    )
                else:
                    draw.text((left_pad, y), val, font=self.font, fill="white")
                y += self.LINE_PITCH

        if needs_scroll:
            draw_scroll_arrows(
                draw,
                can_up=self.scroll_offset > 0,
                can_down=self.scroll_offset < max_scroll,
                x=arrow_x,
                top_y=body_start_y,
                bottom_y=45,
            )

    def handle_input(self, event: InputEvent, **kwargs: Any) -> ViewAction:
        rows = self._get_rows(max_px=OLED_WIDTH - 20)
        max_scroll = max(0, len(rows) - self.VISIBLE_LINES)

        if event == InputEvent.UP:
            self.scroll_offset = max(0, self.scroll_offset - 1)
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.DOWN:
            self.scroll_offset = min(max_scroll, self.scroll_offset + 1)
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.KEY1, InputEvent.PRESS, InputEvent.ENTER):
            if self.on_action:
                self.on_action()
                return ViewAction(ViewActionType.NONE)
            if self.pop_to_root_on_ok:
                return ViewAction(ViewActionType.POP_TO_ROOT)
            return ViewAction(ViewActionType.POP_VIEW)

        if event in (InputEvent.KEY3, InputEvent.BACK, InputEvent.ESCAPE):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)


class ConfirmDialogView(BaseView):
    """
    Dialogo modal de confirmacion dedicado (C1) sin scroll:
    - Icono centrado arriba (20x20 px).
    - Pregunta corta en 1 sola linea centrada.
    - Dos botones seleccionables con LEFT/RIGHT y foco seguro inicial en cancelar (NO).
    """

    def __init__(
        self,
        title: str,
        question: str,
        icon_name: str = "POWEROFF",
        cancel_label: str = "NO",
        confirm_label: str = "SI",
        on_confirm: Optional[Callable[[], None]] = None,
        default_confirm: bool = False,
    ):
        super().__init__(title=title)
        self.question: str = question
        self.icon_name: str = icon_name
        self.cancel_label: str = cancel_label
        self.confirm_label: str = confirm_label
        self.on_confirm: Optional[Callable[[], None]] = on_confirm
        self.selected_confirm: bool = default_confirm

    def _draw_button(
        self,
        draw: ImageDraw.ImageDraw,
        box: Tuple[int, int, int, int],
        label: str,
        focused: bool,
    ) -> None:
        x1, y1, x2, y2 = box
        btn_w = max(12, x2 - x1 + 1)
        if focused:
            draw.rectangle(box, fill="white")
            text_fill = "black"
        else:
            draw.rectangle(box, outline="white", fill="black")
            text_fill = "white"

        fitted = fit_text_px(draw, label.upper(), self.small_font, max(8, btn_w - 6))
        if fitted:
            txt_w = measure_text_px(draw, fitted, self.small_font)
            txt_x = x1 + max(0, (btn_w - txt_w) // 2)
            draw_text_vcenter(
                draw,
                fitted,
                x=txt_x,
                y_top=y1,
                y_bottom=y2,
                font=self.small_font,
                fill=text_fill,
            )

    def render(self, draw: ImageDraw.ImageDraw, width: int = OLED_WIDTH, height: int = OLED_HEIGHT) -> None:
        IconRenderer.draw_icon(draw, self.icon_name, cx=width // 2, cy=13)
        self.draw_centered_text(
            draw,
            self.question.upper(),
            y=27,
            screen_width=width,
            font=self.font,
            max_px=width - 8,
        )
        mid_gap_left = (width // 2) - 4
        mid_gap_right = (width // 2) + 3
        self._draw_button(draw, (4, 44, mid_gap_left, 59), self.cancel_label, focused=not self.selected_confirm)
        self._draw_button(draw, (mid_gap_right, 44, width - 5, 59), self.confirm_label, focused=self.selected_confirm)

    def handle_input(self, event: InputEvent, **kwargs: Any) -> ViewAction:
        if event == InputEvent.LEFT:
            self.selected_confirm = False
            return ViewAction(ViewActionType.NONE)

        if event == InputEvent.RIGHT:
            self.selected_confirm = True
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.KEY1, InputEvent.PRESS, InputEvent.ENTER):
            if not self.selected_confirm:
                return ViewAction(ViewActionType.POP_VIEW)
            if self.on_confirm:
                self.on_confirm()
                return ViewAction(ViewActionType.NONE)
            return ViewAction(ViewActionType.POP_VIEW)

        if event in (InputEvent.KEY3, InputEvent.BACK, InputEvent.ESCAPE):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)


class UpdateProgressView(BaseView):
    """
    Pantalla de progreso bloqueante para actualizaciones, diagnosticos HID y tareas criticas.

    BLOQUEO ESTRICTO: Mientras `is_running=True`, ignora todas las entradas del usuario.
    Al finalizar (`is_finished=True`), muestra el reporte limpio sin marco pesado (4 lineas
    visibles con flechas de scroll), cabecera invertida en ERROR o limpia en OK.
    """

    VISIBLE_LINES: int = 4
    LINE_START_Y: int = 17
    LINE_PITCH: int = 11

    def __init__(
        self,
        title: str,
        on_start: Optional[Callable[[], None]] = None,
        indeterminate: bool = False,
        warning_text: Optional[str] = "NO APAGAR",
    ):
        super().__init__(title=title)
        self.on_start = on_start
        self.indeterminate: bool = bool(indeterminate)
        self.warning_text: Optional[str] = warning_text
        self.stage_message: str = "Iniciando..."
        self.progress: float = 0.0
        self.is_running: bool = True
        self.is_finished: bool = False
        self.is_success: bool = False
        self.result_summary: str = ""
        self.result_details: List[str] = []
        self.scroll_offset: int = 0
        self._anim_tick: int = 0
        self._start_time: float = time.monotonic()
        self._started: bool = False
        self._result_lines_cache_key: Optional[Tuple[Tuple[str, ...], int]] = None
        self._result_lines_cache: List[str] = []

    def reset(self, stage_message: str = "Iniciando...") -> None:
        """Reinicia completamente la vista para poder reutilizarla en nuevas ejecuciones."""
        self.stage_message = stage_message
        self.progress = 0.0
        self.is_running = True
        self.is_finished = False
        self.is_success = False
        self.result_summary = ""
        self.result_details = []
        self.scroll_offset = 0
        self._anim_tick = 0
        self._start_time = time.monotonic()
        self._started = False
        self._result_lines_cache_key = None

    def start(self) -> None:
        """Inicia la tarea en segundo plano asociada si existe callback."""
        if self.is_finished:
            self.reset()
        if not self._started and self.on_start:
            self._started = True
            self._start_time = time.monotonic()
            self.on_start()

    def set_progress(self, message: str, progress: float) -> None:
        """Actualiza de forma segura la fase y el porcentaje de progreso (0.0 a 1.0)."""
        self.stage_message = message
        self.progress = max(0.0, min(1.0, progress))

    def set_completed(
        self,
        success: bool,
        summary: str,
        details: Optional[List[str]] = None,
    ) -> None:
        """Transiciona la vista del estado en ejecución al reporte final."""
        self.is_running = False
        self.is_finished = True
        self.is_success = success
        self.result_summary = summary
        self.result_details = list(details) if details else []
        self.progress = 1.0
        self.scroll_offset = 0
        self._result_lines_cache_key = None

    def update(self) -> None:
        """Actualiza la animación de progreso a 30 FPS."""
        if not self._started and self.on_start and not self.is_finished:
            self.start()
        if self.is_running:
            self._anim_tick = (self._anim_tick + 1) % 60

    def _build_finished_lines(
        self,
        draw: Optional[ImageDraw.ImageDraw] = None,
        max_px: int = 112,
    ) -> List[str]:
        """Combina resumen y detalles finales sin duplicados y envueltos por píxeles."""
        raw_items: List[str] = []
        summary_clean = sanitize_oled_text(self.result_summary).strip()
        if summary_clean:
            raw_items.append(summary_clean)

        for item in self.result_details:
            clean_item = sanitize_oled_text(item).strip()
            if not clean_item:
                continue
            if clean_item.lower() in ("ok/key3: salir", "ok / key3: salir"):
                continue
            if not raw_items or clean_item.lower() != raw_items[0].lower():
                raw_items.append(clean_item)

        if not raw_items:
            raw_items = ["Proceso completado." if self.is_success else "Error en la operacion."]

        key = (tuple(raw_items), max_px)
        if key != self._result_lines_cache_key:
            active_draw = draw or ImageDraw.Draw(Image.new("1", (OLED_WIDTH, OLED_HEIGHT), "black"))
            self._result_lines_cache = wrap_text_px(active_draw, raw_items, self.font, max_px=max_px)
            self._result_lines_cache_key = key
        return self._result_lines_cache

    def render(self, draw: ImageDraw.ImageDraw, width: int = OLED_WIDTH, height: int = OLED_HEIGHT) -> None:
        if self.is_running:
            inner_x1, _, inner_x2, _ = draw_instrument_frame(
                draw,
                title=self.title,
                is_loading=True,
                loading_tick=self._anim_tick // 5,
                font=self.small_font,
                width=width,
                height=height,
            )
            draw_spinner(draw, cx=width // 2, cy=27, tick=self._anim_tick // 3, radius=4)
            self.draw_centered_text(
                draw,
                self.stage_message or "Procesando...",
                y=40,
                screen_width=width,
                font=self.font,
                max_px=max(16, inner_x2 - inner_x1 + 1),
            )
            return

        # Estado finalizado (Exito y Error unificados en formato modal W3)
        inner_x1, _, inner_x2, _ = draw_titled_modal_box(
            draw,
            title=self.title,
            icon_glyph="check" if self.is_success else "cross",
            bottom_right_text="K3 OK",
            font=self.small_font,
            width=width,
            height=height,
        )
        vis_count = 3
        base_max_px = max(24, inner_x2 - inner_x1 + 1)
        scroll_max_px = max(20, base_max_px - 7)
        wrapped = self._build_finished_lines(draw, max_px=base_max_px)
        needs_scroll = len(wrapped) > vis_count
        if needs_scroll:
            wrapped = self._build_finished_lines(draw, max_px=scroll_max_px)

        max_scroll = max(0, len(wrapped) - vis_count)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))
        visible = wrapped[self.scroll_offset : self.scroll_offset + vis_count]

        if not needs_scroll and len(visible) == 1:
            y = 26
        elif not needs_scroll and len(visible) == 2:
            y = 20
        else:
            y = 16
        for line in visible:
            draw.text((inner_x1, y), line, font=self.font, fill="white")
            y += self.LINE_PITCH

        if needs_scroll:
            draw_scroll_arrows(
                draw,
                can_up=self.scroll_offset > 0,
                can_down=self.scroll_offset < max_scroll,
                x=width - 11,
                top_y=16,
                bottom_y=45,
            )

    def handle_input(self, event: InputEvent, **kwargs: Any) -> ViewAction:
        if self.is_running:
            # BLOQUEO ESTRICTO: no se permite salir ni interrumpir mientras corre
            return ViewAction(ViewActionType.NONE)

        # Permitir desplazar reportes largos al concluir
        vis_count = 3
        wrapped = self._build_finished_lines(max_px=OLED_WIDTH - 20)
        max_scroll = max(0, len(wrapped) - vis_count)
        if event == InputEvent.UP:
            self.scroll_offset = max(0, self.scroll_offset - 1)
            return ViewAction(ViewActionType.NONE)
        if event == InputEvent.DOWN:
            self.scroll_offset = min(max_scroll, self.scroll_offset + 1)
            return ViewAction(ViewActionType.NONE)

        # Cuando concluye, permitir salir con KEY3, KEY1, PRESS, BACK, ENTER o ESCAPE
        if event in (
            InputEvent.KEY3,
            InputEvent.KEY1,
            InputEvent.PRESS,
            InputEvent.BACK,
            InputEvent.ENTER,
            InputEvent.ESCAPE,
        ):
            return ViewAction(ViewActionType.POP_VIEW)

        return ViewAction(ViewActionType.NONE)


class QRCodeView(BaseView):
    """
    Renderiza un código QR de 1 bit en el OLED SH1106 (128x64 px) con columna
    informativa a la derecha alineada al sistema visual unificado (cero solapes).
    Permite alternar entre URL Wi-Fi y URL USB mediante KEY2.
    """

    def __init__(
        self,
        title: str = "REPORTE QR",
        url: str = "http://10.0.0.1:8000/r/latest",
        subtitle: Optional[str] = None,
        alt_url: Optional[str] = None,
        net_label: Optional[str] = None,
        alt_label: Optional[str] = None,
        on_exit: Optional[Callable[[], None]] = None,
    ):
        super().__init__(title=title)
        self.url = url
        self.alt_url = alt_url
        self.net_label = net_label
        self.alt_label = alt_label
        self.subtitle = subtitle or "Escanea con tu movil"
        self.on_exit = on_exit
        self._qr_image: Optional[Image.Image] = None
        self._generate_qr()

    def _generate_qr(self) -> None:
        """Genera la imagen QR de 1 bit para la URL activa."""
        try:
            import qrcode

            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=2,
                border=1,
            )
            qr.add_data(self.url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="white", back_color="black")
            self._qr_image = img.convert("1")
        except Exception as ex:
            logger.error(f"Error generando código QR para '{self.url}': {ex}")
            self._qr_image = None

    def render(self, draw: ImageDraw.ImageDraw, width: int = OLED_WIDTH, height: int = OLED_HEIGHT) -> None:
        draw_frame(draw, width, height)

        if self._qr_image is not None:
            qr_w, qr_h = self._qr_image.size
            if qr_w <= 56 and qr_h <= 56:
                start_x = 6
                start_y = max(2, (height - qr_h) // 2)
            else:
                start_x = 2
                start_y = max(1, (height - qr_h) // 2)

            draw.bitmap((start_x, start_y), self._qr_image, fill="white")

            # Columna derecha estrictamente separada y medida en píxeles
            text_x = max(66, start_x + qr_w + 4)
            col_w = max(16, (width - 4) - text_x)

            draw.text((text_x, 4), fit_text_px(draw, "REPORTE", self.small_font, col_w), font=self.small_font, fill="white")
            draw.text((text_x, 13), fit_text_px(draw, "MOVIL", self.small_font, col_w), font=self.small_font, fill="white")
            draw.line((text_x, 23, width - 5, 23), fill="white")

            # Indicador de red activo con badge invertido de alto contraste
            current_label = self.net_label
            if not current_label:
                host = urllib.parse.urlparse(self.url).hostname or ""
                if host.startswith("10.0.0."):
                    current_label = "USB PC"
                elif host:
                    current_label = "WIFI"
                else:
                    current_label = "RED"

            badge_text = fit_text_px(draw, current_label.upper(), self.small_font, max(16, col_w - 4))
            badge_w = measure_text_px(draw, badge_text, self.small_font) + 4
            draw.rectangle((text_x, 26, min(width - 5, text_x + badge_w), 36), fill="white")
            draw_text_vcenter(draw, badge_text, x=text_x + 2, y_top=26, y_bottom=36, font=self.small_font, fill="black")

            # Alternancia de red o host actual
            if self.alt_url:
                toggle_hint = fit_text_px(
                    draw,
                    f"K2:{(self.alt_label or 'ALT').upper()}",
                    self.small_font,
                    col_w,
                )
                draw.text((text_x, 40), toggle_hint, font=self.small_font, fill="white")
            else:
                host_str = fit_text_px(
                    draw,
                    urllib.parse.urlparse(self.url).hostname or "10.0.0.1",
                    self.small_font,
                    col_w,
                )
                draw.text((text_x, 40), host_str, font=self.small_font, fill="white")

            # Indicador de salida consistente con glifo
            draw_mini_glyph(draw, "arrow_left", x=text_x, y=52)
            draw.text((text_x + 7, 50), fit_text_px(draw, "K3 SALIR", self.small_font, max(12, col_w - 7)), font=self.small_font, fill="white")
            return

        # Fallback estructurado si falla la generación del QR
        draw_header_bar(
            draw,
            self.title or "REPORTE QR",
            font=self.small_font,
            right_badge=NoticeLevel.ERROR.value,
            width=width,
        )
        fallback_lines = wrap_text_px(
            draw,
            ["No se pudo generar QR.", "URL:", self.url],
            self.small_font,
            max_px=116,
        )
        y = 17
        for line in fallback_lines[:3]:
            draw.text((CONTENT_LEFT, y), line, font=self.small_font, fill="white")
            y += 11
        draw_footer_bar(draw, hints=[("arrow_left", "K3")], font=self.small_font, width=width)

    def handle_input(self, event: InputEvent, **kwargs: Any) -> ViewAction:
        if event == InputEvent.KEY2 and self.alt_url:
            self.url, self.alt_url = self.alt_url, self.url
            self.net_label, self.alt_label = self.alt_label, self.net_label
            self._generate_qr()
            return ViewAction(ViewActionType.NONE)

        if event in (InputEvent.KEY3, InputEvent.KEY1, InputEvent.PRESS, InputEvent.BACK, InputEvent.KEY2):
            if self.on_exit:
                self.on_exit()
            return ViewAction(ViewActionType.POP_VIEW)
        return ViewAction(ViewActionType.NONE)


# Re-exportación de vistas de teclado para retrocompatibilidad
from .keyboard_view import KeyboardLayer, VirtualKeyboardInputView
from .wifi_password_view import WiFiPasswordView

KeyboardInputView = VirtualKeyboardInputView


class ScreenManager:
    """
    Gestor de pantallas y renderizado OLED SH1106 con doble búfer y envío
    diferencial por SPI para reducir carga de CPU/bus en la Pi Zero 2 W.
    Soporta SPI directo (DC=GPIO24, RST=GPIO25, rotate=2) con fallback a I2C o virtual.
    """

    def __init__(
        self,
        width: int = OLED_WIDTH,
        height: int = OLED_HEIGHT,
        gpio_dc: int = 24,
        gpio_rst: int = 25,
        bus_speed_hz: int = 8000000,
        rotate: int = 2,
        i2c_port: int = 1,
        i2c_address: int = 0x3C,
    ):
        self.width = width
        self.height = height
        self.rotate = rotate
        self._view_stack: List[BaseView] = []
        self._oled_device: Optional[Any] = None
        self._buffer: Image.Image = Image.new("1", (self.width, self.height), "black")
        self._draw: ImageDraw.ImageDraw = ImageDraw.Draw(self._buffer)
        self._last_frame_bytes: Optional[bytes] = None

        self._initialize_display_hardware(
            gpio_dc=gpio_dc,
            gpio_rst=gpio_rst,
            bus_speed_hz=bus_speed_hz,
            rotate=rotate,
            i2c_port=i2c_port,
            i2c_address=i2c_address,
        )

    def _initialize_display_hardware(
        self,
        gpio_dc: int,
        gpio_rst: int,
        bus_speed_hz: int,
        rotate: int,
        i2c_port: int,
        i2c_address: int,
    ) -> None:
        """Inicializa el controlador luma.oled SH1106 por SPI con fallback a I2C."""
        try:
            from luma.core.interface.serial import i2c, spi
            from luma.oled.device import sh1106

            try:
                serial_interface = spi(
                    device=0,
                    port=0,
                    bus_speed_hz=bus_speed_hz,
                    gpio_DC=gpio_dc,
                    gpio_RST=gpio_rst,
                )
                self._oled_device = sh1106(
                    serial_interface,
                    width=self.width,
                    height=self.height,
                    rotate=rotate,
                )
                logger.info(
                    f"Initialized SH1106 OLED via SPI (DC={gpio_dc}, RST={gpio_rst}, rotate={rotate})."
                )
                return
            except Exception as spi_ex:
                logger.warning(f"SPI initialization failed ({spi_ex}), trying I2C fallback...")

            try:
                serial_interface = i2c(port=i2c_port, address=i2c_address)
                self._oled_device = sh1106(
                    serial_interface,
                    width=self.width,
                    height=self.height,
                    rotate=rotate,
                )
                logger.info(f"Initialized SH1106 OLED via I2C (port={i2c_port}, addr={hex(i2c_address)}).")
                return
            except Exception as i2c_ex:
                logger.error(
                    f"I2C initialization failed ({i2c_ex}). "
                    "Panel OLED NO disponible: REI sigue ejecutándose, pero la "
                    "pantalla física queda en blanco (la UI se dibuja en un "
                    "framebuffer virtual). Revisa /dev/spidev0.0 y que el firmware "
                    "haya arrancado con dtparam=spi=on."
                )

        except ImportError as ex:
            self._oled_device = None
            logger.error(
                f"luma/Pillow no disponibles ({ex}). "
                "REI arranca sin panel OLED (framebuffer virtual)."
            )
        except Exception as ex:
            self._oled_device = None
            logger.error(f"Error inesperado al inicializar el panel OLED: {ex}")

    @property
    def display_ready(self) -> bool:
        """True si el panel OLED físico está inicializado y listo para renderizar."""
        return self._oled_device is not None

    @property
    def current_view(self) -> Optional[BaseView]:
        """Devuelve la vista activa en la cima de la pila de navegación."""
        return self._view_stack[-1] if self._view_stack else None

    def push_view(self, view: BaseView) -> None:
        """Apila una nueva vista sobre la pila de navegación."""
        self._view_stack.append(view)
        logger.debug(f"Pushed view: {view.title} (Stack depth: {len(self._view_stack)})")

    def pop_view(self) -> Optional[BaseView]:
        """Extrae la vista superior de la pila manteniendo siempre la raíz."""
        if len(self._view_stack) > 1:
            popped = self._view_stack.pop()
            logger.debug(f"Popped view: {popped.title} (Stack depth: {len(self._view_stack)})")
            return popped
        return None

    def replace_view(self, view: BaseView) -> None:
        """Reemplaza la vista superior sin modificar el resto de la pila."""
        if self._view_stack:
            self._view_stack[-1] = view
        else:
            self._view_stack = [view]

    def pop_to_root(self) -> None:
        """Regresa directamente a la vista raíz."""
        if len(self._view_stack) > 1:
            self._view_stack = [self._view_stack[0]]
            logger.debug("Popped to root view (Stack depth: 1)")

    def set_root_view(self, view: BaseView) -> None:
        """Reinicia la pila estableciendo la vista raíz."""
        self._view_stack = [view]

    def _render_error_fallback(self, err: Exception) -> None:
        """Dibuja una pantalla de recuperación limpia si una vista falla al renderizar."""
        draw_frame(self._draw, self.width, self.height)
        draw_header_bar(
            self._draw,
            "ERROR DE VISTA",
            font=get_small_font(),
            right_badge=NoticeLevel.ERROR.value,
            width=self.width,
        )
        msg = fit_text_px(self._draw, str(err) or "Fallo de renderizado", get_small_font(), 116)
        self._draw.text((CONTENT_LEFT, 20), "Fallo al dibujar vista.", font=get_small_font(), fill="white")
        self._draw.text((CONTENT_LEFT, 32), msg, font=get_small_font(), fill="white")
        draw_footer_bar(self._draw, hints=[("arrow_left", "K3")], font=get_small_font(), width=self.width)

    def render(self, force: bool = False) -> Image.Image:
        """
        Renderiza la vista activa en el búfer 128x64 de 1 bit y envía el frame
        al panel SH1106 solo cuando existen cambios de píxeles (o `force=True`).
        """
        self._draw.rectangle([0, 0, self.width - 1, self.height - 1], fill="black")

        view = self.current_view
        if view:
            try:
                view.update()
                view.render(self._draw, self.width, self.height)
            except Exception as render_ex:
                logger.exception(f"Error renderizando vista '{view.title}': {render_ex}")
                self._draw.rectangle([0, 0, self.width - 1, self.height - 1], fill="black")
                self._render_error_fallback(render_ex)

        if self._oled_device is not None:
            frame_bytes = self._buffer.tobytes()
            if force or frame_bytes != self._last_frame_bytes:
                try:
                    self._oled_device.display(self._buffer)
                    self._last_frame_bytes = frame_bytes
                except Exception as ex:
                    logger.error(f"Error transferring frame to OLED: {ex}")

        return self._buffer

    @property
    def buffer(self) -> Image.Image:
        """Acceso directo al búfer PIL de 1 bit actual."""
        return self._buffer

    def clear(self) -> None:
        """Limpia el panel físico al cerrar la aplicación."""
        self._last_frame_bytes = None
        if self._oled_device is not None:
            try:
                self._oled_device.clear()
            except Exception as ex:
                logger.debug(f"No se pudo limpiar OLED al salir: {ex}")
