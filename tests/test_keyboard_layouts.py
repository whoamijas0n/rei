"""
Pruebas para resolución de distribuciones de teclado USB (LATAM/ES/US),
composición de teclas muertas y detección estricta de teclados reales evdev.
"""

import os
import tempfile
import types
import unittest
from unittest.mock import patch

from ui.keyboard_layouts import (
    build_key_map,
    compose_dead_key,
    detect_system_layout,
    get_layout_badge,
    is_real_keyboard_capabilities,
    next_layout_name,
    normalize_layout_name,
    resolve_key_binding,
)


def make_fake_ecodes():
    """Construye un módulo ecodes mínimo compatible con Linux input-event-codes."""
    ns = types.SimpleNamespace(EV_KEY=1, EV_REL=2)
    codes = {
        "KEY_ESC": 1, "KEY_1": 2, "KEY_2": 3, "KEY_3": 4, "KEY_4": 5, "KEY_5": 6,
        "KEY_6": 7, "KEY_7": 8, "KEY_8": 9, "KEY_9": 10, "KEY_0": 11,
        "KEY_MINUS": 12, "KEY_EQUAL": 13, "KEY_BACKSPACE": 14, "KEY_TAB": 15,
        "KEY_Q": 16, "KEY_W": 17, "KEY_E": 18, "KEY_R": 19, "KEY_T": 20,
        "KEY_Y": 21, "KEY_U": 22, "KEY_I": 23, "KEY_O": 24, "KEY_P": 25,
        "KEY_LEFTBRACE": 26, "KEY_RIGHTBRACE": 27, "KEY_ENTER": 28, "KEY_LEFTCTRL": 29,
        "KEY_A": 30, "KEY_S": 31, "KEY_D": 32, "KEY_F": 33, "KEY_G": 34,
        "KEY_H": 35, "KEY_J": 36, "KEY_K": 37, "KEY_L": 38, "KEY_SEMICOLON": 39,
        "KEY_APOSTROPHE": 40, "KEY_GRAVE": 41, "KEY_LEFTSHIFT": 42, "KEY_BACKSLASH": 43,
        "KEY_Z": 44, "KEY_X": 45, "KEY_C": 46, "KEY_V": 47, "KEY_B": 48,
        "KEY_N": 49, "KEY_M": 50, "KEY_COMMA": 51, "KEY_DOT": 52, "KEY_SLASH": 53,
        "KEY_RIGHTSHIFT": 54, "KEY_KPASTERISK": 55, "KEY_LEFTALT": 56, "KEY_SPACE": 57,
        "KEY_CAPSLOCK": 58, "KEY_F1": 59, "KEY_F2": 60, "KEY_F3": 61, "KEY_F4": 62,
        "KEY_KP7": 71, "KEY_KP8": 72, "KEY_KP9": 73, "KEY_KPMINUS": 74,
        "KEY_KP4": 75, "KEY_KP5": 76, "KEY_KP6": 77, "KEY_KPPLUS": 78,
        "KEY_KP1": 79, "KEY_KP2": 80, "KEY_KP3": 81, "KEY_KP0": 82, "KEY_KPDOT": 83,
        "KEY_102ND": 86, "KEY_KPENTER": 96, "KEY_RIGHTCTRL": 97, "KEY_KPSLASH": 98,
        "KEY_RIGHTALT": 100, "KEY_UP": 103, "KEY_LEFT": 105, "KEY_RIGHT": 106,
        "KEY_DOWN": 108, "KEY_DELETE": 111,
    }
    for k, v in codes.items():
        setattr(ns, k, v)
    return ns


