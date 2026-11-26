"""
Pruebas unitarias del sistema visual OLED unificado, iconos pixel-art,
vistas consistentes, renderizado diferencial y formateo de errores de REI.
"""

import os
import unittest
from unittest.mock import MagicMock

from PIL import Image, ImageDraw

from core.errors import NoticeLevel, format_oled_error, severity_to_notice_level
from core.interfaces import DiagnosticStatus, Severity
from core.plugins import (
    BatteryStatusPlugin,
    CiscoSerialPlugin,
    CiscoSSHPlugin,
    IPAddressPlugin,
    LinuxSSHPlugin,
    PoweroffPlugin,
    SNMPScanPlugin,
    SystemStatusPlugin,
    VaultPlugin,
    WindowsRNDISPlugin,
)
from ui.display import (
    BatteryTelemetryView,
    ConfirmDialogView,
    DetailCardView,
    DetailEntity,
    DetailHero,
    DetailPage,
    DetailRow,
    HeroCard,
    HeroCardDeckView,
    IconRenderer,
    NoticeView,
    QRCodeView,
    ScreenManager,
    SystemTelemetryView,
    UpdateProgressView,
    ViewActionType,
    VirtualKeyboardInputView,
    WiFiPasswordView,
)
from ui.icons import ICON_SIZE, _ICON_SPRITES, get_icon_bitmap, resolve_icon_name
from ui.input_handler import InputEvent
from ui.theme import (
    OLED_HEIGHT,
    OLED_WIDTH,
    fit_text_px,
    get_body_font,
    get_small_font,
    measure_text_px,
    wrap_text_px,
)


def _new_canvas():
    img = Image.new("1", (OLED_WIDTH, OLED_HEIGHT), "black")
    return img, ImageDraw.Draw(img)


def _assert_clean_outer_margin(tc: unittest.TestCase, img: Image.Image, label: str) -> None:
    """Verifica que ninguna vista dibuje píxeles fuera del marco perimetral (0 o 127 / 0 o 63)."""
    px = img.load()
    for x in range(OLED_WIDTH):
        tc.assertEqual(px[x, 0], 0, f"{label}: píxel fuera de marco superior en x={x}")
        tc.assertEqual(px[x, OLED_HEIGHT - 1], 0, f"{label}: píxel fuera de marco inferior en x={x}")
    for y in range(OLED_HEIGHT):
        tc.assertEqual(px[0, y], 0, f"{label}: píxel fuera de marco izquierdo en y={y}")
        tc.assertEqual(px[OLED_WIDTH - 1, y], 0, f"{label}: píxel fuera de marco derecho en y={y}")


class TestIconLibrary(unittest.TestCase):
    """Valida dimensiones, compilación y cobertura semántica de iconos 20x20."""

    def test_all_sprites_have_exact_20x20_geometry(self) -> None:
        for name, grid in _ICON_SPRITES.items():
            self.assertEqual(len(grid), ICON_SIZE, f"Icono {name} no tiene 20 filas")
            for idx, row in enumerate(grid):
                self.assertEqual(len(row), ICON_SIZE, f"Icono {name} fila {idx} no tiene 20 columnas")
                self.assertTrue(set(row).issubset({".", "#"}), f"Carácter inválido en {name} fila {idx}")
            bmp = get_icon_bitmap(name)
            self.assertEqual(bmp.size, (ICON_SIZE, ICON_SIZE))
            self.assertTrue(any(bmp.tobytes()), f"Icono {name} está completamente vacío")

    def test_semantic_icons_are_distinct(self) -> None:
        """Comprueba que conceptos distintos ya no colapsan a ondas Wi-Fi o al mismo sprite."""
        self.assertNotEqual(resolve_icon_name("SUCCESS"), resolve_icon_name("WIFI_OK"))
        self.assertNotEqual(resolve_icon_name("ERROR"), resolve_icon_name("WIFI_FAIL"))
        self.assertNotEqual(resolve_icon_name("COMPLETO"), resolve_icon_name("WIFI_OK"))
        self.assertNotEqual(resolve_icon_name("ESPAÑOL"), resolve_icon_name("INGLES"))
        self.assertNotEqual(resolve_icon_name("TRASH"), resolve_icon_name("POWEROFF"))
        self.assertNotEqual(resolve_icon_name("SWITCH"), resolve_icon_name("NETWORK"))
        self.assertNotEqual(resolve_icon_name("LINUX"), resolve_icon_name("SSH"))
        self.assertEqual(resolve_icon_name("NO_EXISTE_XYZ"), "DEFAULT")

    def test_draw_icon_renders_pixels(self) -> None:
        for name in IconRenderer.list_icons():
            img, draw = _new_canvas()
            IconRenderer.draw_icon(draw, name, cx=64, cy=28)
            self.assertTrue(any(img.tobytes()), f"Icono {name} no dibujó píxeles")


