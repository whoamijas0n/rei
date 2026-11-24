"""
Tests for keyboard layout resolution and dead-key composition
(ui/keyboard_layouts.py).

These tests do not require python-evdev: the binding tables are pure data and
the capability classifier is exercised with a fake ``evdev.ecodes`` module.
"""

import os
import sys
import types
import unittest
from unittest.mock import patch

from ui import keyboard_layouts as kl


class TestLayoutNames(unittest.TestCase):

    def test_normalize_known_aliases(self):
        self.assertEqual(kl.normalize_layout_name("ES"), "es")
        self.assertEqual(kl.normalize_layout_name("es-ES"), "es")
        self.assertEqual(kl.normalize_layout_name("spanish"), "es")
        self.assertEqual(kl.normalize_layout_name("latam"), "latam")
        self.assertEqual(kl.normalize_layout_name("es-419"), "latam")
        self.assertEqual(kl.normalize_layout_name("en-US"), "us")

    def test_normalize_unknown_falls_back_to_us(self):
        self.assertEqual(kl.normalize_layout_name("dvorak"), "us")
        self.assertEqual(kl.normalize_layout_name(None), "us")
        self.assertEqual(kl.normalize_layout_name(""), "us")

    def test_normalize_uses_first_group(self):
        self.assertEqual(kl.normalize_layout_name("latam,us"), "latam")


class TestSystemLayoutDetection(unittest.TestCase):

    def setUp(self):
        self.tmpdir = os.path.join("/tmp/opencode", "rei-layout-tests")
        os.makedirs(self.tmpdir, exist_ok=True)

    def _write(self, name: str, content: str) -> str:
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_env_override_wins(self):
        keyboard = self._write("keyboard-env", 'XKBLAYOUT="latam"\n')
        with patch.dict(os.environ, {"REI_KEYBOARD_LAYOUT": "es"}):
            self.assertEqual(
                kl.detect_system_layout(default_keyboard_path=keyboard),
                "es",
            )

    def test_auto_env_is_ignored(self):
        keyboard = self._write("keyboard-auto", 'XKBLAYOUT="latam"\n')
        with patch.dict(os.environ, {"REI_KEYBOARD_LAYOUT": "auto"}):
            self.assertEqual(
                kl.detect_system_layout(default_keyboard_path=keyboard),
                "latam",
            )

    def test_reads_xkb_layout(self):
        keyboard = self._write("keyboard-xkb", 'XKBLAYOUT="es"\n')
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REI_KEYBOARD_LAYOUT", None)
            self.assertEqual(
                kl.detect_system_layout(default_keyboard_path=keyboard),
                "es",
            )

    def test_reads_vconsole_fallback(self):
        missing = os.path.join(self.tmpdir, "does-not-exist")
        vconsole = self._write("vconsole", "KEYMAP=latam\n")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REI_KEYBOARD_LAYOUT", None)
            self.assertEqual(
                kl.detect_system_layout(
                    default_keyboard_path=missing,
                    vconsole_path=vconsole,
                ),
                "latam",
            )

    def test_defaults_to_us(self):
        missing = os.path.join(self.tmpdir, "does-not-exist")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REI_KEYBOARD_LAYOUT", None)
            self.assertEqual(
                kl.detect_system_layout(
                    default_keyboard_path=missing,
                    vconsole_path=missing,
                ),
                "us",
            )


class TestDeadKeys(unittest.TestCase):

    def test_acute_composes(self):
        self.assertEqual(kl.compose_dead_key("dead_acute", "e"), "é")
        self.assertEqual(kl.compose_dead_key("dead_acute", "E"), "É")

    def test_tilde_composes_ntilde(self):
        self.assertEqual(kl.compose_dead_key("dead_tilde", "n"), "ñ")
        self.assertEqual(kl.compose_dead_key("dead_tilde", "N"), "Ñ")

    def test_diaeresis_composes(self):
        self.assertEqual(kl.compose_dead_key("dead_diaeresis", "u"), "ü")

    def test_dead_key_plus_space_is_literal(self):
        self.assertEqual(kl.compose_dead_key("dead_acute", " "), "´")
        self.assertEqual(kl.compose_dead_key("dead_tilde", " "), "~")

    def test_unknown_dead_key_returns_literal(self):
        self.assertEqual(kl.compose_dead_key("dead_unknown", "a"), "a")


