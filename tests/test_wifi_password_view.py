"""
Pruebas para WiFiPasswordView:
- Reemplazo del teclado virtual con mensaje de teclado USB externo
- Visualizar u ocultar contraseña (KEY2 / Tab)
- Validación temprana WPA/WEP sin bloqueos
- Renderizado 128x64 sin desbordamiento de píxeles
"""

import sys
import types
import unittest
from unittest.mock import MagicMock

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    pil_mod = types.ModuleType("PIL")
    img_mod = types.ModuleType("PIL.Image")
    draw_mod = types.ModuleType("PIL.ImageDraw")
    font_mod = types.ModuleType("PIL.ImageFont")
    img_mod.Image = MagicMock
    img_mod.new = MagicMock(return_value=MagicMock())
    draw_mod.ImageDraw = MagicMock
    draw_mod.Draw = MagicMock(return_value=MagicMock())
    font_mod.ImageFont = MagicMock
    font_mod.load_default = MagicMock(return_value=MagicMock())
    pil_mod.Image = img_mod
    pil_mod.ImageDraw = draw_mod
    pil_mod.ImageFont = font_mod
    for _name, _mod in (
        ("PIL", pil_mod),
        ("PIL.Image", img_mod),
        ("PIL.ImageDraw", draw_mod),
        ("PIL.ImageFont", font_mod),
    ):
        sys.modules.setdefault(_name, _mod)

from ui.display import ViewActionType
from ui.input_handler import InputEvent
from ui.wifi_password_view import WiFiPasswordView


class _RecordingDraw:
    """Canvas ligero para validar textos dibujados sin requerir Pillow."""

    def __init__(self):
        self.texts = []
        self.rects = []
        self.lines = []
        self.ellipses = []

    def textbbox(self, xy, text, font=None):
        return (0, 0, len(text) * 7, 8)

    def rectangle(self, coords, outline=None, fill=None):
        self.rects.append((coords, outline, fill))

    def ellipse(self, coords, outline=None, fill=None):
        self.ellipses.append((coords, outline, fill))

    def line(self, coords, fill=None):
        self.lines.append((coords, fill))

    def text(self, xy, text, font=None, fill=None):
        self.texts.append((xy[0], xy[1], text, fill))


