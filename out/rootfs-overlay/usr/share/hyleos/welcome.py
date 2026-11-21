#!/usr/bin/env python3
"""
Hyle Welcome - Schermata di benvenuto e guida iniziale per Hyle OS
"""

import sys
import os
import json
import shutil
import subprocess
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

class WelcomeWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("Benvenuto in Hyle OS")
        self.set_default_size(780, 540)
        self.set_resizable(False)

        # Stile CSS Material You
        css = """
        .welcome-title { font-size: 24px; font-weight: 800; color: #a08cdc; }
        .welcome-sub { font-size: 14px; color: #b0a8c8; }
        .card-btn { padding: 12px; border-radius: 12px; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode('utf-8'))
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Layout Principale
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # HeaderBar
        header = Adw.HeaderBar()
        main_box.append(header)

        # Contenuto a scorrimento
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        main_box.append(scroller)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(32)
        content.set_margin_end(32)
        scroller.set_child(content)

        # Intestazione con Logo
        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        top_box.set_halign(Gtk.Align.CENTER)
        
        logo_path = "/usr/share/hyleos/assets/hyleos-logo.png"
        if os.path.exists(logo_path):
            img = Gtk.Image.new_from_file(logo_path)
            img.set_pixel_size(64)
            top_box.append(img)

        txt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        t_label = Gtk.Label(label="Benvenuto in Hyle OS 1.0")
        t_label.add_css_class("welcome-title")
        t_label.set_xalign(0)
        s_label = Gtk.Label(label="Void Linux + Hyprland + DankMaterialShell (Edizione Rolling)")
        s_label.add_css_class("welcome-sub")
        s_label.set_xalign(0)
        txt_box.append(t_label)
        txt_box.append(s_label)
        top_box.append(txt_box)
        content.append(top_box)

        # Griglia Azioni Rapide
        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        grid.set_column_homogeneous(True)
        content.append(grid)

        # 1. Installa OS (o Stato)
        is_live = os.path.exists("/mnt/target") or os.path.exists("/usr/bin/hyleos-installer")
        btn_install = self._create_card(
            "Installa sul Disco",
            "Avvia la procedura guidata per installare Hyle OS in modo permanente.",
            "media-optical-symbolic",
            self._action_installer
        )
        grid.attach(btn_install, 0, 0, 1, 1)

        # 2. Hyle Store
        btn_store = self._create_card(
            "Esplora Software",
            "Installa applicazioni da XBPS e AppImage",
            "software-properties-symbolic",
            self._action_store
        )
        grid.attach(btn_store, 1, 0, 1, 1)

        # 3. Scorciatoie da Tastiera
        btn_keys = self._create_card(
            "Scorciatoie Tastiera",
            "Impara a usare il gestore a finestre Hyprland e i comandi rapidi.",
            "input-keyboard-symbolic",
            self._action_shortcuts
        )
        grid.attach(btn_keys, 0, 1, 1, 1)

        # 4. Aggiorna Sistema
        btn_update = self._create_card(
            "Aggiorna Sistema (hyle)",
            "Aggiorna i pacchetti XBPS e le app AppImage con un clic",
            "view-refresh-symbolic",
            self._action_update
        )
        grid.attach(btn_update, 1, 1, 1, 1)

        # Modalità finestre (mouse-first): la scelta è persistente e viene
        # applicata subito da hyprland senza riavviare la sessione.
        mode_group = Adw.PreferencesGroup()
        mode_group.set_title("Finestre")
        mode_group.set_description(
            "Scegli come devono comportarsi le finestre. Puoi cambiare idea "
            "in qualsiasi momento dal Centro di Controllo o dalle Impostazioni."
        )

        self.mode_row = Adw.ComboRow()
        self.mode_row.set_title("Modalità finestre")
        self.mode_row.set_subtitle(
            "Tiling automatico oppure finestre libere, spostabili e "
            "ridimensionabili con il mouse"
        )
        mode_model = Gtk.StringList.new(["Tiling automatico", "Finestre libere"])
        self.mode_row.set_model(mode_model)
        self.mode_row.set_selected(1 if self._read_window_mode() == "floating" else 0)
        self.mode_row.connect("notify::selected", self._on_window_mode_changed)
        mode_group.add(self.mode_row)
        content.append(mode_group)

        # Barra inferiore: Toggle "Mostra all'avvio"
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        footer.set_margin_top(12)

        self.check_autostart = Gtk.CheckButton(label="Mostra questa schermata al prossimo avvio")
        autostart_file = os.path.expanduser("~/.config/autostart/org.hyleos.welcome.desktop")
        self.check_autostart.set_active(os.path.exists(autostart_file))
        self.check_autostart.connect("toggled", self._on_autostart_toggled)
        footer.append(self.check_autostart)

        content.append(footer)

    def _create_card(self, title, subtitle, icon_name, callback):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("card")
        box.add_css_class("card-btn")

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(32)
        icon.set_valign(Gtk.Align.CENTER)
        box.append(icon)

        t_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t_box.set_hexpand(True)
        lbl_t = Gtk.Label(label=title)
        lbl_t.set_xalign(0)
        lbl_t.set_markup(f"<b>{title}</b>")
        lbl_s = Gtk.Label(label=subtitle)
        lbl_s.set_xalign(0)
        lbl_s.set_wrap(True)
        lbl_s.set_max_width_chars(32)
        lbl_s.add_css_class("dim-label")
        t_box.append(lbl_t)
        t_box.append(lbl_s)
        box.append(t_box)

        btn = Gtk.Button()
        btn.set_child(box)
        btn.connect("clicked", lambda b: callback())
        return btn

    def _action_installer(self):
        if os.path.exists("/usr/bin/hyleos-installer"):
            subprocess.Popen(["/usr/bin/hyleos-installer"])

    def _action_store(self):
        if os.path.exists("/usr/share/hyleos/appstore.py"):
            subprocess.Popen(["python3", "/usr/share/hyleos/appstore.py"])

    def _action_update(self):
        subprocess.Popen(["kitty", "-e", "sh", "-c", "hyle update; echo ''; read -p 'Premi Invio per chiudere...'"])

    def _action_shortcuts(self):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Scorciatoie Rapide Hyprland",
            body=(
                "• SUPER + INVIO: Terminale Kitty\n"
                "• SUPER + B: Browser Firefox\n"
                "• SUPER + E: File Manager Nautilus\n"
                "• SUPER + A: Hyle App Store\n"
                "• SUPER + SPAZIO: Ricerca Applicazioni (Spotlight)\n"
                "• SUPER + C: Centro di Controllo (Wi-Fi, Bluetooth, Audio)\n"
                "• SUPER + Q: Chiudi Finestra attiva\n"
                "• SUPER + V: Rendi finestra fluttuante\n"
                "• SUPER + F: Schermo intero\n"
                "• STAMP / SHIFT+STAMP: Cattura schermata\n\n"
                "Mouse: trascina la barra del titolo per spostare una finestra "
                "libera, trascina i bordi per ridimensionarla. Clic destro sul "
                "desktop per il menu rapido."
            )
        )
        dialog.add_response("ok", "Capito")
        dialog.present()

    def _window_mode_path(self):
        base = os.environ.get("XDG_CONFIG_HOME")
        if not base:
            base = os.path.join(os.path.expanduser("~"), ".config")
        return os.path.join(base, "hyleos", "window-mode.json")

    def _read_window_mode(self):
        try:
            with open(self._window_mode_path(), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return "tiling"
        mode = data.get("mode") if isinstance(data, dict) else None
        return mode if mode in ("tiling", "floating") else "tiling"

    def _write_window_mode(self, mode):
        helper = "/usr/bin/hyleos-window-mode"
        if os.path.exists(helper):
            try:
                subprocess.run([helper, "set", mode], check=False)
                return
            except OSError:
                pass

        # Fallback: scrittura diretta se l'helper non è disponibile.
        path = self._window_mode_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "mode": mode}, handle, indent=2)
                handle.write("\n")
        except OSError:
            pass

    def _on_window_mode_changed(self, row, _pspec):
        self._write_window_mode("floating" if row.get_selected() == 1 else "tiling")

    def _on_autostart_toggled(self, check):
        autostart_dir = os.path.expanduser("~/.config/autostart")
        autostart_file = os.path.join(autostart_dir, "org.hyleos.welcome.desktop")
        os.makedirs(autostart_dir, exist_ok=True)
        if check.get_active():
            src = "/usr/share/applications/org.hyleos.welcome.desktop"
            if os.path.exists(src):
                shutil.copy(src, autostart_file)
        else:
            if os.path.exists(autostart_file):
                os.remove(autostart_file)

class WelcomeApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.hyleos.welcome",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = WelcomeWindow(application=self)
        win.present()

if __name__ == "__main__":
    app = WelcomeApp()
    app.run(sys.argv)
