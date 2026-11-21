#!/usr/bin/env python3
"""Test del backend AppImage dello Store: nessuna rete, nessun root."""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "out/rootfs-overlay/usr/share/hyleos/appimage_backend.py")


def load_module():
    loader = importlib.machinery.SourceFileLoader("appimage_backend", SCRIPT)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class AppImageBackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hyleos-appimage-test-")
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home, exist_ok=True)

        self.old_env = {key: os.environ.get(key) for key in ("HOME", "HYLEOS_APPIMAGE_CATALOG")}
        self.addCleanup(self._restore_env)
        os.environ["HOME"] = self.home
        os.environ.pop("HYLEOS_APPIMAGE_CATALOG", None)

        self.mod = load_module()
        self.original_extract = self.mod._extract_appimage

        self.payload = b"fake AppImage payload per i test\n"
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.entry = {
            "id": "obsidian",
            "name": "Obsidian",
            "summary": "Note personali",
            "category": "Ufficio",
            "version": "1.0",
            "download_url": "https://example.invalid/obsidian.AppImage",
            "sha256": self.digest,
            "icon_url": "",
        }

        # Nessuna reale rete: il download scrive sempre il payload locale.
        self.mod._download = lambda url, dest: self._write_payload(dest)
        self.mod._extract_appimage = self._fake_extract

    def _restore_env(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _write_payload(self, destination):
        with open(destination, "wb") as handle:
            handle.write(self.payload)

    def _fake_extract(self, appimage, workdir):
        root = os.path.join(workdir, "squashfs-root")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "app.desktop"), "w", encoding="utf-8") as handle:
            handle.write(
                "[Desktop Entry]\n"
                "Name=Obsidian dal pacchetto\n"
                "Comment=Commento estratto\n"
                "Exec=/vecchio/percorso\n"
                "Icon=vecchia\n"
                "Categories=GTK;\n"
            )
        with open(os.path.join(root, "app-256x256.png"), "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\nfake-icon")
        return root

    def _write_catalog(self, data, name="catalog.json"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def test_load_catalog_valid_and_env_override(self):
        path = self._write_catalog({"version": 1, "apps": [self.entry]})
        os.environ["HYLEOS_APPIMAGE_CATALOG"] = path
        self.assertEqual(self.mod.catalog_path(), path)
        entries = self.mod.load_catalog()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "obsidian")
        self.assertEqual(entries[0]["source"], "AppImage")
        self.assertEqual(self.mod.catalog(path), entries)

    def test_example_catalog_with_empty_hash_is_readable(self):
        example = dict(self.entry, sha256="", download_url="")
        entries = self.mod.load_catalog(self._write_catalog({"version": 1, "apps": [example]}, "example.json"))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["sha256"], "")

    def test_real_system_catalog_is_compatible(self):
        """Il catalogo reale dell'overlay resta leggibile senza installare nulla."""
        catalog_file = os.path.join(
            ROOT, "out/rootfs-overlay/usr/share/hyleos/appimage-catalog.json"
        )
        with open(catalog_file, encoding="utf-8") as handle:
            data = json.load(handle)

        self.assertEqual(data.get("version"), 1)
        apps = data.get("apps")
        self.assertIsInstance(apps, list)
        self.assertEqual(len(apps), 3)
        self.assertEqual(
            {entry.get("id") for entry in apps},
            {"obsidian", "krita", "zen-browser"},
        )

        required = (
            "id", "name", "summary", "category", "version",
            "download_url", "sha256", "icon_url",
        )
        for entry in apps:
            with self.subTest(app=entry.get("id")):
                for field in required:
                    self.assertIn(field, entry)
                    self.assertIsInstance(entry[field], str)
                self.assertRegex(entry["sha256"], r"^[0-9a-fA-F]{64}$")
                self.assertIsNotNone(re.fullmatch(r"[0-9a-fA-F]{64}", entry["sha256"]))
                # Non deve sollevare: il backend accetta tutte le voci reali.
                self.mod.validate_entry(entry)

        loaded = self.mod.load_catalog(catalog_file)
        self.assertEqual(len(loaded), 3)
        self.assertEqual(
            [entry["id"] for entry in loaded],
            ["obsidian", "krita", "zen-browser"],
        )
        self.assertEqual(loaded, [self.mod.validate_entry(entry) for entry in apps])

    def test_load_catalog_invalid(self):
        cases = (
            {"apps": []},
            {"version": 1, "apps": {}},
            {"version": 2, "apps": []},
            {"version": 1, "apps": [dict(self.entry, id="a/../../x")]},
            {"version": 1, "apps": [dict(self.entry, sha256="non-esadecimale")]},
            {"version": 1, "apps": [dict(self.entry, download_url="ftp://example.invalid/x")]},
        )
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    self.mod.load_catalog(self._write_catalog(data, "bad.json"))
        with self.assertRaises(ValueError):
            self.mod.load_catalog(os.path.join(self.tmp.name, "inesistente.json"))

    def test_install_rejects_empty_or_wrong_sha256(self):
        for digest in ("", "0" * 64):
            with self.subTest(digest=digest):
                with self.assertRaises(ValueError):
                    self.mod.install(dict(self.entry, sha256=digest))
        root = self.mod.apps_dir()
        leftovers = [name for name in os.listdir(root) if name.startswith(".") or name.endswith(".AppImage")]
        self.assertEqual(leftovers, [])
        self.assertFalse(os.path.exists(self.mod.manifest_path()))

    def test_install_idempotent_and_manifest_tracks_exact_files(self):
        final = self.mod.install(self.entry)
        self.assertEqual(final, self.mod.appimage_path("obsidian"))
        self.assertEqual(stat.S_IMODE(os.stat(final).st_mode), 0o755)

        again = self.mod.install(self.entry)
        self.assertEqual(again, final)
        self.assertEqual(
            [name for name in os.listdir(self.mod.apps_dir()) if name.endswith(".AppImage")],
            ["obsidian.AppImage"],
        )

        desktop = self.mod.desktop_path("obsidian")
        with open(desktop, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("[Desktop Entry]", text)
        self.assertIn("Exec=" + final, text)
        self.assertIn("TryExec=" + final, text)
        self.assertIn("Icon=obsidian", text)
        self.assertIn("Categories=", text)

        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            manifest = json.load(handle)
        files = manifest["apps"]["obsidian"]["files"]
        self.assertEqual(len(files), 3)
        for path in files:
            self.assertTrue(os.path.isfile(path), path)
        self.assertTrue(any("/256/apps/obsidian.png" in path for path in files))
        self.assertEqual(set(files), {final, desktop, os.path.join(self.mod.icons_dir(), "256", "apps", "obsidian.png")})

        leftovers = [name for name in os.listdir(self.mod.apps_dir()) if ".part" in name]
        self.assertEqual(leftovers, [])

    def test_remove_is_idempotent_and_only_removes_manifest_files(self):
        self.mod.install(self.entry)
        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            files = list(json.load(handle)["apps"]["obsidian"]["files"])

        user_appimage = os.path.join(self.mod.apps_dir(), "mio-app.AppImage")
        user_desktop = os.path.join(self.mod.desktop_dir(), "mio-app.desktop")
        for path in (user_appimage, user_desktop):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("dati utente")

        self.assertTrue(self.mod.remove("obsidian"))
        self.assertTrue(self.mod.remove("obsidian"))
        for path in files:
            self.assertFalse(os.path.exists(path), path)
        self.assertTrue(os.path.isfile(user_appimage))
        self.assertTrue(os.path.isfile(user_desktop))
        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["apps"], {})

    def test_remove_without_manifest_uses_only_canonical_paths(self):
        canonical = [self.mod.appimage_path("plain"), self.mod.desktop_path("plain")]
        for path in canonical:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("file canonico")
        user_file = os.path.join(self.mod.apps_dir(), "non-gestita.AppImage")
        with open(user_file, "w", encoding="utf-8") as handle:
            handle.write("utente")

        self.mod.remove("plain")
        for path in canonical:
            self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.isfile(user_file))
        self.assertFalse(os.path.exists(self.mod.manifest_path()))

    def test_safe_basename_rejects_path_traversal(self):
        for bad in ("a/../../x", "../x", "..", "x\\..\\y", "", "a\x00b"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.mod.safe_basename(bad)
        self.assertEqual(self.mod.safe_basename("Icon@2x.png"), "Icon_2x.png")

    def test_install_and_open_never_call_privileged_helpers(self):
        self.mod._extract_appimage = self.original_extract
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(("run", list(cmd)))
            return subprocess.CompletedProcess(cmd, 1, "", "")

        def fake_popen(cmd, **kwargs):
            calls.append(("popen", list(cmd), kwargs))
            return object()

        with mock.patch.object(self.mod.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(self.mod.subprocess, "Popen", side_effect=fake_popen):
            self.mod.install(self.entry)
            self.mod.open_app("obsidian")

        self.assertTrue(calls)
        for call in calls:
            joined = " ".join(str(part) for part in call[1])
            for forbidden in ("pkexec", "doas", "sudo"):
                self.assertNotIn(forbidden, joined)

    def test_open_app_uses_popen_without_shell(self):
        self.mod.install(self.entry)
        sentinel = object()
        with mock.patch.object(self.mod.subprocess, "Popen", return_value=sentinel) as popen:
            result = self.mod.open_app("obsidian")
        self.assertIs(result, sentinel)
        args, kwargs = popen.call_args
        self.assertEqual(args, ([self.mod.appimage_path("obsidian")],))
        self.assertIs(kwargs.get("shell"), False)
        self.assertIsInstance(args[0], list)

    def test_is_installed_and_installed_ids(self):
        self.assertFalse(self.mod.is_installed("obsidian"))
        self.mod.install(self.entry)
        self.assertTrue(self.mod.is_installed("obsidian"))
        self.assertEqual(self.mod.installed_ids(), ["obsidian"])
        self.assertFalse(self.mod.is_installed("a/../../x"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