class TestThemeTextLayout(unittest.TestCase):
    """Valida ajuste y envoltura de texto exactos en píxeles con Pillow proporcional."""

    def test_fit_text_px_never_exceeds_max_width(self) -> None:
        _, draw = _new_canvas()
        font = get_body_font()
        samples = [
            "WWWWWWWWWWWWWWWWWWWWWWWW",
            "ACTUALIZAR SISTEMA OPERATIVO COMPLETO",
            "MiRedCorporativa_5GHz_Invitados_Edificio_Norte",
            "A",
        ]
        for max_px in (40, 80, 114):
            for text in samples:
                fitted = fit_text_px(draw, text, font, max_px=max_px)
                self.assertLessEqual(
                    measure_text_px(draw, fitted, font),
                    max_px,
                    f"Texto '{fitted}' excedió {max_px}px",
                )

    def test_wrap_text_px_respects_pixel_budget(self) -> None:
        _, draw = _new_canvas()
        font = get_body_font()
        wrapped = wrap_text_px(
            draw,
            [
                "No se pudo conectar al servidor de repositorios APT por falta de red.",
                "SUPERTOKENMUYLARGOSINESPACIOS1234567890ABCDEFGHIJ",
            ],
            font,
            max_px=112,
        )
        self.assertGreaterEqual(len(wrapped), 4)
        for line in wrapped:
            self.assertLessEqual(measure_text_px(draw, line, font), 112, f"Línea excede 112px: {line}")


class TestErrorFormatter(unittest.TestCase):
    """Valida traducción limpia de errores técnicos a avisos legibles en español."""

    def test_known_error_categories(self) -> None:
        net_err = format_oled_error(OSError("[Errno 101] Network is unreachable"))
        self.assertEqual(net_err.title, "SIN CONEXION")
        self.assertEqual(net_err.level, NoticeLevel.ERROR)

        perm_err = format_oled_error(PermissionError("[Errno 13] Permission denied: '/dev/hidg0'"))
        self.assertEqual(perm_err.title, "SIN PERMISOS")

        hid_err = format_oled_error("Dispositivo /dev/hidg0 no disponible")
        self.assertEqual(hid_err.title, "USB NO LISTO")
        self.assertEqual(hid_err.level, NoticeLevel.WARNING)

        timeout_err = format_oled_error("Operation timed out waiting for host", context="HID")
        self.assertEqual(timeout_err.title, "TIMEOUT USB")

        apt_err = format_oled_error("E: Could not get lock /var/lib/dpkg/lock-frontend")
        self.assertEqual(apt_err.title, "APT OCUPADO")

    def test_severity_mapping(self) -> None:
        self.assertEqual(severity_to_notice_level(Severity.OK), NoticeLevel.OK)
        self.assertEqual(severity_to_notice_level(Severity.WARNING), NoticeLevel.WARNING)
        self.assertEqual(severity_to_notice_level(Severity.CRITICAL), NoticeLevel.ERROR)
        self.assertEqual(severity_to_notice_level(DiagnosticStatus.FAILED), NoticeLevel.ERROR)
        self.assertEqual(severity_to_notice_level("AVISO"), NoticeLevel.WARNING)


