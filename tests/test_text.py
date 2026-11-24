"""
Tests for ui/text.py: ASCII transliteration for the OLED font and word wrap.

Pillow's default font (Aileron) has no glyphs outside ASCII, so every string
drawn on the 128x64 panel must come out of to_oled() as printable ASCII.
"""

import unittest

from ui.text import to_oled, wrap_text


class TestToOled(unittest.TestCase):

    def test_accents_lose_diacritics(self):
        self.assertEqual(to_oled("Contraseña"), "Contrasena")
        self.assertEqual(to_oled("Conexión"), "Conexion")
        self.assertEqual(to_oled("Señal"), "Senal")
        self.assertEqual(to_oled("ÁÉÍÓÚÜ"), "AEIOUU")

    def test_known_symbols_map_to_ascii(self):
        self.assertEqual(to_oled("[✓] COMPLETADO"), "[OK] COMPLETADO")
        self.assertEqual(to_oled("[✗] FALLO"), "[X] FALLO")
        self.assertEqual(to_oled("88°C"), "88oC")
        self.assertEqual(to_oled("¿Que?"), "?Que?")
        self.assertEqual(to_oled("a—b"), "a-b")
        self.assertEqual(to_oled("a…b"), "a...b")

    def test_unknown_glyphs_become_question_mark(self):
        result = to_oled("a\U0001f600b")
        self.assertTrue(result.isascii())
        self.assertIn("?", result)

    def test_output_is_always_printable_ascii(self):
        samples = [
            "Red Español Ñ",
            "Temperatura 92°C",
            "conexión/ señal ✗",
            None,
            "",
            "already ascii",
        ]
        for sample in samples:
            result = to_oled(sample)
            self.assertTrue(result.isascii(), repr(result))
            self.assertTrue(result.isprintable() or result == "", repr(result))

    def test_whitespace_collapses(self):
        self.assertEqual(to_oled("a\tb\nc   d"), "a b c d")

    def test_max_len_truncates(self):
        self.assertEqual(to_oled("Contrasena larga", max_len=8), "Contrase")

    def test_numbers_kept_as_integers(self):
        self.assertEqual(to_oled(42), "42")
        self.assertEqual(to_oled(42.0), "42")


class TestWrapText(unittest.TestCase):

    def test_wraps_at_char_limit(self):
        lines = wrap_text("Una frase bastante larga para una linea", max_chars=20)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(len(line), 20)

    def test_respects_word_boundaries(self):
        lines = wrap_text("CLAVE INCORRECTA RED", max_chars=18)
        self.assertEqual(lines[0], "CLAVE INCORRECTA")
        self.assertEqual(lines[1], "RED")

    def test_hard_splits_overlong_words(self):
        lines = wrap_text("M" * 45, max_chars=20)
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "M" * 20)
        self.assertEqual(lines[1], "M" * 20)
        self.assertEqual(lines[2], "M" * 5)

    def test_max_lines_limits_output(self):
        lines = wrap_text("palabra " * 20, max_chars=20, max_lines=3)
        self.assertEqual(len(lines), 3)

    def test_empty_input(self):
        self.assertEqual(wrap_text(""), [])

    def test_output_is_ascii(self):
        lines = wrap_text("Contraseña incorrecta en la red", max_chars=20)
        self.assertTrue(all(line.isascii() for line in lines))


if __name__ == "__main__":
    unittest.main()
