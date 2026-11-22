#!/usr/bin/env python3
"""Test delle funzioni pure della UI dello Store, senza richiedere GTK/GI."""
import importlib.machinery
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "out/rootfs-overlay/usr/share/hyleos/appstore.py")


class _NoGI:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "gi" or fullname.startswith("gi."):
            raise ImportError("gi disabilitato nei test della UI")
        return None


def load_appstore_without_gi():
    gi_names = [name for name in sys.modules if name == "gi" or name.startswith("gi.")]
    saved = {name: sys.modules.pop(name) for name in gi_names}
    finder = _NoGI()
    sys.meta_path.insert(0, finder)
    try:
        loader = importlib.machinery.SourceFileLoader("appstore_no_gi", SCRIPT)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        sys.meta_path.remove(finder)
        for name in [name for name in sys.modules if name == "gi" or name.startswith("gi.")]:
            del sys.modules[name]
        sys.modules.update(saved)


class AppStorePureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_appstore_without_gi()

    def test_module_loads_without_gi(self):
        self.assertFalse(self.mod.GI_AVAILABLE)
        self.assertTrue(callable(self.mod.format_transaction_list))
        self.assertTrue(callable(self.mod.make_search_app))
        self.assertTrue(callable(self.mod.select_apps_for_view))

    def test_format_transaction_list_shows_every_package(self):
        names = ["firefox", "libx11", "gtk3", "mesa-libGL", "zsh"]
        text = self.mod.format_transaction_list(names, "firefox")
        for name in names:
            self.assertIn("- %s" % name, text)
        self.assertIn("La rimozione di firefox comporta", text)
        self.assertIn("Verranno richiesti privilegi di amministratore.", text)
        self.assertIn("Totale: 5 pacchetti.", text)
        self.assertEqual(text.count("\n- "), len(names))

    def test_format_transaction_list_keeps_long_transactions(self):
        names = ["pkg-%02d" % index for index in range(60)]
        text = self.mod.format_transaction_list(names, "meta-app")
        for name in names:
            self.assertIn("- %s" % name, text)
        self.assertIn("Totale: 60 pacchetti.", text)
        self.assertEqual(text.count("\n- "), 60)

    def test_format_transaction_list_falls_back_to_package(self):
        text = self.mod.format_transaction_list([], "firefox")
        self.assertIn("- firefox", text)
        self.assertIn("Totale: 1 pacchetto.", text)

    def test_format_transaction_list_skips_blank_names(self):
        text = self.mod.format_transaction_list(["firefox", "", "  "], "firefox")
        self.assertEqual(text.count("\n- "), 1)
        self.assertIn("Totale: 1 pacchetto.", text)

    def test_make_search_app_maps_backend_tuple(self):
        app = self.mod.make_search_app(
            ("Firefox", "Browser web", "firefox"),
            {"firefox": "160.0_1"},
            {"firefox"},
        )
        self.assertEqual(app["id"], "firefox")
        self.assertEqual(app["name"], "Firefox")
        self.assertEqual(app["summary"], "Browser web")
        self.assertEqual(app["source"], self.mod.SOURCE_SYSTEM)
        self.assertTrue(app["installed"])
        self.assertTrue(app["protected"])
        self.assertEqual(app["version"], "160.0_1")
        self.assertIn(app["category"], self.mod.CATEGORIES)
        self.assertIsNone(app["icon_path"])
        self.assertIsNone(app["entry"])

    def test_make_search_app_not_installed_is_not_protected(self):
        app = self.mod.make_search_app(("mpv", "Lettore video", "mpv"), {}, set())
        self.assertFalse(app["installed"])
        self.assertFalse(app["protected"])
        self.assertEqual(app["version"], "")

    def test_make_search_app_tolerates_short_result(self):
        app = self.mod.make_search_app(("solo-nome",))
        self.assertEqual(app["id"], "solo-nome")
        self.assertEqual(app["summary"], "")
        self.assertFalse(app["installed"])

    def test_search_results_never_enter_catalog(self):
        catalog = [
            {"id": "firefox", "name": "Firefox", "category": "Internet", "installed": True},
            {"id": "mpv", "name": "mpv", "category": "Media", "installed": False},
        ]
        search = [
            {"id": "htop", "name": "htop", "category": "Sistema", "installed": False},
            {"id": "code-oss", "name": "Code", "category": "Sviluppo", "installed": False},
        ]
        catalog_ids = [app["id"] for app in catalog]
        view = self.mod.select_apps_for_view(
            catalog, search, nav="installate", category="Sistema", query="ignored"
        )
        self.assertEqual([app["id"] for app in view], ["code-oss", "htop"])
        self.assertEqual([app["id"] for app in catalog], catalog_ids)
        self.assertFalse(any(app["id"] in catalog_ids for app in view))

    def test_catalog_views_when_no_search_is_active(self):
        catalog = [
            {"id": "firefox", "name": "Firefox", "category": "Internet", "installed": True},
            {"id": "mpv", "name": "mpv", "category": "Media", "installed": False},
        ]
        installed = self.mod.select_apps_for_view(catalog, None, nav="installate")
        self.assertEqual([app["id"] for app in installed], ["firefox"])
        categorized = self.mod.select_apps_for_view(
            catalog, None, nav="categorie", category="Media"
        )
        self.assertEqual([app["id"] for app in categorized], ["mpv"])
        self.assertEqual(len(self.mod.select_apps_for_view(catalog, [])), 0)


class AppStoreFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_appstore_without_gi()

    def setUp(self):
        self.store = object.__new__(self.mod.HyleOSStore)
        self.store._search_token = 7
        self.store._search_query = "firefox"
        self.store._search_results = None
        self.store._search_pending = False
        self.store.xbps_versions = {}
        self.store.populate_grid = lambda: None

    def test_stale_search_response_is_discarded(self):
        self.assertFalse(
            self.store.on_search_results(6, "firefox", [("Firefox", "Browser", "firefox")])
        )
        self.assertIsNone(self.store._search_results)

    def test_stale_query_is_discarded_even_with_current_token(self):
        self.assertFalse(
            self.store.on_search_results(7, "chromium", [("Chromium", "Browser", "chromium")])
        )
        self.assertIsNone(self.store._search_results)

    def test_current_search_response_is_accepted(self):
        backend = self.mod.xbps_backend
        self.mod.xbps_backend = type(
            "FakeBackend", (), {"is_protected": staticmethod(lambda pkg: pkg == "firefox")}
        )
        try:
            self.store._search_pending = True
            self.assertFalse(
                self.store.on_search_results(
                    7, "firefox", [("Firefox", "Browser web", "firefox")]
                )
            )
            self.assertEqual([app["id"] for app in self.store._search_results], ["firefox"])
            self.assertTrue(self.store._search_results[0]["protected"])
            self.assertFalse(self.store._search_pending)
        finally:
            self.mod.xbps_backend = backend

    def test_stale_debounce_does_not_start_worker(self):
        self.assertEqual(self.store._search_debounced("firefox", 6), False)
        self.assertIsNone(self.store._search_results)

    def test_remove_preview_error_does_not_start_action(self):
        dialogs = []
        self.store._show_dialog = lambda title, message: dialogs.append((title, message))
        self.store._start_action = lambda action: self.fail("azione avviata senza conferma")
        app = {"id": "firefox", "source": self.mod.SOURCE_SYSTEM}
        self.store.detail_app = app
        self.store.on_remove_preview_ready(app, False, [], "exit 6: protetti")
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0][0], "Anteprima rimozione non disponibile")

    def test_remove_preview_success_uses_full_list(self):
        captured = []
        self.store._show_confirm_remove_dialog = lambda names, text: captured.append((names, text))
        app = {"id": "firefox", "source": self.mod.SOURCE_SYSTEM}
        self.store.detail_app = app
        names = ["firefox"] + ["dip-%02d" % index for index in range(40)]
        self.store.on_remove_preview_ready(app, True, names, "")
        self.assertEqual(captured[0][0], names)
        for name in names:
            self.assertIn("- %s" % name, captured[0][1])
        self.assertIn("Totale: 41 pacchetti.", captured[0][1])

    def test_remove_preview_empty_list_uses_package_id(self):
        captured = []
        self.store._show_confirm_remove_dialog = lambda names, text: captured.append((names, text))
        app = {"id": "firefox", "source": self.mod.SOURCE_SYSTEM}
        self.store.detail_app = app
        self.store.on_remove_preview_ready(app, True, [], "")
        self.assertEqual(captured[0][0], ["firefox"])

    def test_appimage_remove_stays_direct(self):
        started = []
        self.store.detail_app = {"id": "obsidian", "source": self.mod.SOURCE_APPIMAGE}
        self.store._start_action = lambda action: started.append(action)
        self.store.on_remove_clicked(None)
        self.assertEqual(started, ["remove"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