class TestBindingTables(unittest.TestCase):

    def test_us_symbols(self):
        table = kl._layout_bindings("us")
        self.assertEqual(table["KEY_2"].normal, "2")
        self.assertEqual(table["KEY_2"].shift, "@")
        self.assertEqual(table["KEY_MINUS"].normal, "-")
        self.assertEqual(table["KEY_MINUS"].shift, "_")

    def test_es_symbols_and_dead_keys(self):
        table = kl._layout_bindings("es")
        self.assertEqual(table["KEY_2"].normal, "2")
        self.assertEqual(table["KEY_2"].shift, '"')
        self.assertEqual(table["KEY_2"].altgr, "@")
        self.assertEqual(table["KEY_SLASH"].normal, "-")
        self.assertEqual(table["KEY_SEMICOLON"].normal, "ñ")
        self.assertEqual(table["KEY_APOSTROPHE"].normal_dead, "dead_acute")
        self.assertEqual(table["KEY_LEFTBRACE"].normal_dead, "dead_grave")

    def test_latam_symbols(self):
        table = kl._layout_bindings("latam")
        self.assertEqual(table["KEY_SLASH"].normal, "-")
        self.assertEqual(table["KEY_SEMICOLON"].normal, "ñ")
        self.assertEqual(table["KEY_APOSTROPHE"].normal, "{")
        self.assertEqual(table["KEY_BACKSLASH"].normal, "}")
        self.assertEqual(table["KEY_2"].altgr, "@")


class TestCapabilityClassification(unittest.TestCase):
    """Exercises is_keyboard_capabilities with a synthetic evdev module."""

    @staticmethod
    def _fake_evdev_module():
        ecodes = types.SimpleNamespace()
        ecodes.EV_KEY = 1
        ecodes.KEY_ENTER = 28
        ecodes.KEY_SPACE = 57
        ecodes.KEY_BACKSPACE = 14
        ecodes.KEY_LEFTSHIFT = 42
        for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            setattr(ecodes, f"KEY_{letter}", 100 + index)
        module = types.ModuleType("evdev")
        module.ecodes = ecodes
        return module, ecodes

    def test_full_keyboard_is_accepted(self):
        module, ecodes = self._fake_evdev_module()
        keys = [getattr(ecodes, f"KEY_{letter}") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
        keys.extend([ecodes.KEY_ENTER, ecodes.KEY_SPACE, ecodes.KEY_BACKSPACE, ecodes.KEY_LEFTSHIFT])
        with patch.dict(sys.modules, {"evdev": module}):
            self.assertTrue(
                kl.is_keyboard_capabilities({ecodes.EV_KEY: keys}, "USB Keyboard")
            )

    def test_hdmi_remote_is_rejected(self):
        module, ecodes = self._fake_evdev_module()
        with patch.dict(sys.modules, {"evdev": module}):
            self.assertFalse(
                kl.is_keyboard_capabilities(
                    {ecodes.EV_KEY: [ecodes.KEY_ENTER]},
                    "vc4-hdmi",
                )
            )

    def test_partial_keyboard_is_rejected(self):
        module, ecodes = self._fake_evdev_module()
        with patch.dict(sys.modules, {"evdev": module}):
            self.assertFalse(
                kl.is_keyboard_capabilities(
                    {ecodes.EV_KEY: [ecodes.KEY_ENTER, ecodes.KEY_SPACE]},
                    "Unknown device",
                )
            )

    def test_without_evdev_returns_false(self):
        with patch.dict(sys.modules, {"evdev": None}):
            self.assertFalse(kl.is_keyboard_capabilities({1: [28]}, "Keyboard"))


if __name__ == "__main__":
    unittest.main()