class TestKeyboardLayouts(unittest.TestCase):

    def setUp(self):
        self.ecodes = make_fake_ecodes()

    def test_layout_normalization_and_cycling(self):
        self.assertEqual(normalize_layout_name("latam,us"), "latam")
        self.assertEqual(normalize_layout_name("es-ES"), "es")
        self.assertEqual(normalize_layout_name("en_US"), "us")
        self.assertEqual(get_layout_badge("latam"), "LA")
        self.assertEqual(get_layout_badge("es"), "ES")
        self.assertEqual(get_layout_badge("us"), "US")
        self.assertEqual(next_layout_name("latam"), "es")
        self.assertEqual(next_layout_name("es"), "us")
        self.assertEqual(next_layout_name("us"), "latam")

    def test_detect_system_layout_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            kbd_file = os.path.join(tmp, "keyboard")
            vcon_file = os.path.join(tmp, "vconsole.conf")
            with open(kbd_file, "w", encoding="utf-8") as fh:
                fh.write('XKBMODEL="pc105"\nXKBLAYOUT="latam"\n')
            with open(vcon_file, "w", encoding="utf-8") as fh:
                fh.write("KEYMAP=es\n")

            with patch.dict(os.environ, {"REI_KEYBOARD_LAYOUT": ""}, clear=False):
                self.assertEqual(
                    detect_system_layout(default_keyboard_path=kbd_file, vconsole_path=vcon_file),
                    "latam",
                )
                self.assertEqual(
                    detect_system_layout(
                        configured_layout="es",
                        default_keyboard_path=kbd_file,
                        vconsole_path=vcon_file,
                    ),
                    "es",
                )
            with patch.dict(os.environ, {"REI_KEYBOARD_LAYOUT": "us"}, clear=False):
                self.assertEqual(
                    detect_system_layout(default_keyboard_path=kbd_file, vconsole_path=vcon_file),
                    "us",
                )

    def test_latam_critical_wifi_symbols_and_altgr(self):
        kmap = build_key_map("latam", ecodes_mod=self.ecodes)

        # AltGr+Q y AltGr+2 producen '@' en LATAM
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_Q], altgr=True)
        self.assertEqual(ch, "@")
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_2], altgr=True)
        self.assertEqual(ch, "@")

        # Shift+2 = '"', KEY_SLASH = '-'/'_', KEY_MINUS = "'"/'?'/'\'
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_2], shift=True)
        self.assertEqual(ch, '"')
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_SLASH], shift=False)
        self.assertEqual(ch, "-")
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_SLASH], shift=True)
        self.assertEqual(ch, "_")
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_MINUS], altgr=True)
        self.assertEqual(ch, "\\")

        # Llaves/corchetes y 102ND
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_APOSTROPHE], shift=False)
        self.assertEqual(ch, "{")
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_APOSTROPHE], shift=True)
        self.assertEqual(ch, "[")
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_BACKSLASH], shift=False)
        self.assertEqual(ch, "}")
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_102ND], shift=True)
        self.assertEqual(ch, ">")

        # CapsLock solo debe afectar letras, NO digitos ni simbolos
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_A], caps_lock=True)
        self.assertEqual(ch, "A")
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_2], caps_lock=True)
        self.assertEqual(ch, "2")
        ch, _ = resolve_key_binding(kmap.bindings[self.ecodes.KEY_SLASH], caps_lock=True)
        self.assertEqual(ch, "-")

    def test_es_and_us_layouts(self):
        es_map = build_key_map("es", ecodes_mod=self.ecodes)
        ch, _ = resolve_key_binding(es_map.bindings[self.ecodes.KEY_Q], altgr=True)
        self.assertEqual(ch, "@")
        ch, _ = resolve_key_binding(es_map.bindings[self.ecodes.KEY_102ND], altgr=True)
        self.assertEqual(ch, "|")
        ch, _ = resolve_key_binding(es_map.bindings[self.ecodes.KEY_RIGHTBRACE], altgr=True)
        self.assertEqual(ch, "]")

        us_map = build_key_map("us", ecodes_mod=self.ecodes)
        ch, _ = resolve_key_binding(us_map.bindings[self.ecodes.KEY_2], shift=True)
        self.assertEqual(ch, "@")
        ch, _ = resolve_key_binding(us_map.bindings[self.ecodes.KEY_MINUS], shift=True)
        self.assertEqual(ch, "_")

    def test_dead_key_composition_and_literal_passwords(self):
        es_map = build_key_map("es", ecodes_mod=self.ecodes)
        # En ES: KEY_LEFTBRACE + Shift = dead_circumflex ('^')
        ch, dead = resolve_key_binding(es_map.bindings[self.ecodes.KEY_LEFTBRACE], shift=True)
        self.assertEqual(ch, "")
        self.assertEqual(dead, "dead_circumflex")

        # Seguido de espacio -> '^' literal
        ch, dead = resolve_key_binding(es_map.bindings[self.ecodes.KEY_SPACE], pending_dead=dead)
        self.assertEqual(ch, "^")
        self.assertIsNone(dead)

        # Seguido de vocal -> compuesto ('á'), seguido de consonante -> '´p'
        self.assertEqual(compose_dead_key("dead_acute", "a"), "á")
        self.assertEqual(compose_dead_key("dead_circumflex", "p"), "^p")

    def test_real_keyboard_filter_rejects_vc4_hdmi_and_consumer_control(self):
        # 1. vc4-hdmi real de Pi Zero 2 W: solo KEY_ENTER
        self.assertFalse(
            is_real_keyboard_capabilities(
                {self.ecodes.EV_KEY: [self.ecodes.KEY_ENTER], self.ecodes.EV_REL: [0, 1]},
                device_name="vc4-hdmi",
                ecodes_mod=self.ecodes,
            )
        )
        # 2. Consumer Control del receptor 2.4G: tiene KEY_ENTER y KEY_ESC pero no alfabeto
        self.assertFalse(
            is_real_keyboard_capabilities(
                {self.ecodes.EV_KEY: [self.ecodes.KEY_ENTER, self.ecodes.KEY_ESC]},
                device_name="BJX 2.4G Receiver Consumer Control",
                ecodes_mod=self.ecodes,
            )
        )
        # 3. Teclado fisico real: tiene A..Z + ENTER + SPACE + BACKSPACE + LEFTSHIFT
        full_keys = [getattr(self.ecodes, f"KEY_{c}") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"] + [
            self.ecodes.KEY_ENTER,
            self.ecodes.KEY_SPACE,
            self.ecodes.KEY_BACKSPACE,
            self.ecodes.KEY_LEFTSHIFT,
        ]
        self.assertTrue(
            is_real_keyboard_capabilities(
                {self.ecodes.EV_KEY: full_keys},
                device_name="BJX 2.4G Receiver",
                ecodes_mod=self.ecodes,
            )
        )


if __name__ == "__main__":
    unittest.main()

