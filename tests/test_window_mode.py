#!/usr/bin/env python3
"""Test di hyleos-window-mode: persistenza e applicazione della modalita'."""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "out/rootfs-overlay/usr/bin/hyleos-window-mode",
)


def load_module():
    loader = importlib.machinery.SourceFileLoader("hyleos_window_mode", SCRIPT)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class WindowModeTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.tmp = tempfile.mkdtemp(prefix="hyleos-window-mode-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self._old_env = {
            "HOME": os.environ.get("HOME"),
            "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME"),
        }
        os.environ["HOME"] = self.home
        os.environ.pop("XDG_CONFIG_HOME", None)

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self):
        return os.path.join(self.home, ".config/hyleos/window-mode.json")

    def test_default_is_tiling_without_file(self):
        self.assertEqual(self.mod.read_mode(), "tiling")
        self.assertFalse(os.path.exists(self._path()))

    def test_set_writes_versioned_json(self):
        self.mod.write_mode("floating")
        self.assertTrue(os.path.isfile(self._path()))
        with open(self._path()) as handle:
            data = json.load(handle)
        self.assertEqual(data, {"version": 1, "mode": "floating"})
        self.assertEqual(self.mod.read_mode(), "floating")

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.write_mode("bogus")

    def test_corrupt_file_falls_back_to_tiling(self):
        os.makedirs(os.path.dirname(self._path()))
        with open(self._path(), "w") as handle:
            handle.write("{not json")
        self.assertEqual(self.mod.read_mode(), "tiling")

    def test_unknown_mode_value_falls_back_to_tiling(self):
        os.makedirs(os.path.dirname(self._path()))
        with open(self._path(), "w") as handle:
            json.dump({"version": 1, "mode": "tiled"}, handle)
        self.assertEqual(self.mod.read_mode(), "tiling")

    def test_protected_classes_and_titles(self):
        self.assertTrue(self.mod.is_protected({"class": "org.hyleos.store", "title": "Hyle Store"}))
        self.assertTrue(self.mod.is_protected({"class": "xdg-desktop-portal-gtk.FileChooser", "title": "Open"}))
        self.assertTrue(self.mod.is_protected({"class": "kitty", "title": "Save File"}))
        self.assertFalse(self.mod.is_protected({"class": "kitty", "title": "vim README"}))

    def test_apply_floating_only_touches_unprotected_tiled_windows(self):
        calls = []

        self.mod.hyprland_available = lambda: True
        self.mod.clients = lambda: [
            {"address": "0x1", "class": "kitty", "title": "shell", "floating": False},
            {"address": "0x2", "class": "org.hyleos.store", "title": "Hyle Store", "floating": False},
            {"address": "0x3", "class": "firefox", "title": "web", "floating": True},
        ]
        self.mod.dispatch = lambda lua: calls.append(lua)
        self.mod.set_rule_enabled = lambda enabled: calls.append("rule:%s" % enabled)

        self.assertTrue(self.mod.apply_mode("floating"))

        self.assertIn("rule:True", calls)
        float_calls = [c for c in calls if "float" in c]
        self.assertEqual(len(float_calls), 1)
        self.assertIn("0x1", float_calls[0])
        self.assertNotIn("0x2", float_calls[0])

    def test_apply_tiling_unfloats_unprotected_floating_windows(self):
        calls = []

        self.mod.hyprland_available = lambda: True
        self.mod.clients = lambda: [
            {"address": "0x1", "class": "kitty", "title": "shell", "floating": True},
            {"address": "0x2", "class": "org.hyleos.store", "title": "Hyle Store", "floating": True},
        ]
        self.mod.dispatch = lambda lua: calls.append(lua)
        self.mod.set_rule_enabled = lambda enabled: calls.append("rule:%s" % enabled)

        self.assertTrue(self.mod.apply_mode("tiling"))

        self.assertIn("rule:False", calls)
        unfloat_calls = [c for c in calls if "unset" in c]
        self.assertEqual(len(unfloat_calls), 1)
        self.assertIn("0x1", unfloat_calls[0])
        self.assertNotIn("0x2", unfloat_calls[0])

    def test_apply_without_hyprland_keeps_file(self):
        self.mod.hyprland_available = lambda: False
        self.mod.write_mode("floating")
        self.assertFalse(self.mod.apply_mode("floating"))
        self.assertEqual(self.mod.read_mode(), "floating")


if __name__ == "__main__":
    unittest.main(verbosity=2)