class TestUnifiedViews(unittest.TestCase):
    """Valida consistencia de renderizado, bordes y lógica de teclas en todas las vistas."""

    def test_hero_card_deck_navigation_and_bounds(self) -> None:
        deck = HeroCardDeckView("UTILIDADES")
        deck.add_card(HeroCard("CONEXION DE RED", "NETWORK"))
        deck.add_card(HeroCard("ESTADO BATERIA", "BATTERY"))
        deck.add_card(HeroCard("ACTUALIZACIONES DEL SISTEMA MUY LARGO", "UPDATE"))

        for _ in range(3):
            img, draw = _new_canvas()
            deck.render(draw, OLED_WIDTH, OLED_HEIGHT)
            _assert_clean_outer_margin(self, img, f"HeroCardDeckView idx={deck.active_index}")
            # Verificar que no se dibuja barra de cabecera ni divisor superior en y=13
            px = img.load()
            self.assertEqual(px[10, 13], 0, "HeroCardDeckView no debe tener divisor de cabecera en y=13")
            action = deck.handle_input(InputEvent.RIGHT)
            self.assertEqual(action.action_type, ViewActionType.NONE)

        self.assertEqual(deck.active_index, 0)
        self.assertEqual(deck.handle_input(InputEvent.LEFT).action_type, ViewActionType.NONE)
        self.assertEqual(deck.active_index, 2)
        self.assertEqual(deck.handle_input(InputEvent.KEY3).action_type, ViewActionType.POP_VIEW)

    def test_hero_card_deck_many_items_counter(self) -> None:
        deck = HeroCardDeckView("REDES WI-FI")
        for i in range(12):
            deck.add_card(HeroCard(f"RED_CORPORATIVA_{i:02d}", "LOCK"))
        img, draw = _new_canvas()
        deck.render(draw, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img, "HeroCardDeckView >7 items")

    def test_detail_card_loading_and_scroll(self) -> None:
        refreshed = []
        view = DetailCardView(
            title="ESTADO SISTEMA",
            initial_lines=[
                "Temp CPU: 45.2 C",
                "Carga: 0.42",
                "RAM: 142MB / 512MB",
                "Kernel: 6.18.50+rpt-rpi-v7",
                "Almacenamiento: OverlayFS activo en raiz",
            ],
            on_refresh=lambda: refreshed.append(True),
            level=NoticeLevel.OK,
        )
        img, draw = _new_canvas()
        view.render(draw, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img, "DetailCardView loaded")

        view.handle_input(InputEvent.DOWN)
        self.assertGreater(view.scroll_offset, 0)
        view.handle_input(InputEvent.UP)
        self.assertEqual(view.scroll_offset, 0)

        view.handle_input(InputEvent.KEY2)
        self.assertTrue(view.is_loading)
        self.assertEqual(len(refreshed), 1)

        view.update()
        img_load, draw_load = _new_canvas()
        view.render(draw_load, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_load, "DetailCardView loading")

    def test_notice_view_scroll_and_action(self) -> None:
        triggered = []
        view = NoticeView(
            title="CLAVE INCORRECTA",
            lines=[
                "La clave ingresada fue rechazada por el punto de acceso.",
                "Verifique mayusculas, simbolos y distribucion de teclado.",
                "Presione ENT para intentar con una clave nueva.",
            ],
            level=NoticeLevel.ERROR,
            action_label="CLAVE",
            on_action=lambda: triggered.append(True),
        )
        img, draw = _new_canvas()
        view.render(draw, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img, "NoticeView error")

        view.handle_input(InputEvent.DOWN)
        self.assertGreater(view.scroll_offset, 0)
        self.assertEqual(view.handle_input(InputEvent.ENTER).action_type, ViewActionType.NONE)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(view.handle_input(InputEvent.KEY3).action_type, ViewActionType.POP_VIEW)

    def test_update_progress_strict_lock_reset_and_scroll(self) -> None:
        started = []
        view = UpdateProgressView(title="ACTUALIZAR SISTEMA", on_start=lambda: started.append(True))
        view.start()
        self.assertEqual(len(started), 1)

        # Bloqueo estricto mientras corre
        for ev in (InputEvent.KEY1, InputEvent.KEY2, InputEvent.KEY3, InputEvent.UP, InputEvent.DOWN, InputEvent.PRESS):
            self.assertEqual(view.handle_input(ev).action_type, ViewActionType.NONE)

        view.set_progress("Actualizando APT...", 0.45)
        img_run, draw_run = _new_canvas()
        view.render(draw_run, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_run, "UpdateProgressView running")
        px_run = img_run.load()
        self.assertNotEqual(px_run[2, 30], 0, "UpdateProgressView running debe usar el marco unificado")
        self.assertNotEqual(px_run[125, 30], 0, "UpdateProgressView running debe tener borde derecho")
        self.assertNotEqual(px_run[64, 57], 0, "UpdateProgressView running debe mantener borde inferior limpio")
        # Sin barra de progreso horizontal en y=18: zona de spinner limpia
        self.assertEqual(px_run[10, 18], 0, "UpdateProgressView no debe dibujar barra de progreso")

        # Variante indeterminada (escaner Wi-Fi / IA) comparte el mismo layout de spinner + subtitulo
        ind_view = UpdateProgressView(title="ESCANER WI-FI", indeterminate=True)
        ind_view.stage_message = "Buscando redes..."
        ind_view.update()
        img_ind, draw_ind = _new_canvas()
        ind_view.render(draw_ind, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_ind, "UpdateProgressView indeterminate")
        px_ind = img_ind.load()
        self.assertNotEqual(px_ind[2, 30], 0)
        self.assertNotEqual(px_ind[125, 30], 0)
        self.assertNotEqual(px_ind[64, 57], 0, "Carga indeterminada debe mantener borde inferior limpio")

        # Finalización con texto largo desplazable
        view.set_completed(
            success=False,
            summary="Fallo al conectar",
            details=[
                "No se pudo conectar a los repositorios Debian.",
                "Verifique su conexion Wi-Fi o Ethernet y reintente.",
            ],
        )
        img_done, draw_done = _new_canvas()
        view.render(draw_done, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_done, "UpdateProgressView finished")

        view.handle_input(InputEvent.DOWN)
        self.assertGreater(view.scroll_offset, 0)
        self.assertEqual(view.handle_input(InputEvent.KEY3).action_type, ViewActionType.POP_VIEW)

        # Re-ejecución limpia tras reset automático en start()
        view.start()
        self.assertTrue(view.is_running)
        self.assertFalse(view.is_finished)
        self.assertEqual(len(started), 2)

    def test_wifi_password_and_virtual_keyboard_bounds(self) -> None:
        pw = WiFiPasswordView(ssid="RedCorporativa_5G", initial_text="ClaveSegura123")
        for vis in (False, True):
            pw.is_visible = vis
            img, draw = _new_canvas()
            pw.render(draw, OLED_WIDTH, OLED_HEIGHT)
            _assert_clean_outer_margin(self, img, f"WiFiPasswordView vis={vis}")
            px = img.load()
            # Marco unificado de instrumento (2, 6)-(125, 57) con borde inferior limpio
            self.assertNotEqual(px[2, 30], 0, "WiFiPasswordView debe usar marco unificado")
            self.assertNotEqual(px[125, 30], 0, "WiFiPasswordView debe tener borde derecho")
            self.assertNotEqual(px[64, 57], 0, "WiFiPasswordView debe mantener borde inferior limpio")
            # Campo compacto (6, 31)-(121, 48)
            self.assertNotEqual(px[6, 31], 0, "Campo compacto superior ausente")
            self.assertNotEqual(px[6, 48], 0, "Campo compacto inferior ausente")

        pw.is_visible = False
        pw.error_message = "Min. 8 caracteres obligatorios"
        img_err, draw_err = _new_canvas()
        pw.render(draw_err, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_err, "WiFiPasswordView error")
        # El error no debe rellenar el interior del campo compacto con una barra solida
        self.assertEqual(img_err.load()[8, 33], 0, "Error no debe tapar el campo con bloque invertido")

        # Estado sin teclado USB y modo HID
        for kwargs, label in (
            ({"keyboard_status": lambda: False}, "no_keyboard"),
            ({"usb_gadget_active": True}, "usb_hid"),
        ):
            pw_alt = WiFiPasswordView(ssid="Oficina_5G", **kwargs)
            img_alt, draw_alt = _new_canvas()
            pw_alt.render(draw_alt, OLED_WIDTH, OLED_HEIGHT)
            _assert_clean_outer_margin(self, img_alt, f"WiFiPasswordView {label}")

        vk = VirtualKeyboardInputView(ssid="TestWiFi", initial_text="UnaClaveLargaParaProbarScroll123")
        img_vk, draw_vk = _new_canvas()
        vk.render(draw_vk, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_vk, "VirtualKeyboardInputView")

    def test_qr_code_view_and_toggle(self) -> None:
        qr = QRCodeView(
            title="REPORTE MOVIL",
            url="http://192.168.1.50:8000/r/abc12345",
            alt_url="http://10.0.0.1:8000/r/abc12345",
            net_label="WIFI",
            alt_label="USB",
        )
        img, draw = _new_canvas()
        qr.render(draw, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img, "QRCodeView")

        self.assertEqual(qr.handle_input(InputEvent.KEY2).action_type, ViewActionType.NONE)
        self.assertEqual(qr.net_label, "USB")
        self.assertEqual(qr.handle_input(InputEvent.KEY3).action_type, ViewActionType.POP_VIEW)

    def test_screen_manager_differential_rendering_and_error_recovery(self) -> None:
        sm = ScreenManager()
        fake_oled = MagicMock()
        sm._oled_device = fake_oled

        deck = HeroCardDeckView("MAIN")
        deck.add_card(HeroCard("UTILIDADES", "TOOLS"))
        sm.set_root_view(deck)

        # Primer render envía frame; segundo render idéntico NO reenvía por SPI
        sm.render()
        sm.render()
        self.assertEqual(fake_oled.display.call_count, 1)

        # Cambio de vista fuerza nuevo envío
        sm.push_view(NoticeView("AVISO", ["Cambio visible"], level=NoticeLevel.INFO))
        sm.render()
        self.assertEqual(fake_oled.display.call_count, 2)

        # Error en una vista defectuosa no rompe ScreenManager
        class BrokenView(HeroCardDeckView):
            def render(self, draw, width=128, height=64):
                raise RuntimeError("Fallo simulado de render")

        sm.push_view(BrokenView("BROKEN"))
        buf = sm.render()
        self.assertIsNotNone(buf)
        _assert_clean_outer_margin(self, buf, "ScreenManager error fallback")

    def test_battery_telemetry_view_states(self) -> None:
        refreshed = []
        view = BatteryTelemetryView(title="BATERIA", on_refresh=lambda: refreshed.append(True))

        # Fallback sin metricas
        img_empty, draw_empty = _new_canvas()
        view.render(draw_empty, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_empty, "BatteryTelemetryView empty")

        # Estado en carga (con rayo)
        view.set_content(
            lines=["Nivel: 76%"],
            status="OK",
            is_loading=False,
            level=NoticeLevel.OK,
            metrics={
                "percentage": 76,
                "voltage": 4.18,
                "current_ma": 450,
                "power_w": 1.88,
                "is_charging": True,
                "is_power_plugged": True,
            },
        )
        img_chg, draw_chg = _new_canvas()
        view.render(draw_chg, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_chg, "BatteryTelemetryView charging")

        # Estado en descarga normal
        view.set_content(
            lines=["Nivel: 88%"],
            status="OK",
            is_loading=False,
            level=NoticeLevel.OK,
            metrics={
                "percentage": 88,
                "voltage": 4.12,
                "current_ma": -260,
                "power_w": 1.07,
                "is_charging": False,
                "is_power_plugged": False,
            },
        )
        img_bat, draw_bat = _new_canvas()
        view.render(draw_bat, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_bat, "BatteryTelemetryView normal")
        # Cabecera normal NO debe tener franja invertida completa en x=120, y=2
        self.assertEqual(img_bat.load()[120, 2], 0)

        # Bateria baja <= 15% en descarga: mismo marco unificado con icono warn en cabecera
        view.set_content(
            lines=["Nivel: 10%"],
            status="OK",
            is_loading=False,
            level=NoticeLevel.WARNING,
            metrics={
                "percentage": 10,
                "voltage": 3.55,
                "current_ma": -300,
                "power_w": 1.06,
                "is_charging": False,
                "is_power_plugged": False,
            },
        )
        img_low, draw_low = _new_canvas()
        view.render(draw_low, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_low, "BatteryTelemetryView low")
        px_low = img_low.load()
        self.assertNotEqual(px_low[2, 30], 0, "BatteryTelemetryView debe usar el marco unificado")
        self.assertNotEqual(px_low[125, 30], 0, "BatteryTelemetryView debe tener borde derecho")
        self.assertNotEqual(px_low[64, 57], 0, "BatteryTelemetryView debe tener borde inferior limpio")
        self.assertNotEqual(
            img_bat.crop((5, 2, 16, 11)).tobytes(),
            img_low.crop((5, 2, 16, 11)).tobytes(),
            "Bateria baja debe mostrar icono de advertencia en cabecera",
        )

        # Refresh por KEY2 entra en carga
        view.handle_input(InputEvent.KEY2)
        self.assertTrue(view.is_loading)
        self.assertEqual(len(refreshed), 1)
        img_load, draw_load = _new_canvas()
        view.render(draw_load, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_load, "BatteryTelemetryView loading")

    def test_system_telemetry_view_pagination_and_warning(self) -> None:
        view = SystemTelemetryView(title="SISTEMA")
        view.set_content(
            lines=["Temp CPU: 43.5 C"],
            status="OK",
            is_loading=False,
            level=NoticeLevel.OK,
            metrics={
                "temp": 43.5,
                "load": 0.35,
                "cpu_count": 4,
                "ram_used_mb": 142,
                "ram_total_mb": 512,
                "ram_pct": 28,
                "kernel": "6.6.51-v7+",
                "uptime": "2h 15m",
                "hostname": "rei-pi",
            },
        )
        img_p0, draw_p0 = _new_canvas()
        view.render(draw_p0, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_p0, "SystemTelemetryView page 0")
        self.assertEqual(view.page_index, 0)

        self.assertEqual(view.handle_input(InputEvent.RIGHT).action_type, ViewActionType.NONE)
        self.assertEqual(view.page_index, 1)
        img_p1, draw_p1 = _new_canvas()
        view.render(draw_p1, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_p1, "SystemTelemetryView page 1")

        self.assertEqual(view.handle_input(InputEvent.LEFT).action_type, ViewActionType.NONE)
        self.assertEqual(view.page_index, 0)

        # Alerta por temperatura alta
        view.set_content(
            lines=["Temp CPU: 78.2 C"],
            status="OK",
            is_loading=False,
            level=NoticeLevel.WARNING,
            metrics={
                "temp": 78.2,
                "load": 2.4,
                "cpu_count": 4,
                "ram_used_mb": 465,
                "ram_total_mb": 512,
                "ram_pct": 91,
                "kernel": "6.6.51-v7+",
                "uptime": "5h 10m",
                "hostname": "rei-pi",
            },
        )
        img_warn, draw_warn = _new_canvas()
        view.render(draw_warn, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_warn, "SystemTelemetryView warning")

    def test_detail_card_hybrid_p1_p2_p3_pagination_and_geometry(self) -> None:
        # 3 pares KV caben en 1 pagina exacta (Hero + 2 filas tabulares)
        view_single = DetailCardView(
            title="RED ACTIVA",
            initial_lines=[
                "IPv4: 192.168.1.100",
                "Host: rei-pi",
                "Link: wlan0",
            ],
            level=NoticeLevel.OK,
        )
        img1, draw1 = _new_canvas()
        view_single.render(draw1, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img1, "DetailCardView 1 page P1+P2+P3")
        self.assertEqual(view_single.total_pages, 1)
        px1 = img1.load()
        # Marco instrumento P2 (2, 6)-(125, 57)
        self.assertNotEqual(px1[2, 30], 0, "Borde izquierdo de instrumento ausente")
        self.assertNotEqual(px1[125, 30], 0, "Borde derecho de instrumento ausente")
        self.assertNotEqual(px1[3, 6], 0, "Borde superior de instrumento ausente")
        self.assertNotEqual(px1[3, 57], 0, "Borde inferior de instrumento ausente")
        # Unico divisor punteado en y=28, sin bloque invertido en (6,15) ni borde inferior recargado
        self.assertNotEqual(px1[6, 28], 0, "Divisor punteado bajo bloque protagonista ausente")
        self.assertEqual(px1[6, 15], 0, "Propuesta A no debe dibujar badge invertido solido")
        self.assertNotEqual(px1[64, 57], 0, "Borde inferior debe ser continuo y limpio")
        self.assertEqual(view_single.font, get_body_font(), "DetailCardView debe usar la fuente original de 10 px")
        self.assertEqual(view_single.handle_input(InputEvent.DOWN).action_type, ViewActionType.NONE)
        self.assertEqual(view_single.page_index, 0)

        # 5 pares KV se compilan en 2 paginas limpias (Hero+2 filas, luego 2 filas)
        view_multi = DetailCardView(
            title="SWITCH",
            initial_lines=[
                "Puerto: ttyUSB0",
                "Baud: 9600",
                "DSR: ACTIVO",
                "CTS: OK",
                "Prompt: Switch#",
            ],
            level=NoticeLevel.OK,
        )
        self.assertEqual(view_multi.total_pages, 2)
        img_p0, draw_p0 = _new_canvas()
        view_multi.render(draw_p0, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_p0, "DetailCardView multi page 0")

        view_multi.handle_input(InputEvent.RIGHT)
        self.assertEqual(view_multi.page_index, 1)
        self.assertEqual(view_multi.scroll_offset, 1)
        img_p1, draw_p1 = _new_canvas()
        view_multi.render(draw_p1, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_p1, "DetailCardView multi page 1")
        self.assertNotEqual(img_p0.tobytes(), img_p1.tobytes(), "Las paginas 0 y 1 deben diferir visualmente")

        # RIGHT envuelve circularmente a pagina 0; LEFT vuelve a pagina 1
        view_multi.handle_input(InputEvent.RIGHT)
        self.assertEqual(view_multi.page_index, 0)
        view_multi.handle_input(InputEvent.LEFT)
        self.assertEqual(view_multi.page_index, 1)
        view_multi.handle_input(InputEvent.UP)
        self.assertEqual(view_multi.page_index, 0)

    def test_detail_card_structured_pages_signal_bars_and_entities(self) -> None:
        pages = [
            DetailPage(
                title="RED ACTIVA",
                hero=DetailHero(tag="WIFI", value="Oficina_5G", signal_pct=84),
                rows=(
                    DetailRow(label="IP", value="192.168.1.222"),
                    DetailRow(label="INFO", value="84% 5G WPA2"),
                ),
            ),
            DetailPage(
                title="INTERFACES",
                entities=(
                    DetailEntity(title="wlan0", subtitle="192.168.1.222", badge="UP", status_active=True),
                    DetailEntity(title="usb0", subtitle="Sin direccion IPv4", badge="DOWN", status_active=False),
                ),
            ),
        ]
        view = DetailCardView(
            title="RED ACTIVA",
            level=NoticeLevel.OK,
            on_refresh=lambda: None,
            pages=pages,
        )
        self.assertEqual(view.total_pages, 2)

        img0, draw0 = _new_canvas()
        view.render(draw0, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img0, "DetailCardView structured page 0 signal bars")
        # Verificar que las barras de senal se dibujan en el extremo derecho del bloque hero
        signal_crop = img0.crop((109, 14, 122, 24))
        self.assertTrue(any(signal_crop.tobytes()), "Widget de barras de senal no se renderizo")

        view.handle_input(InputEvent.RIGHT)
        img1, draw1 = _new_canvas()
        view.render(draw1, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img1, "DetailCardView structured page 1 entities")
        self.assertNotEqual(img0.tobytes(), img1.tobytes())

    def test_detail_card_levels_and_prose_fallback(self) -> None:
        for level in (NoticeLevel.OK, NoticeLevel.INFO, NoticeLevel.WARNING, NoticeLevel.ERROR):
            v = DetailCardView(
                title="DIAGNOSTICO",
                initial_lines=[
                    "Sincronizando disco del sistema...",
                    "Aplicando estado seguro de energia.",
                    "Operacion completada correctamente.",
                    "Puede retirar el cable de consola.",
                ],
                level=level,
            )
            self.assertGreaterEqual(v.total_pages, 1)
            img, draw = _new_canvas()
            v.render(draw, OLED_WIDTH, OLED_HEIGHT)
            _assert_clean_outer_margin(self, img, f"DetailCardView prose level={level.name}")

    def test_all_detail_plugins_emit_structured_pages_and_render_cleanly(self) -> None:
        prev = os.environ.get("REI_DRY_RUN")
        os.environ["REI_DRY_RUN"] = "1"
        try:
            structured_plugins = [
                IPAddressPlugin(),
                CiscoSerialPlugin(),
                CiscoSSHPlugin(),
                SNMPScanPlugin(),
                WindowsRNDISPlugin(),
                LinuxSSHPlugin(),
                VaultPlugin(),
            ]
            for plugin in structured_plugins:
                res = plugin.run()
                self.assertEqual(res.status, DiagnosticStatus.SUCCESS, f"{plugin.id} fallo")
                self.assertIsInstance(res.metrics, dict, f"{plugin.id} debe devolver dict metrics")
                self.assertIn("detail_pages", res.metrics, f"{plugin.id} debe incluir detail_pages")
                self.assertGreaterEqual(len(res.metrics["detail_pages"]), 1)

                view = DetailCardView(title=plugin.name, on_refresh=lambda: None)
                view.set_content(
                    lines=res.details,
                    status="OK",
                    is_loading=False,
                    level=severity_to_notice_level(res.overall_status),
                    metrics=res.metrics,
                )
                for p_idx in range(view.total_pages):
                    view.page_index = p_idx
                    img, draw = _new_canvas()
                    view.render(draw, OLED_WIDTH, OLED_HEIGHT)
                    _assert_clean_outer_margin(self, img, f"{plugin.id} page={p_idx}")

            # PoweroffPlugin (prosa pura) tambien debe renderizar limpio dentro del marco P2
            p_res = PoweroffPlugin().run()
            p_view = DetailCardView(title="APAGAR")
            p_view.set_content(lines=p_res.details, status="OK", level=NoticeLevel.OK)
            img_p, draw_p = _new_canvas()
            p_view.render(draw_p, OLED_WIDTH, OLED_HEIGHT)
            _assert_clean_outer_margin(self, img_p, "PoweroffPlugin detail view")
        finally:
            if prev is None:
                os.environ.pop("REI_DRY_RUN", None)
            else:
                os.environ["REI_DRY_RUN"] = prev

    def test_notice_view_severity_visual_differentiation(self) -> None:
        v_ok = NoticeView(
            title="CONECTADO",
            lines=["Red: Oficina_5G", "IP: 192.168.1.105", "Senal: 84%"],
            level=NoticeLevel.OK,
        )
        img_ok, draw_ok = _new_canvas()
        v_ok.render(draw_ok, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_ok, "NoticeView OK toast")

        v_warn = NoticeView(
            title="USB NO LISTO",
            lines=["Interfaz HID ausente.", "Active Modo HID."],
            level=NoticeLevel.WARNING,
        )
        img_warn, draw_warn = _new_canvas()
        v_warn.render(draw_warn, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_warn, "NoticeView WARNING")

        v_info = NoticeView(
            title="SIN REDES",
            lines=["No se encontraron redes."],
            level=NoticeLevel.INFO,
        )
        img_info, draw_info = _new_canvas()
        v_info.render(draw_info, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_info, "NoticeView INFO")

        v_err = NoticeView(
            title="SIN CONEXION",
            lines=["No se pudo conectar."],
            level=NoticeLevel.ERROR,
        )
        img_err, draw_err = _new_canvas()
        v_err.render(draw_err, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_err, "NoticeView ERROR")

        # Todas las severidades comparten marco W3 y solo cambian el micro-icono superior
        icon_crops = []
        for img, label in (
            (img_ok, "OK"),
            (img_info, "INFO"),
            (img_warn, "WARNING"),
            (img_err, "ERROR"),
        ):
            px = img.load()
            self.assertNotEqual(px[2, 30], 0, f"{label}: borde izquierdo W3 ausente")
            self.assertNotEqual(px[125, 30], 0, f"{label}: borde derecho W3 ausente")
            self.assertNotEqual(px[3, 6], 0, f"{label}: borde superior W3 ausente")
            self.assertNotEqual(px[3, 57], 0, f"{label}: borde inferior W3 ausente")
            self.assertEqual(px[120, 2], 0, f"{label}: no debe tener cabecera invertida")
            icon_crops.append(img.crop((6, 3, 14, 10)).tobytes())
        self.assertEqual(len(set(icon_crops)), 4, "Cada severidad debe tener un micro-icono distinto")

    def test_notice_view_w3_unified_warning_and_ok_format(self) -> None:
        v_warn = NoticeView(
            title="USB NO LISTO",
            lines=[
                "Interfaz USB HID no disponible.",
                "Active Modo HID en Utilidades > USB.",
            ],
            level=NoticeLevel.WARNING,
        )
        v_ok = NoticeView(
            title="CONECTADO",
            lines=["Red: Oficina_5G", "IP: 192.168.1.105"],
            level=NoticeLevel.OK,
        )

        img_w, draw_w = _new_canvas()
        v_warn.render(draw_w, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_w, "NoticeView WARNING W3")

        img_ok, draw_ok = _new_canvas()
        v_ok.render(draw_ok, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_ok, "NoticeView OK W3")

        # UpdateProgressView finalizado (exito y error) tambien usa marco W3
        for ok_flag in (True, False):
            up = UpdateProgressView(title="ACTUALIZAR SISTEMA")
            up.set_completed(success=ok_flag, summary="Resumen", details=["Detalle de prueba"])
            img_up, draw_up = _new_canvas()
            up.render(draw_up, OLED_WIDTH, OLED_HEIGHT)
            _assert_clean_outer_margin(self, img_up, f"UpdateProgressView W3 ok={ok_flag}")
            px_up = img_up.load()
            self.assertNotEqual(px_up[2, 30], 0)
            self.assertNotEqual(px_up[125, 30], 0)
            self.assertNotEqual(px_up[3, 6], 0)
            self.assertNotEqual(px_up[3, 57], 0)

        # Advertencia larga con mas de 3 lineas permite scroll
        v_warn.handle_input(InputEvent.DOWN)
        self.assertGreater(v_warn.scroll_offset, 0)

    def test_confirm_dialog_c1_safe_default_focus_and_navigation(self) -> None:
        confirmed = []
        dlg = ConfirmDialogView(
            title="APAGAR",
            question="APAGAR EQUIPO?",
            icon_name="POWEROFF",
            cancel_label="NO",
            confirm_label="APAGAR",
            on_confirm=lambda: confirmed.append(True),
            default_confirm=False,
        )
        self.assertFalse(dlg.selected_confirm)

        img_no, draw_no = _new_canvas()
        dlg.render(draw_no, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_no, "ConfirmDialogView NO focused")
        # Foco en NO -> interior del boton izquierdo invertido (x=10, y=46 encendido)
        self.assertNotEqual(img_no.load()[10, 46], 0)
        self.assertEqual(img_no.load()[80, 46], 0)

        # ENTER por defecto cancela sin confirmar
        self.assertEqual(dlg.handle_input(InputEvent.ENTER).action_type, ViewActionType.POP_VIEW)
        self.assertEqual(len(confirmed), 0)

        # RIGHT mueve foco a confirmar
        self.assertEqual(dlg.handle_input(InputEvent.RIGHT).action_type, ViewActionType.NONE)
        self.assertTrue(dlg.selected_confirm)

        img_yes, draw_yes = _new_canvas()
        dlg.render(draw_yes, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_yes, "ConfirmDialogView confirm focused")
        self.assertEqual(img_yes.load()[10, 46], 0)
        self.assertNotEqual(img_yes.load()[80, 46], 0)

        # KEY3 siempre cancela aunque este seleccionado confirmar
        self.assertEqual(dlg.handle_input(InputEvent.KEY3).action_type, ViewActionType.POP_VIEW)
        self.assertEqual(len(confirmed), 0)

        # LEFT vuelve a NO; RIGHT + ENTER dispara on_confirm
        dlg.handle_input(InputEvent.LEFT)
        self.assertFalse(dlg.selected_confirm)
        dlg.handle_input(InputEvent.RIGHT)
        self.assertEqual(dlg.handle_input(InputEvent.ENTER).action_type, ViewActionType.NONE)
        self.assertEqual(len(confirmed), 1)

        # Verificar tamano y margenes de REINICIAR
        dlg_reb = ConfirmDialogView(
            title="REINICIAR",
            question="REINICIAR EQUIPO?",
            icon_name="REBOOT",
            cancel_label="NO",
            confirm_label="REINICIAR",
        )
        img_reb, draw_reb = _new_canvas()
        dlg_reb.render(draw_reb, OLED_WIDTH, OLED_HEIGHT)
        _assert_clean_outer_margin(self, img_reb, "ConfirmDialogView REINICIAR")


