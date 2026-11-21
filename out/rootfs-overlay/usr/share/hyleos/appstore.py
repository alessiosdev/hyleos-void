#!/usr/bin/env python3
"""HyleOS Store: app desktop da XBPS (prioritario) e AppImage utente.

Interfaccia GTK4 pura, mouse-first, coerente con lo stile DMS. Le operazioni
lunghe girano in thread e aggiornano la UI solo tramite GLib.idle_add.
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk
import os
import re
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import xbps_backend
except Exception:  # pragma: no cover - lo Store resta usabile senza XBPS
    xbps_backend = None

try:
    import appimage_backend
except Exception:  # pragma: no cover - lo Store resta usabile senza AppImage
    appimage_backend = None

CATEGORIES = [
    "Sistema",
    "Sviluppo",
    "Internet",
    "Grafica",
    "Media",
    "Giochi",
    "Ufficio",
    "Altro",
]

CATEGORY_ALIASES = {
    "system": "Sistema",
    "sistema": "Sistema",
    "utility": "Sistema",
    "utilità": "Sistema",
    "development": "Sviluppo",
    "sviluppo": "Sviluppo",
    "internet": "Internet",
    "network": "Internet",
    "graphics": "Grafica",
    "grafica": "Grafica",
    "media": "Media",
    "audiovideo": "Media",
    "games": "Giochi",
    "game": "Giochi",
    "giochi": "Giochi",
    "office": "Ufficio",
    "ufficio": "Ufficio",
    "education": "Ufficio",
    "istruzione": "Ufficio",
    "other": "Altro",
    "altro": "Altro",
}

SOURCE_SYSTEM = "Sistema"
SOURCE_APPIMAGE = "AppImage"
MAX_CARDS = 200

STORE_CSS = """
.hyleos-card { padding: 12px; border-radius: 12px; }
.hyleos-card:hover { background: alpha(currentColor, 0.08); }
.hyleos-card-title { font-weight: bold; font-size: 1.05em; }
.hyleos-card-summary { font-size: 0.9em; }
.hyleos-badge {
    font-size: 0.78em;
    padding: 1px 8px;
    border-radius: 6px;
    background: alpha(currentColor, 0.10);
}
.hyleos-nav { font-weight: 600; }
.hyleos-meta { font-size: 0.9em; }
"""


def normalize_category(value):
    text = (value or "").strip().lower()
    if text in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[text]
    for key, category in CATEGORY_ALIASES.items():
        if key in text:
            return category
    return "Altro"


def guess_category(name, desc, app_id):
    """Categoria euristica per le voci XBPS, che non dichiarano categoria."""
    text = (str(name) + " " + str(desc) + " " + str(app_id)).lower()
    rules = (
        ("Giochi", ("game", "steam", "emu", "retro", "minecraft", "play", "gioch")),
        ("Sviluppo", ("code", "ide", "builder", "git", "python", "studio", "compiler", "debugger", "sviluppo")),
        ("Internet", ("browser", "firefox", "chromium", "chat", "messenger", "mail", "telegram", "internet", "rete")),
        ("Grafica", ("gimp", "inkscape", "krita", "blender", "paint", "draw", "cad", "photo", "grafica")),
        ("Media", ("vlc", "mpv", "player", "video", "audio", "music", "obs", "audacity", "media")),
        ("Ufficio", ("office", "libreoffice", "writer", "calc", "note", "ufficio")),
        ("Sistema", ("htop", "fastfetch", "grim", "clipboard", "manager", "utility", "sistema", "strument")),
    )
    for category, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return category
    return "Altro"


def _markup(value):
    return GLib.markup_escape_text(_safe_text(value))


def _safe_text(value):
    return re.sub(r"[\r\n]+", " ", str(value or "")).strip()


def _load_css():
    try:
        provider = Gtk.CssProvider()
        provider.load_from_data(STORE_CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
    except Exception:
        pass


class AppCard(Gtk.Button):
    """Card cliccabile per una singola applicazione."""

    def __init__(self, app, callback):
        super().__init__()
        self.app = app
        self.set_name("hyleos-card")
        self.add_css_class("hyleos-card")
        self.set_valign(Gtk.Align.START)
        self.set_hexpand(False)
        self.set_tooltip_text("Apri i dettagli di " + _safe_text(app.get("name")))

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        image = self._build_icon(app)
        image.set_pixel_size(40)
        image.set_valign(Gtk.Align.CENTER)
        header.append(image)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        texts.set_hexpand(True)
        title = Gtk.Label(label=_safe_text(app.get("name")))
        title.add_css_class("hyleos-card-title")
        title.set_xalign(0)
        title.set_wrap(True)
        summary = Gtk.Label(label=_safe_text(app.get("summary")) or "Nessun sommario disponibile.")
        summary.add_css_class("dim-label")
        summary.add_css_class("hyleos-card-summary")
        summary.set_xalign(0)
        summary.set_wrap(True)
        summary.set_max_width_chars(40)
        texts.append(title)
        texts.append(summary)
        header.append(texts)
        root.append(header)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        category = Gtk.Label(label=_safe_text(app.get("category")))
        category.add_css_class("dim-label")
        category.add_css_class("hyleos-meta")
        category.set_xalign(0)
        category.set_hexpand(True)
        badge = Gtk.Label(label=app.get("source", SOURCE_SYSTEM))
        badge.add_css_class("hyleos-badge")
        badge.add_css_class("dim-label")
        footer.append(category)
        footer.append(badge)
        root.append(footer)

        self.set_child(root)
        self.connect("clicked", callback, app)

    @staticmethod
    def _build_icon(app):
        candidate = app.get("icon_path")
        if candidate and os.path.isfile(candidate):
            try:
                return Gtk.Image.new_from_file(candidate)
            except Exception:
                pass
        try:
            display = Gdk.Display.get_default()
            if display is not None:
                theme = Gtk.IconTheme.get_for_display(display)
                if theme.has_icon(app.get("id", "")):
                    return Gtk.Image.new_from_icon_name(app.get("id"))
        except Exception:
            pass
        return Gtk.Image.new_from_icon_name("application-x-executable")


class HyleOSStore(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="org.hyleos.store",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.apps = []
        self.installed_xbps = set()
        self.installed_appimage = set()
        self.current_nav = "esplora"
        self.current_category = CATEGORIES[0]
        self.detail_app = None
        self._loading = False

    # ------------------------------------------------------------------
    # Costruzione UI
    # ------------------------------------------------------------------

    def do_activate(self):
        _load_css()
        win = Gtk.ApplicationWindow(application=self)
        self.win = win
        win.set_default_size(950, 650)
        win.set_resizable(True)
        win.set_title("HyleOS Store")

        header = Gtk.HeaderBar()
        win.set_titlebar(header)

        self.back_button = Gtk.Button(label="Indietro")
        self.back_button.add_css_class("flat")
        self.back_button.connect("clicked", self.on_back_clicked)
        self.back_button.set_visible(False)
        header.pack_start(self.back_button)

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_box.set_valign(Gtk.Align.CENTER)
        title_box.append(self._build_logo())
        title = Gtk.Label(label="HyleOS Store")
        title.add_css_class("title")
        title_box.append(title)
        header.set_title_widget(title_box)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Cerca tra le applicazioni...")
        self.search_entry.set_size_request(280, -1)
        self.search_entry.connect("search-changed", self.on_search_changed)
        header.pack_end(self.search_entry)

        win.set_child(self.build_ui())
        win.present()
        self.reload()

    @staticmethod
    def _build_logo():
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = (
            os.path.join(script_dir, "assets", "hyleos-logo.png"),
            os.path.join(script_dir, "hyleos-store.svg"),
            "/usr/share/hyleos/assets/hyleos-logo.png",
        )
        for path in candidates:
            if os.path.isfile(path):
                try:
                    image = Gtk.Image.new_from_file(path)
                    image.set_pixel_size(28)
                    return image
                except Exception:
                    continue
        fallback = Gtk.Image.new_from_icon_name("applications-games")
        fallback.set_pixel_size(28)
        return fallback

    def build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.sidebar.set_size_request(190, -1)
        self.sidebar.set_margin_top(12)
        self.sidebar.set_margin_start(10)
        self.sidebar.set_margin_end(10)

        self.nav_buttons = {}
        for key, label in (
            ("esplora", "Esplora"),
            ("categorie", "Categorie"),
            ("installate", "Installate"),
        ):
            button = Gtk.Button(label=label)
            button.add_css_class("flat")
            button.add_css_class("hyleos-nav")
            child = button.get_child()
            if child is not None:
                child.set_xalign(0)
            button.connect("clicked", self.on_nav_clicked, key)
            self.sidebar.append(button)
            self.nav_buttons[key] = button
        self._highlight_nav()

        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)

        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.set_hexpand(True)
        self.main_stack.set_vexpand(True)

        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        loading.set_valign(Gtk.Align.CENTER)
        loading.set_halign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        spinner.set_size_request(40, 40)
        spinner.start()
        loading_label = Gtk.Label(label="Caricamento del catalogo in corso...")
        loading_label.add_css_class("dim-label")
        loading.append(spinner)
        loading.append(loading_label)
        self.main_stack.add_named(loading, "loading")

        self.error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.error_box.set_valign(Gtk.Align.CENTER)
        self.error_box.set_halign(Gtk.Align.CENTER)
        self.error_label = Gtk.Label()
        self.error_label.set_wrap(True)
        self.error_label.set_justify(Gtk.Justification.CENTER)
        retry = Gtk.Button(label="Riprova")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda _button: self.reload())
        self.error_box.append(self.error_label)
        self.error_box.append(retry)
        self.main_stack.add_named(self.error_box, "error")

        self.grid_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.category_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.category_bar.set_margin_top(10)
        self.category_bar.set_margin_start(14)
        self.category_bar.set_margin_end(14)
        self.category_buttons = {}
        for category in CATEGORIES:
            button = Gtk.Button(label=category)
            button.add_css_class("flat")
            button.connect("clicked", self.on_category_clicked, category)
            self.category_bar.append(button)
            self.category_buttons[category] = button
        self._highlight_category()

        self.grid = Gtk.FlowBox()
        self.grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.grid.set_max_children_per_line(3)
        self.grid.set_min_children_per_line(2)
        self.grid.set_column_spacing(12)
        self.grid.set_row_spacing(12)
        self.grid.set_homogeneous(True)
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_child(self.grid)
        self.scrolled.set_margin_top(6)
        self.scrolled.set_margin_bottom(12)
        self.scrolled.set_margin_start(12)
        self.scrolled.set_margin_end(12)
        self.scrolled.set_vexpand(True)
        self.grid_page.append(self.category_bar)
        self.grid_page.append(self.scrolled)
        self.main_stack.add_named(self.grid_page, "grid")

        self.main_stack.add_named(self.build_detail(), "detail")

        root.append(self.sidebar)
        root.append(separator)
        root.append(self.main_stack)
        return root

    def build_detail(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        page.set_margin_start(32)
        page.set_margin_end(32)

        self.detail_title = Gtk.Label()
        self.detail_title.set_xalign(0)
        self.detail_title.set_wrap(True)
        page.append(self.detail_title)

        self.detail_summary = Gtk.Label()
        self.detail_summary.set_xalign(0)
        self.detail_summary.set_wrap(True)
        self.detail_summary.set_max_width_chars(90)
        self.detail_summary.add_css_class("dim-label")
        page.append(self.detail_summary)

        meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.detail_meta = Gtk.Label()
        self.detail_meta.add_css_class("dim-label")
        self.detail_meta.add_css_class("hyleos-meta")
        self.detail_meta.set_xalign(0)
        self.detail_meta.set_hexpand(True)
        self.detail_badge = Gtk.Label()
        self.detail_badge.add_css_class("hyleos-badge")
        self.detail_badge.add_css_class("dim-label")
        meta_row.append(self.detail_meta)
        meta_row.append(self.detail_badge)
        page.append(meta_row)

        self.detail_status = Gtk.Label(label="")
        self.detail_status.set_xalign(0)
        self.detail_status.add_css_class("dim-label")
        self.detail_status.set_wrap(True)
        page.append(self.detail_status)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.set_margin_top(12)
        self.install_button = Gtk.Button(label="Installa")
        self.install_button.add_css_class("suggested-action")
        self.install_button.connect("clicked", self.on_install_clicked)
        self.remove_button = Gtk.Button(label="Disinstalla")
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.connect("clicked", self.on_remove_clicked)
        self.open_button = Gtk.Button(label="Apri")
        self.open_button.connect("clicked", self.on_open_clicked)
        actions.append(self.install_button)
        actions.append(self.remove_button)
        actions.append(self.open_button)
        page.append(actions)
        return page

    # ------------------------------------------------------------------
    # Caricamento dati (thread + GLib.idle_add)
    # ------------------------------------------------------------------

    def reload(self):
        if self._loading:
            return
        self._loading = True
        self.main_stack.set_visible_child_name("loading")
        threading.Thread(target=self._async_load, daemon=True).start()

    def _async_load(self):
        apps = []
        errors = []
        xbps_installed = {}
        appimage_installed = {}

        if xbps_backend is not None:
            try:
                xbps_installed = xbps_backend.installed_packages()
                for name, desc, pkg in xbps_backend.catalog():
                    apps.append({
                        "id": pkg,
                        "name": name,
                        "summary": desc,
                        "category": guess_category(name, desc, pkg),
                        "version": xbps_installed.get(pkg, ""),
                        "source": SOURCE_SYSTEM,
                        "installed": pkg in xbps_installed,
                        "protected": bool(xbps_backend.is_protected(pkg)),
                        "icon_path": None,
                        "entry": None,
                    })
            except Exception as exc:
                errors.append("XBPS: %s" % exc)

        if appimage_backend is not None:
            try:
                entries = appimage_backend.catalog()
                appimage_installed = appimage_backend.installed()
                entries_by_id = {}
                for entry in entries:
                    app_id = entry.get("id")
                    entries_by_id[app_id] = entry
                    apps.append({
                        "id": app_id,
                        "name": entry.get("name") or app_id,
                        "summary": entry.get("summary", ""),
                        "category": normalize_category(entry.get("category")),
                        "version": entry.get("version", ""),
                        "source": SOURCE_APPIMAGE,
                        "installed": app_id in appimage_installed,
                        "protected": False,
                        "icon_path": self._local_icon_path(entry, appimage_installed.get(app_id)),
                        "entry": entry,
                    })
                for app_id, meta in appimage_installed.items():
                    if app_id in entries_by_id:
                        continue
                    apps.append({
                        "id": app_id,
                        "name": meta.get("name") or app_id,
                        "summary": meta.get("summary", ""),
                        "category": normalize_category(meta.get("category")),
                        "version": meta.get("version", ""),
                        "source": SOURCE_APPIMAGE,
                        "installed": True,
                        "protected": False,
                        "icon_path": meta.get("icon") if meta.get("icon") and os.path.isfile(meta["icon"]) else None,
                        "entry": None,
                    })
            except Exception as exc:
                errors.append("AppImage: %s" % exc)

        apps = self._prioritize_xbps(apps)
        GLib.idle_add(self.on_data_loaded, apps, xbps_installed, appimage_installed, errors)

    @staticmethod
    def _local_icon_path(entry, installed_meta):
        candidate = (installed_meta or {}).get("icon")
        if candidate and os.path.isfile(candidate):
            return candidate
        icon_url = entry.get("icon_url", "")
        if icon_url and os.path.isfile(icon_url):
            return icon_url
        return None

    @staticmethod
    def _prioritize_xbps(apps):
        """Se un'app è sia in XBPS sia nel catalogo AppImage, resta solo XBPS."""
        result = []
        seen_ids = set()
        seen_names = set()
        for source in (SOURCE_SYSTEM, SOURCE_APPIMAGE):
            for app in apps:
                if app.get("source") != source:
                    continue
                key_id = str(app.get("id", "")).casefold()
                key_name = re.sub(r"[^a-z0-9]", "", str(app.get("name", "")).casefold())
                if key_id in seen_ids or (key_name and key_name in seen_names):
                    continue
                seen_ids.add(key_id)
                if key_name:
                    seen_names.add(key_name)
                result.append(app)
        return result

    def on_data_loaded(self, apps, xbps_installed, appimage_installed, errors):
        self._loading = False
        self.apps = apps
        self.installed_xbps = set(xbps_installed)
        self.installed_appimage = set(appimage_installed)

        if not apps:
            detail = "\n".join(errors) if errors else "Nessun catalogo disponibile."
            self.error_label.set_markup(
                "<b>Nessuna applicazione disponibile.</b>\n%s" % _markup(detail)
            )
            self.main_stack.set_visible_child_name("error")
            return False

        for message in errors:
            print("[Store] %s" % message, file=sys.stderr)
        self.main_stack.set_visible_child_name("grid")
        self.populate_grid()
        self.update_detail()
        return False

    # ------------------------------------------------------------------
    # Viste e ricerca
    # ------------------------------------------------------------------

    def _highlight_nav(self):
        for key, button in self.nav_buttons.items():
            if key == self.current_nav:
                button.add_css_class("suggested-action")
            else:
                button.remove_css_class("suggested-action")

    def _highlight_category(self):
        for category, button in self.category_buttons.items():
            if category == self.current_category:
                button.add_css_class("suggested-action")
            else:
                button.remove_css_class("suggested-action")

    def on_nav_clicked(self, button, key):
        self.current_nav = key
        self._highlight_nav()
        self.back_button.set_visible(False)
        self.search_entry.set_sensitive(True)
        self.main_stack.set_visible_child_name("grid")
        self.populate_grid()

    def on_category_clicked(self, button, category):
        self.current_category = category
        self._highlight_category()
        if self.current_nav != "categorie":
            self.current_nav = "categorie"
            self._highlight_nav()
        self.populate_grid()

    def on_back_clicked(self, button):
        self.back_button.set_visible(False)
        self.search_entry.set_sensitive(True)
        self.main_stack.set_visible_child_name("grid")

    def on_search_changed(self, entry):
        if self.main_stack.get_visible_child_name() != "detail":
            self.populate_grid()

    def visible_apps(self):
        query = self.search_entry.get_text().strip().casefold()
        if self.current_nav == "installate":
            apps = [app for app in self.apps if app.get("installed")]
        elif self.current_nav == "categorie":
            apps = [app for app in self.apps if app.get("category") == self.current_category]
        else:
            apps = list(self.apps)
        if query:
            apps = [
                app for app in apps
                if query in str(app.get("name", "")).casefold()
                or query in str(app.get("summary", "")).casefold()
                or query in str(app.get("category", "")).casefold()
            ]
        return sorted(apps, key=lambda app: str(app.get("name", "")).casefold())

    def populate_grid(self):
        while True:
            child = self.grid.get_first_child()
            if child is None:
                break
            self.grid.remove(child)

        self.category_bar.set_visible(self.current_nav == "categorie")
        apps = self.visible_apps()[:MAX_CARDS]
        for app in apps:
            self.grid.append(AppCard(app, self.on_app_clicked))
        if not apps:
            empty = Gtk.Label(label="Nessuna applicazione trovata.")
            empty.add_css_class("dim-label")
            self.grid.append(empty)

    def on_app_clicked(self, button, app):
        self.detail_app = app
        self.detail_status.set_label("")
        self.detail_title.set_markup(
            "<span size='x-large' weight='bold'>%s</span>" % _markup(app.get("name"))
        )
        self.detail_summary.set_label(_safe_text(app.get("summary")) or "Nessun sommario disponibile.")
        version = _safe_text(app.get("version")) or "sconosciuta"
        self.detail_meta.set_label(
            "Versione: %s  ·  Categoria: %s" % (version, _safe_text(app.get("category")))
        )
        self.detail_badge.set_label(app.get("source", SOURCE_SYSTEM))
        self.update_detail()
        self.back_button.set_visible(True)
        self.search_entry.set_sensitive(False)
        self.main_stack.set_visible_child_name("detail")

    def update_detail(self):
        app = self.detail_app
        if app is None:
            self.install_button.set_sensitive(False)
            self.remove_button.set_sensitive(False)
            self.open_button.set_sensitive(False)
            return
        installed = bool(app.get("installed"))
        protected = bool(app.get("protected"))
        self.install_button.set_sensitive(not installed)
        self.remove_button.set_sensitive(installed and not protected)
        self.open_button.set_sensitive(installed)

    # ------------------------------------------------------------------
    # Azioni (thread + GLib.idle_add)
    # ------------------------------------------------------------------

    def on_install_clicked(self, button):
        self._start_action("install")

    def on_remove_clicked(self, button):
        self._start_action("remove")

    def on_open_clicked(self, button):
        self._start_action("open")

    def _start_action(self, action):
        app = self.detail_app
        if app is None:
            return
        self.install_button.set_sensitive(False)
        self.remove_button.set_sensitive(False)
        self.open_button.set_sensitive(False)
        self.detail_status.set_label("Operazione in corso...")
        threading.Thread(target=self._async_action, args=(action, app), daemon=True).start()

    def _async_action(self, action, app):
        try:
            if action == "install":
                self._do_install(app)
                installed = True
            elif action == "remove":
                self._do_remove(app)
                installed = False
            else:
                self._do_open(app)
                GLib.idle_add(self.on_action_finished, app, app.get("installed", True), "open")
                return False
            GLib.idle_add(self.on_action_finished, app, installed, action)
        except Exception as exc:
            message = _safe_text(exc) or "Operazione non riuscita."
            GLib.idle_add(self.on_action_failed, message)
        return False

    @staticmethod
    def _do_install(app):
        if app.get("source") == SOURCE_SYSTEM:
            if xbps_backend is None:
                raise RuntimeError("Backend XBPS non disponibile.")
            ok, message, _needs_update = xbps_backend.preflight(app["id"])
            if not ok:
                raise RuntimeError(message)
            ok, message = xbps_backend.install(app["id"])
            if not ok:
                raise RuntimeError(message)
            return
        if appimage_backend is None:
            raise RuntimeError("Backend AppImage non disponibile.")
        entry = app.get("entry")
        if not entry:
            raise RuntimeError("Voce del catalogo AppImage non disponibile.")
        appimage_backend.install(entry)

    @staticmethod
    def _do_remove(app):
        if app.get("source") == SOURCE_SYSTEM:
            if xbps_backend is None:
                raise RuntimeError("Backend XBPS non disponibile.")
            ok, message = xbps_backend.remove(app["id"])
            if not ok:
                raise RuntimeError(message)
            return
        if appimage_backend is None:
            raise RuntimeError("Backend AppImage non disponibile.")
        appimage_backend.remove(app["id"])

    @staticmethod
    def _do_open(app):
        if app.get("source") == SOURCE_APPIMAGE:
            if appimage_backend is None:
                raise RuntimeError("Backend AppImage non disponibile.")
            appimage_backend.open_app(app["id"])
            return
        info = Gio.DesktopAppInfo.new(app["id"] + ".desktop")
        if info is None:
            needle = str(app.get("name", "")).casefold()
            for candidate in Gio.AppInfo.get_all():
                if needle and needle in candidate.get_name().casefold():
                    info = candidate
                    break
        if info is None or not info.launch(None):
            raise RuntimeError("Nessuna applicazione avviabile trovata per %s." % app["id"])

    def on_action_finished(self, app, installed, action):
        app["installed"] = bool(installed)
        if app.get("source") == SOURCE_SYSTEM:
            if installed:
                self.installed_xbps.add(app["id"])
            else:
                self.installed_xbps.discard(app["id"])
        else:
            if installed:
                self.installed_appimage.add(app["id"])
            else:
                self.installed_appimage.discard(app["id"])
        self.detail_status.set_label("Operazione completata." if action != "open" else "Applicazione avviata.")
        self.update_detail()
        self.populate_grid()
        return False

    def on_action_failed(self, message):
        self.detail_status.set_label("Errore: " + message)
        self.update_detail()
        self._show_dialog("Operazione non riuscita", message)
        return False

    @staticmethod
    def _show_dialog(title, message):
        dialog = Gtk.Window(transient_for=None, modal=True, title=title)
        dialog.set_default_size(480, -1)
        dialog.set_resizable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        label = Gtk.Label(label=message)
        label.set_wrap(True)
        label.set_xalign(0)
        label.set_selectable(True)
        close = Gtk.Button(label="Chiudi")
        close.set_halign(Gtk.Align.END)
        close.connect("clicked", lambda _button: dialog.destroy())
        box.append(label)
        box.append(close)
        dialog.set_child(box)
        dialog.present()


if __name__ == "__main__":
    HyleOSStore().run(sys.argv)
