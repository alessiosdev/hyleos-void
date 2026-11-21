#!/usr/bin/env python3
"""Regression test per hyleos-appimage-update: argomenti e filtro versioni.

Nessuna rete, nessuna home reale, nessun root: il modulo viene caricato dal
sorgente dell'overlay e tutte le chiamate usano dati finti in memoria.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "out/rootfs-overlay/usr/bin/hyleos-appimage-update")


def load_module():
    loader = importlib.machinery.SourceFileLoader("hyleos_appimage_update", SCRIPT)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class AppImageUpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    @staticmethod
    def _entry(app_id, version):
        return {
            "id": app_id,
            "name": "App " + app_id,
            "version": version,
            "download_url": "https://example.invalid/%s.AppImage" % app_id,
            "sha256": "0" * 64,
        }

    def _find(self, old, new, force=False):
        catalog = [self._entry("demo", new)]
        manifest = {"demo": {"version": old}}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            updates = self.mod.find_updates(catalog, manifest, force=force)
        return updates, output.getvalue()

    def test_parse_args(self):
        for argv, expected in (
            (["--check"], (True, False)),
            (["--force"], (False, True)),
            (["--yes"], (False, False)),
            (["--unknown"], (False, False)),
        ):
            with self.subTest(argv=argv):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(self.mod.parse_args(argv), expected)

    def test_parse_args_force_short_flag(self):
        self.assertEqual(self.mod.parse_args(["-f"]), (False, True))
        self.assertEqual(self.mod.parse_args(["--check", "-f"]), (True, True))

    def test_find_updates_newer_catalog_version(self):
        updates, output = self._find("1.2.0", "1.3.0")
        self.assertEqual(len(updates), 1)
        app_id, old, new, entry = updates[0]
        self.assertEqual(app_id, "demo")
        self.assertEqual((old, new), ("1.2.0", "1.3.0"))
        self.assertEqual(entry["id"], "demo")
        self.assertEqual(output, "")

    def test_find_updates_skips_downgrade_unless_forced(self):
        updates, output = self._find("2.0", "1.9")
        self.assertEqual(updates, [])
        self.assertIn("non superiore", output)
        self.assertIn("--force", output)

        forced, forced_output = self._find("2.0", "1.9", force=True)
        self.assertEqual(len(forced), 1)
        self.assertEqual(forced[0][:3], ("demo", "2.0", "1.9"))
        self.assertEqual(forced_output, "")

    def test_find_updates_skips_non_comparable_versions_unless_forced(self):
        updates, output = self._find("beta", "1.0")
        self.assertEqual(updates, [])
        self.assertIn("non confrontabili", output)
        self.assertIn("--force", output)

        forced, forced_output = self._find("beta", "1.0", force=True)
        self.assertEqual(len(forced), 1)
        self.assertEqual(forced[0][:3], ("demo", "beta", "1.0"))
        self.assertEqual(forced_output, "")

    def test_find_updates_ignores_same_version_and_unknown_ids(self):
        updates, _output = self._find("1.0", "1.0")
        self.assertEqual(updates, [])
        catalog = [self._entry("demo", "1.1")]
        with contextlib.redirect_stdout(io.StringIO()):
            updates = self.mod.find_updates(catalog, {"altro": {"version": "1.0"}})
        self.assertEqual(updates, [])

    def test_source_has_no_global_tempdir_override(self):
        with open(SCRIPT, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("tempfile", "temp_root", "tempfile.tempdir"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