class TestTelemetryPluginsMetrics(unittest.TestCase):
    """Valida que los plugins de bateria y sistema exponen metricas estructuradas."""

    def test_battery_and_system_plugins_return_structured_metrics(self) -> None:
        bat_res = BatteryStatusPlugin().run()
        self.assertEqual(bat_res.status, DiagnosticStatus.SUCCESS)
        self.assertIsInstance(bat_res.metrics, dict)
        for key in ("percentage", "voltage", "current_ma", "power_w", "is_charging", "is_power_plugged"):
            self.assertIn(key, bat_res.metrics)

        sys_res = SystemStatusPlugin().run()
        self.assertEqual(sys_res.status, DiagnosticStatus.SUCCESS)
        self.assertIsInstance(sys_res.metrics, dict)
        for key in ("temp", "load", "cpu_count", "ram_used_mb", "ram_total_mb", "ram_pct", "kernel", "uptime", "hostname"):
            self.assertIn(key, sys_res.metrics)


class TestREIAppDryRunSmoke(unittest.TestCase):
    """Smoke test de REIApp completo en modo REI_DRY_RUN=1."""

    def test_app_initializes_and_renders_root_menu(self) -> None:
        prev = os.environ.get("REI_DRY_RUN")
        os.environ["REI_DRY_RUN"] = "1"
        try:
            from main import REIApp

            app = REIApp()
            try:
                buf = app.screen_manager.render()
                self.assertEqual(buf.size, (OLED_WIDTH, OLED_HEIGHT))
                _assert_clean_outer_margin(self, buf, "REIApp root menu")

                self.assertIsInstance(app._detail_views["diag_battery"], BatteryTelemetryView)
                self.assertIsInstance(app._detail_views["diag_system"], SystemTelemetryView)

                # Verificar que APAGAR pide confirmacion (NoticeView) antes de ejecutar
                root_deck = app.screen_manager.current_view
                util_deck = root_deck.cards[0].submenu
                power_card = next(c for c in util_deck.cards if c.title == "ALIMENTACION")
                power_deck = power_card.submenu
                apagar_card = next(c for c in power_deck.cards if c.title == "APAGAR")
                self.assertIsNone(apagar_card.submenu)
                self.assertIsNotNone(apagar_card.on_select)
                apagar_card.on_select()
                self.assertIsInstance(app.screen_manager.current_view, ConfirmDialogView)
                self.assertFalse(app.screen_manager.current_view.selected_confirm)
            finally:
                app.shutdown()
        finally:
            if prev is None:
                os.environ.pop("REI_DRY_RUN", None)
            else:
                os.environ["REI_DRY_RUN"] = prev


if __name__ == "__main__":
    unittest.main()