class TestWiFiPasswordView(unittest.TestCase):

    def test_typing_visibility_toggle_and_submit(self):
        submitted = []
        view = WiFiPasswordView(
            ssid="Oficina_5G",
            security="WPA2-PSK",
            keyboard_status=lambda: True,
            layout_provider=lambda: "latam",
            on_submit=lambda s, p: submitted.append((s, p)),
        )

        # Oculta por defecto
        self.assertTrue(view.is_masked)
        self.assertFalse(view.is_visible)

        # Escribir incluyendo espacio legitimo y simbolos
        for ch in "Mi Clave@2026":
            view.handle_input(InputEvent.CHAR, char=ch)
        self.assertEqual(view.password, "Mi Clave@2026")

        # Backspace borra ultimo caracter
        view.handle_input(InputEvent.BACKSPACE)
        self.assertEqual(view.password, "Mi Clave@202")

        # KEY2 alterna mostrar / ocultar
        view.handle_input(InputEvent.KEY2)
        self.assertTrue(view.is_visible)
        self.assertFalse(view.is_masked)
        view.handle_input(InputEvent.KEY2)
        self.assertFalse(view.is_visible)

        # ENTER valida y envia exactamente lo escrito (incluyendo espacio)
        action = view.handle_input(InputEvent.ENTER)
        self.assertEqual(action.action_type, ViewActionType.NONE)
        self.assertEqual(submitted, [("Oficina_5G", "Mi Clave@202")])

        # Guardia contra doble envio
        view.handle_input(InputEvent.ENTER)
        self.assertEqual(len(submitted), 1)

    def test_validation_rejects_empty_and_short_wpa_inline(self):
        submitted = []
        view = WiFiPasswordView(
            ssid="Casa_WPA2",
            security="WPA2-PSK",
            on_submit=lambda s, p: submitted.append((s, p)),
        )

        # Vacia
        view.handle_input(InputEvent.ENTER)
        self.assertEqual(submitted, [])
        self.assertEqual(view.error_message, "Ingrese una clave")

        # Menor a 8 caracteres en WPA2
        for ch in "1234":
            view.handle_input(InputEvent.CHAR, char=ch)
        self.assertEqual(view.error_message, "")
        view.handle_input(InputEvent.KEY1)
        self.assertEqual(submitted, [])
        self.assertEqual(view.error_message, "Min. 8 caracteres")

        # Completar 8+ caracteres y enviar
        for ch in "5678":
            view.handle_input(InputEvent.CHAR, char=ch)
        view.handle_input(InputEvent.PRESS)
        self.assertEqual(submitted, [("Casa_WPA2", "12345678")])

    def test_cancel_with_key3_or_escape(self):
        view = WiFiPasswordView(ssid="Red")
        self.assertEqual(view.handle_input(InputEvent.ESCAPE).action_type, ViewActionType.POP_VIEW)
        self.assertEqual(view.handle_input(InputEvent.KEY3).action_type, ViewActionType.POP_VIEW)

    def test_renders_prompt_when_no_keyboard_and_preserves_password_on_unplug(self):
        connected = [False]
        view = WiFiPasswordView(
            ssid="MiRed",
            keyboard_status=lambda: connected[0],
            layout_provider=lambda: "latam",
        )
        view.update()
        draw = _RecordingDraw()
        view.render(draw, 128, 64)
        rendered_texts = [t[2] for t in draw.texts]
        # Texto guía dentro del cuadro + badge de layout + hints compactos
        # (substring: el recorte defensivo depende de las métricas de la fuente)
        self.assertTrue(any("Conecte" in t for t in rendered_texts))
        self.assertIn("LA", rendered_texts)
        self.assertIn("K2", rendered_texts)
        self.assertIn("ENT", rendered_texts)
        self.assertIn("K3", rendered_texts)
        # Punto de estado hueco (teclado ausente): outline sin fill
        hollow_dots = [r for r in draw.rects if r[0] == (117, 6, 121, 10) and r[2] is None]
        self.assertTrue(hollow_dots)
        # Los pills antiguos ya no se dibujan
        self.assertNotIn("SIN KB", rendered_texts)
        self.assertNotIn("KB OK", rendered_texts)

        # Al conectar teclado y escribir, luego desconectar no pierde la clave escrita
        connected[0] = True
        view.update()
        for ch in "secreto99":
            view.handle_input(InputEvent.CHAR, char=ch)
        draw2 = _RecordingDraw()
        view.render(draw2, 128, 64)
        rendered2 = [t[2] for t in draw2.texts]
        # Punto de estado sólido (teclado conectado)
        solid_dots = [r for r in draw2.rects if r[0] == (117, 6, 121, 10) and r[2] == "white"]
        self.assertTrue(solid_dots)
        # Privacidad: oculta, la clave NUNCA aparece como texto dibujado
        self.assertFalse(any("secreto99" in t for t in rendered2))
        # Se dibujan puntos enmascarados y el contador de caracteres
        self.assertTrue(draw2.ellipses)
        self.assertIn("09/63", rendered2)

        # Cambiar a visible muestra texto claro y pupila hueca del ojo
        view.handle_input(InputEvent.KEY2)
        draw3 = _RecordingDraw()
        view.render(draw3, 128, 64)
        rendered3 = [t[2] for t in draw3.texts]
        self.assertTrue(any("secreto99" in t for t in rendered3))
        self.assertIn("09/63", rendered3)
        hollow_pupil = [e for e in draw3.ellipses if e[1] == "white" and e[2] is None]
        self.assertTrue(hollow_pupil)

        # Desconectar teclado en mitad de escritura mantiene password en memoria
        connected[0] = False
        view.update()
        self.assertEqual(view.password, "secreto99")

    def test_error_rendered_inverted_inside_box(self):
        view = WiFiPasswordView(ssid="Casa", security="WPA2-PSK")
        view.handle_input(InputEvent.ENTER)  # clave vacia -> "Ingrese una clave"
        draw = _RecordingDraw()
        view.render(draw, 128, 64)
        rendered = [t[2] for t in draw.texts]
        self.assertIn("Ingrese una clave", rendered)
        # Barra invertida: rectangulo blanco de fondo del cuadro con texto negro
        inverted = [r for r in draw.rects if r[2] == "white"]
        self.assertTrue(inverted)
        self.assertTrue(any(t[3] == "black" for t in draw.texts))
        # Sin puntos de máscara rellenos cuando hay error de clave vacia
        # (el glyph del ojo del pie es outline, no punto relleno)
        self.assertFalse(any(e[2] == "white" for e in draw.ellipses))

    def test_renders_usb_gadget_warning_when_hid_mode_active(self):
        view = WiFiPasswordView(
            ssid="MiRed",
            keyboard_status=lambda: False,
            usb_gadget_active=True,
        )
        view.update()
        draw = _RecordingDraw()
        view.render(draw, 128, 64)
        rendered = [t[2] for t in draw.texts]
        # (substring: el recorte defensivo depende de las métricas de la fuente)
        self.assertTrue(any("USB en modo" in t for t in rendered))

    @unittest.skipUnless(HAS_PIL, "Requiere Pillow para medir ancho real de pixeles")
    def test_real_pil_render_long_password_does_not_overflow_box(self):
        view = WiFiPasswordView(
            ssid="SSID_MUY_LARGO_CORPORATIVO",
            security="WPA2-PSK",
            keyboard_status=lambda: True,
            layout_provider=lambda: "latam",
        )
        view.set_text("W" * 63)

        buf = Image.new("1", (128, 64), "black")
        draw = ImageDraw.Draw(buf)

        # Medir que tanto oculta ('*' = 7px) como visible ('W') caben dentro de la caja (x=5..122)
        view.render(draw, 128, 64)
        view.handle_input(InputEvent.KEY2)
        view.render(draw, 128, 64)

        tail, scrolled = view._fit_tail_window(draw, "*" * 63, "_", max_px=111)
        self.assertTrue(scrolled)
        self.assertLessEqual(view.get_text_width(draw, tail + "_"), 103)


if __name__ == "__main__":
    unittest.main()

