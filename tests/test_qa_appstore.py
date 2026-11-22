#!/usr/bin/env python3
"""QA avversario: test di regressione per il fix App Store (ADR-0009).

Aggiunge Uniq case ostili senza toccare sorgenti o test esistenti.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "out/rootfs-overlay/usr/share/hyleos/appimage_backend.py")


def load_backend():
    loader = importlib.machinery.SourceFileLoader("qa_appimage_backend", SCRIPT)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class QAAppImageRollbackTest(unittest.TestCase):
    """Dopo il commit, il rollback ripristina binario, manifest e asset."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qa-appimage-")
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home, exist_ok=True)
        saved = os.environ.get("HOME")
        self.addCleanup(self._restore_home, saved)
        os.environ["HOME"] = self.home
        self.mod = load_backend()

        self.payload = b"v1\n"
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.entry = {
            "id": "obsidian",
            "name": "Obsidian",
            "summary": "Note",
            "category": "Ufficio",
            "version": "1.0",
            "download_url": "https://example.invalid/obsidian.AppImage",
            "sha256": self.digest,
            "icon_url": "",
        }
        self.mod._download = lambda url, dest: self._write(dest, self.payload)
        self.extract_name = "Obsidian v1"
        self.mod._extract_appimage = lambda appimage, workdir: self._extract(appimage, workdir)

    @staticmethod
    def _restore_home(saved):
        if saved is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved

    @staticmethod
    def _write(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)

    def _extract(self, appimage, workdir):
        root = os.path.join(workdir, "squashfs-root")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "obsidian.desktop"), "w", encoding="utf-8") as handle:
            handle.write("[Desktop Entry]\nName=%s\n" % self.extract_name)
        self._write(os.path.join(root, "obsidian-256x256.png"), b"ICONA-NUOVA")
        return root

    def test_icon_failure_after_commit_restores_stale_desktop(self):
        binary = self.mod.install(self.entry)
        desktop = self.mod.desktop_path("obsidian")
        icon = os.path.join(self.mod.icons_dir(), "256", "apps", "obsidian.png")
        with open(desktop, encoding="utf-8") as handle:
            old_desktop = handle.read()
        self.assertIn("Obsidian v1", old_desktop)
        with open(icon, "rb") as handle:
            old_icon = handle.read()

        self.extract_name = "Obsidian v2"
        second = dict(self.entry, version="2.0")
        original_copy = self.mod._copy_file_user

        def failing_copy(source, destination):
            if destination.endswith(os.path.join("apps", "obsidian.png")):
                raise OSError("errore simulato sull'icona finale")
            return original_copy(source, destination)

        self.mod._copy_file_user = failing_copy
        with self.assertRaises(OSError):
            self.mod.install(second)

        # Binario, manifest e asset tornano tutti alla versione 1.
        with open(binary, "rb") as handle:
            self.assertEqual(handle.read(), b"v1\n")
        with open(self.mod.manifest_path(), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["apps"]["obsidian"]["version"], "1.0")
        with open(icon, "rb") as handle:
            self.assertEqual(handle.read(), old_icon)
        with open(desktop, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), old_desktop)
        self.assertNotIn("Obsidian v2", old_desktop)

        # Nessun file v2, .part o directory di staging sopravvive.
        self.assertEqual(os.listdir(self.mod.desktop_dir()), [os.path.basename(desktop)])
        self.assertEqual(os.listdir(os.path.dirname(icon)), [os.path.basename(icon)])
        self.assertEqual(os.listdir(self.mod.apps_dir()), [os.path.basename(binary)])
        leftovers = [
            name for name in os.listdir(self.mod.state_dir())
            if ".part" in name or name.startswith("hyleos-appimage-")
        ]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
