#!/usr/bin/env python3
"""Test della migrazione configurazione HyleOS (hyleos-config-migrate)."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "out/rootfs-overlay/usr/bin/hyleos-config-migrate",
)


def load_module():
    loader = importlib.machinery.SourceFileLoader("hyleos_config_migrate", SCRIPT)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class ConfigMigrateTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.tmp = tempfile.mkdtemp(prefix="hyleos-cfg-")
        self.home = os.path.join(self.tmp, "home")
        self.skel = os.path.join(self.tmp, "skel")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.skel, ".config/hypr"))
        os.makedirs(os.path.join(self.skel, ".config/DankMaterialShell"))
        os.makedirs(os.path.join(self.skel, ".config/kitty"))
        self.mod.SKEL = self.skel

        with open(os.path.join(self.skel, ".config/hypr/hyprland.lua"), "w") as f:
            f.write('hl.config({ kb_layout = "it" })\n')
        with open(os.path.join(self.skel, ".config/kitty/kitty.conf"), "w") as f:
            f.write("font_size 11\n")
        with open(os.path.join(self.skel, ".config/DankMaterialShell/settings.json"), "w") as f:
            json.dump(
                {
                    "theme": "hyle",
                    "barConfigs": [{"id": "default", "enabled": True}],
                    "updaterUseCustomCommand": True,
                    "updaterCustomCommand": "hyleos-system-update",
                },
                f,
            )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kw):
        return self.mod.run(self.home, self.state, **kw)

    def test_fresh_home_gets_defaults_and_marker(self):
        self._run()
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".config/hypr/hyprland.lua")))
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".config/kitty/kitty.conf")))
        with open(os.path.join(self.home, ".config/DankMaterialShell/settings.json")) as f:
            data = json.load(f)
        self.assertEqual(data["theme"], "hyle")
        with open(os.path.join(self.home, ".config/hyleos/config-version")) as f:
            self.assertEqual(int(f.read().strip()), self.mod.CONFIG_VERSION)

    def test_user_values_are_never_overwritten(self):
        dst = os.path.join(self.home, ".config/DankMaterialShell/settings.json")
        os.makedirs(os.path.dirname(dst))
        with open(dst, "w") as f:
            json.dump({"theme": "custom", "barConfigs": [{"id": "mine"}]}, f)

        self._run()

        with open(dst) as f:
            data = json.load(f)
        self.assertEqual(data["theme"], "custom")
        self.assertEqual(data["barConfigs"], [{"id": "mine"}])
        # chiavi nuove aggiunte, non sostituite
        self.assertTrue(data["updaterUseCustomCommand"])
        self.assertEqual(data["updaterCustomCommand"], "hyleos-system-update")

    def test_second_run_is_idempotent(self):
        self._run()
        dst = os.path.join(self.home, ".config/DankMaterialShell/settings.json")
        with open(dst) as handle:
            before = handle.read()
        mtime = os.stat(dst).st_mtime_ns
        changed = self._run()
        self.assertFalse(changed)
        with open(dst) as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(os.stat(dst).st_mtime_ns, mtime)

    def test_old_hyprland_conf_is_backed_up_not_deleted(self):
        hypr = os.path.join(self.home, ".config/hypr")
        os.makedirs(hypr)
        with open(os.path.join(hypr, "hyprland.conf"), "w") as f:
            f.write("monitor=,preferred,auto,1\n")

        self._run()

        self.assertTrue(os.path.isfile(os.path.join(hypr, "hyprland.lua")))
        backups = [n for n in os.listdir(hypr) if n.startswith("hyprland.conf.pre-lua.")]
        self.assertEqual(len(backups), 1)
        self.assertTrue(os.path.isfile(os.path.join(hypr, backups[0])))

    def test_dry_run_writes_nothing(self):
        changed = self._run(dry_run=True)
        self.assertTrue(changed)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config/hypr/hyprland.lua")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config/hyleos/config-version")))

    def test_invalid_user_json_is_left_untouched(self):
        dst = os.path.join(self.home, ".config/DankMaterialShell/settings.json")
        os.makedirs(os.path.dirname(dst))
        with open(dst, "w") as f:
            f.write("{not valid json")
        self._run()
        with open(dst) as handle:
            self.assertEqual(handle.read(), "{not valid json")

    def test_window_mode_default_is_created_and_not_overwritten(self):
        os.makedirs(os.path.join(self.skel, ".config/hyleos"))
        with open(os.path.join(self.skel, ".config/hyleos/window-mode.json"), "w") as f:
            json.dump({"version": 1, "mode": "tiling"}, f)

        self._run()
        dst = os.path.join(self.home, ".config/hyleos/window-mode.json")
        with open(dst) as f:
            self.assertEqual(json.load(f)["mode"], "tiling")

        # Una scelta esplicita dell'utente vince sulla migrazione successiva.
        with open(dst, "w") as f:
            json.dump({"version": 1, "mode": "floating"}, f)
        self._run()
        with open(dst) as f:
            self.assertEqual(json.load(f)["mode"], "floating")


if __name__ == "__main__":
    unittest.main(verbosity=2)
