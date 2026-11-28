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

    def test_reinstall_replaces_assets_and_removes_stale_files(self):
        first = self.mod.install(self.entry)
        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            old_files = list(json.load(handle)["apps"]["obsidian"]["files"])
        old_icon = next(path for path in old_files if "/256/apps/" in path)
        self.assertTrue(os.path.isfile(old_icon))

        new_payload = b"fake AppImage payload versione 2\n"
        self.payload = new_payload

        def _fake_extract_v2(appimage, workdir):
            root = os.path.join(workdir, "squashfs-root")
            os.makedirs(root, exist_ok=True)
            with open(os.path.join(root, "obsidian-v2.desktop"), "w", encoding="utf-8") as handle:
                handle.write(
                    "[Desktop Entry]\nName=Obsidian v2\n"
                    "Comment=seconda versione\nCategories=GTK;\n"
                )
            with open(os.path.join(root, "obsidian-128x128.png"), "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\nicona-128")
            return root

        self.mod._extract_appimage = _fake_extract_v2
        entry_v2 = dict(
            self.entry,
            version="2.0",
            sha256=hashlib.sha256(new_payload).hexdigest(),
        )
        second = self.mod.install(entry_v2)

        self.assertEqual(second, first)
        with open(second, "rb") as handle:
            self.assertEqual(handle.read(), new_payload)
        self.assertFalse(os.path.exists(old_icon))
        new_icon = os.path.join(self.mod.icons_dir(), "128", "apps", "obsidian.png")
        self.assertTrue(os.path.isfile(new_icon))

        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["apps"]["obsidian"]["version"], "2.0")
        self.assertEqual(
            set(manifest["apps"]["obsidian"]["files"]),
            {second, self.mod.desktop_path("obsidian"), new_icon},
        )
        with open(self.mod.desktop_path("obsidian"), encoding="utf-8") as handle:
            self.assertIn("Name=Obsidian v2", handle.read())
        for directory in (self.mod.state_dir(), self.mod.apps_dir()):
            leftovers = [
                name for name in os.listdir(directory)
                if ".part" in name or name.startswith("hyleos-appimage-")
            ]
            self.assertEqual(leftovers, [])

    def test_icon_size_and_ext_ignores_size_like_path_components(self):
        for component in ("hg9dl3x7", "9x1uwlyz"):
            with self.subTest(component=component):
                path = os.path.join(self.tmp.name, component, "obsidian-128x128.png")
                self.assertEqual(self.mod._icon_size_and_ext(path), (128, ".png"))
        mixed = os.path.join(self.tmp.name, "hg9dl3x7", "app-3x7-128x128.png")
        self.assertEqual(self.mod._icon_size_and_ext(mixed), (128, ".png"))
        valid = os.path.join(self.tmp.name, "hg9dl3x7", "app-256x256.png")
        self.assertEqual(self.mod._icon_size_and_ext(valid), (256, ".png"))

    def test_reinstall_replaces_assets_with_size_like_temp_path_component(self):
        weird_home = os.path.join(self.tmp.name, "hg9dl3x7", "home")
        os.makedirs(weird_home, exist_ok=True)
        os.environ["HOME"] = weird_home

        first = self.mod.install(self.entry)
        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            old_files = list(json.load(handle)["apps"]["obsidian"]["files"])
        old_icon = next(path for path in old_files if "/256/apps/" in path)
        self.assertTrue(os.path.isfile(old_icon))

        new_payload = b"fake AppImage payload v2 con componente 3x7 nel path\n"
        self.payload = new_payload

        def _fake_extract_v2(appimage, workdir):
            root = os.path.join(workdir, "squashfs-root")
            os.makedirs(root, exist_ok=True)
            with open(os.path.join(root, "obsidian-v2.desktop"), "w", encoding="utf-8") as handle:
                handle.write("[Desktop Entry]\nName=Obsidian v2\n")
            with open(os.path.join(root, "obsidian-128x128.png"), "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\nicona-128")
            return root

        self.mod._extract_appimage = _fake_extract_v2
        entry_v2 = dict(
            self.entry,
            version="2.0",
            sha256=hashlib.sha256(new_payload).hexdigest(),
        )
        second = self.mod.install(entry_v2)

        self.assertEqual(second, first)
        self.assertFalse(os.path.exists(old_icon))
        new_icon = os.path.join(self.mod.icons_dir(), "128", "apps", "obsidian.png")
        self.assertTrue(os.path.isfile(new_icon))

    def test_reinstall_failure_rolls_back_binary(self):
        first = self.mod.install(self.entry)
        with open(first, "rb") as handle:
            old_binary = handle.read()
        old_icon = os.path.join(self.mod.icons_dir(), "256", "apps", "obsidian.png")
        with open(old_icon, "rb") as handle:
            old_icon_data = handle.read()
        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            old_manifest = json.load(handle)

        new_payload = b"fake AppImage payload che non deve restare\n"
        self.payload = new_payload
        entry_v2 = dict(
            self.entry,
            version="2.0",
            sha256=hashlib.sha256(new_payload).hexdigest(),
        )

        original_write_desktop = self.mod._write_desktop
        calls = []

        def flaky_write_desktop(source, destination, entry, appimage):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError("errore simulato sulla destinazione finale")
            return original_write_desktop(source, destination, entry, appimage)

        self.mod._write_desktop = flaky_write_desktop
        with self.assertRaises(OSError):
            self.mod.install(entry_v2)

        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].startswith(self.mod.state_dir()), calls[0])
        self.assertEqual(calls[1], self.mod.desktop_path("obsidian"))
        with open(first, "rb") as handle:
            self.assertEqual(handle.read(), old_binary)
        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), old_manifest)
        with open(old_icon, "rb") as handle:
            self.assertEqual(handle.read(), old_icon_data)
        for directory in (self.mod.state_dir(), self.mod.apps_dir()):
            leftovers = [
                name for name in os.listdir(directory)
                if ".part" in name or name.startswith("hyleos-appimage-")
            ]
            self.assertEqual(leftovers, [])

    def test_asset_rollback_removes_new_paths_after_commit_failure(self):
        first = self.mod.install(self.entry)
        old_icon = os.path.join(self.mod.icons_dir(), "256", "apps", "obsidian.png")
        with open(old_icon, "rb") as handle:
            old_icon_data = handle.read()
        old_desktop = self.mod.desktop_path("obsidian")
        with open(old_desktop, encoding="utf-8") as handle:
            old_desktop_data = handle.read()

        old_payload = self.payload
        new_payload = b"fake AppImage payload v2 con icona 128\n"
        self.payload = new_payload

        def _fake_extract_v2(appimage, workdir):
            root = os.path.join(workdir, "squashfs-root")
            os.makedirs(root, exist_ok=True)
            with open(os.path.join(root, "v2.desktop"), "w", encoding="utf-8") as handle:
                handle.write("[Desktop Entry]\nName=Obsidian v2\n")
            with open(os.path.join(root, "app-128x128.png"), "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\nicona-128")
            return root

        self.mod._extract_appimage = _fake_extract_v2
        entry_v2 = dict(
            self.entry,
            version="2.0",
            sha256=hashlib.sha256(new_payload).hexdigest(),
        )

        original_write_manifest = self.mod._write_manifest
        calls = []

        def flaky_write_manifest(manifest):
            calls.append(manifest)
            if len(calls) == 1:
                raise OSError("errore simulato sul manifest v2")
            return original_write_manifest(manifest)

        self.mod._write_manifest = flaky_write_manifest
        with self.assertRaises(OSError):
            self.mod.install(entry_v2)

        self.assertEqual(len(calls), 2)
        with open(first, "rb") as handle:
            self.assertEqual(handle.read(), old_payload)
        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["apps"]["obsidian"]["version"], "1.0")
        with open(old_desktop, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), old_desktop_data)
        with open(old_icon, "rb") as handle:
            self.assertEqual(handle.read(), old_icon_data)
        new_icon = os.path.join(self.mod.icons_dir(), "128", "apps", "obsidian.png")
        self.assertFalse(os.path.exists(new_icon))
        for directory in (self.mod.state_dir(), self.mod.apps_dir()):
            leftovers = [
                name for name in os.listdir(directory)
                if ".part" in name or name.startswith("hyleos-appimage-")
            ]
            self.assertEqual(leftovers, [])

    def test_cached_icon_downloads_validates_and_caches(self):
        class FakeResponse:
            def __init__(self, data):
                self._data = data
                self.headers = {"Content-Type": "image/png"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                if not self._data:
                    return b""
                if size is None or size < 0:
                    size = len(self._data)
                chunk = self._data[:size]
                self._data = self._data[size:]
                return chunk

        def responder(data):
            def _urlopen(request, timeout=None):
                return FakeResponse(data)
            return _urlopen

        png = b"\x89PNG\r\n\x1a\n" + b"immagine finta"
        entry = dict(self.entry, icon_url="https://example.invalid/icona.png")
        with mock.patch.object(
            self.mod.urllib.request, "urlopen", side_effect=responder(png)
        ) as urlopen:
            path = self.mod.cached_icon(entry)
            self.assertIsNotNone(path)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(path.startswith(self.home))
            self.assertIn("/.cache/hyleos-store/icons/obsidian/", path)
            self.assertEqual(self.mod.cached_icon(entry), path)
            self.assertEqual(urlopen.call_count, 1)

        icon_dir = os.path.dirname(path)
        before = sorted(os.listdir(icon_dir))
        bad_png = b"GIF89a" + b"x" * 32
        with mock.patch.object(
            self.mod.urllib.request, "urlopen", side_effect=responder(bad_png)
        ):
            self.assertIsNone(
                self.mod.cached_icon(dict(self.entry, icon_url="https://example.invalid/bad.png"))
            )
        self.assertEqual(sorted(os.listdir(icon_dir)), before)

        huge = b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024)
        with mock.patch.object(
            self.mod.urllib.request, "urlopen", side_effect=responder(huge)
        ):
            self.assertIsNone(
                self.mod.cached_icon(dict(self.entry, icon_url="https://example.invalid/huge.png"))
            )
        self.assertEqual(sorted(os.listdir(icon_dir)), before)

    def test_cached_icon_rejects_non_https_and_empty(self):
        with mock.patch.object(
            self.mod.urllib.request, "urlopen", side_effect=AssertionError("nessuna rete attesa")
        ) as urlopen:
            for url in ("", "http://example.invalid/icona.png", "ftp://example.invalid/x.png"):
                with self.subTest(url=url):
                    self.assertIsNone(
                        self.mod.cached_icon(dict(self.entry, icon_url=url))
                    )
            self.assertEqual(urlopen.call_count, 0)

    def test_cached_icon_refuses_symlinked_cache_dir(self):
        outside = os.path.join(self.tmp.name, "fuori-home")
        os.makedirs(outside)
        cache = os.path.join(self.home, ".cache")
        os.symlink(outside, cache)
        entry = dict(self.entry, icon_url="https://example.invalid/icona.png")
        with mock.patch.object(
            self.mod.urllib.request, "urlopen", side_effect=AssertionError("nessuna rete attesa")
        ):
            self.assertIsNone(self.mod.cached_icon(entry))
        self.assertEqual(os.listdir(outside), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
