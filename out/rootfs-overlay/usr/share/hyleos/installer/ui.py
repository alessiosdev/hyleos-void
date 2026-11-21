#!/usr/bin/env python3
"""Pagine GTK dell'installer.

Estratto da installer.py (punto 8 della checklist): il codice è lo stesso,
spostato in un modulo per poterlo leggere e modificare senza toccare la UI.
"""
import os
import re
import json
import shlex
import subprocess
import threading
import time
import glob
import traceback
import shutil
import uuid
from datetime import datetime

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio

from installer.core.disk_manager import DiskManager
from installer.core.validation import (
    validate_username, validate_fullname, validate_hostname,
    validate_password, validate_root_password, validate_timezone, validate_locale,
)


class UiMixin:
    def setup_page_welcome(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.set_margin_top(32)
        page.set_margin_bottom(32)
        page.set_margin_start(48)
        page.set_margin_end(48)
        page.set_valign(Gtk.Align.CENTER)

        # Logo
        logo_path = "/usr/share/hyleos/assets/hyleos-logo.png"
        if os.path.exists(logo_path):
            img = Gtk.Image.new_from_file(logo_path)
            img.set_pixel_size(110)
            page.append(img)

        title = Gtk.Label(label="Benvenuto in Hyle OS")
        title.add_css_class("accent-title")
        page.append(title)

        subtitle = Gtk.Label(
            label="Questa procedura guidata installerà Hyle OS (Hyprland + DankMaterialShell)\nsul disco fisso di questo computer."
        )
        subtitle.set_justify(Gtk.Justification.CENTER)
        page.append(subtitle)

        # Keyboard selector
        group = Adw.PreferencesGroup()
        group.set_title("Configurazione Iniziale")
        group.set_description("Seleziona la disposizione della tua tastiera:")

        self.keymap_combo = Adw.ComboRow()
        self.keymap_combo.set_title("Layout Tastiera")
        keymaps = ["Italiano (it)", "English US (us)", "Deutsch (de)", "Français (fr)", "Español (es)"]
        self.keymap_codes = ["it", "us", "de", "fr", "es"]
        model = Gtk.StringList.new(keymaps)
        self.keymap_combo.set_model(model)
        self.keymap_combo.set_selected(0)
        group.add(self.keymap_combo)
        page.append(group)

        # Next Button
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_box.set_halign(Gtk.Align.END)
        next_btn = Gtk.Button(label="Avanti →")
        next_btn.add_css_class("suggested-action")
        next_btn.set_size_request(130, 42)
        next_btn.connect("clicked", lambda b: self.go_to_user_page())
        btn_box.append(next_btn)
        page.append(btn_box)

        self.stack.add_named(page, "welcome")

    def go_to_user_page(self):
        sel_idx = self.keymap_combo.get_selected()
        self.selected_keymap = self.keymap_codes[sel_idx]
        self.stack.set_visible_child_name("user")

    def setup_page_user(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(28)
        page.set_margin_bottom(28)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label="Crea il tuo Account")
        title.add_css_class("accent-title")
        title.set_xalign(0)
        page.append(title)

        group = Adw.PreferencesGroup()
        group.set_title("Dati Utente e Computer")

        self.entry_fullname = Adw.EntryRow(title="Nome Completo")
        self.entry_fullname.set_text("Utente Hyle")
        group.add(self.entry_fullname)

        self.entry_username = Adw.EntryRow(title="Nome Utente (login)")
        self.entry_username.set_text("")
        group.add(self.entry_username)

        self.entry_hostname = Adw.EntryRow(title="Nome del Computer (Hostname)")
        self.entry_hostname.set_text("hyleos-pc")
        group.add(self.entry_hostname)

        self.entry_password = Adw.PasswordEntryRow(title="Password")
        group.add(self.entry_password)

        self.entry_password_confirm = Adw.PasswordEntryRow(title="Conferma Password")
        group.add(self.entry_password_confirm)

        self.entry_root_password = Adw.PasswordEntryRow(title="Password root")
        self.entry_root_password.set_visible(False)
        group.add(self.entry_root_password)

        self.entry_root_password_confirm = Adw.PasswordEntryRow(title="Conferma Password root")
        self.entry_root_password_confirm.set_visible(False)
        group.add(self.entry_root_password_confirm)

        self.root_same_check = Adw.SwitchRow(title="Usa la stessa password per root")
        self.root_same_check.set_active(True)
        self.root_same_check.connect("notify::active", self.on_root_same_changed)
        group.add(self.root_same_check)

        loc_group = Adw.PreferencesGroup()
        loc_group.set_title("Localizzazione")
        self.entry_timezone = Adw.EntryRow(title="Timezone (es. Europe/Rome)")
        self.entry_timezone.set_text("Europe/Rome")
        loc_group.add(self.entry_timezone)
        self.entry_locale = Adw.EntryRow(title="Locale (es. it_IT.UTF-8)")
        self.entry_locale.set_text("it_IT.UTF-8")
        loc_group.add(self.entry_locale)
        page.append(group)
        page.append(loc_group)

        # Navigation Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.END)

        back_btn = Gtk.Button(label="← Indietro")
        back_btn.connect("clicked", lambda b: self.stack.set_visible_child_name("welcome"))
        btn_box.append(back_btn)

        next_btn = Gtk.Button(label="Avanti →")
        next_btn.add_css_class("suggested-action")
        next_btn.connect("clicked", self.validate_user_and_proceed)
        btn_box.append(next_btn)
        page.append(btn_box)

        self.stack.add_named(page, "user")

    def go_to_disk_page(self, *args):
        try:
            self.refresh_disks()
        except Exception as e:
            self.log(f"go_to_disk_page refresh_disks error: {e}")
        self.stack.set_visible_child_name("disk")

    def validate_user_and_proceed(self, btn):
        ok, username = validate_username(self.entry_username.get_text())
        if not ok:
            self.show_error(username)
            return

        ok, fullname = validate_fullname(self.entry_fullname.get_text())
        if not ok:
            self.show_error(fullname)
            return

        ok, hostname = validate_hostname(self.entry_hostname.get_text())
        if not ok:
            self.show_error(hostname)
            return

        password = self.entry_password.get_text()
        confirm = self.entry_password_confirm.get_text()
        ok, msg = validate_password(password, confirm)
        if not ok:
            self.show_error(msg)
            return

        root_same = self.root_same_check.get_active()
        if root_same:
            root_password = None
        else:
            rp = self.entry_root_password.get_text()
            rpc = self.entry_root_password_confirm.get_text()
            ok, msg = validate_root_password(rp, rpc)
            if not ok:
                self.show_error(msg)
                return
            root_password = rp

        ok, tz = validate_timezone(self.entry_timezone.get_text())
        if not ok:
            self.show_error(tz)
            return

        ok, loc = validate_locale(self.entry_locale.get_text())
        if not ok:
            self.show_error(loc)
            return

        self.selected_timezone = tz
        self.selected_locale = loc
        self.root_password_same = root_same
        self.root_password = root_password

        try:
            self.refresh_disks()
        except Exception as e:
            self.log(f"refresh_disks on enter disk error: {e}")
        self.stack.set_visible_child_name("disk")

    def on_root_same_changed(self, row, _pspec=None):
        show = not row.get_active()
        self.entry_root_password.set_visible(show)
        self.entry_root_password_confirm.set_visible(show)


    def setup_page_confirm(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.set_margin_top(28)
        page.set_margin_bottom(28)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label="Riepilogo e Conferma Installazione")
        title.add_css_class("accent-title")
        title.set_xalign(0)
        page.append(title)

        group = Adw.PreferencesGroup()
        group.set_title("Dettagli dell'installazione")

        self.sum_disk = Adw.ActionRow(title="Disco di destinazione (verrà formattato interamente)")
        group.add(self.sum_disk)

        self.sum_user = Adw.ActionRow(title="Nuovo Utente")
        group.add(self.sum_user)

        self.sum_host = Adw.ActionRow(title="Nome Computer (Hostname)")
        group.add(self.sum_host)

        self.sum_keymap = Adw.ActionRow(title="Layout Tastiera")
        group.add(self.sum_keymap)

        page.append(group)

        # Conferma esplicita prima di cancellare il disco
        self.confirm_check = Gtk.CheckButton(
            label="Confermo che il disco selezionato verrà completamente cancellato"
        )
        self.confirm_check.set_halign(Gtk.Align.START)
        page.append(self.confirm_check)

        # Action Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        btn_box.set_halign(Gtk.Align.END)

        back_btn = Gtk.Button(label="← Modifica")
        back_btn.connect("clicked", lambda b: self.stack.set_visible_child_name("disk"))
        btn_box.append(back_btn)

        install_btn = Gtk.Button(label="Installa Hyle OS Ora")
        install_btn.add_css_class("destructive-action")
        install_btn.set_size_request(180, 44)
        install_btn.set_sensitive(False)
        install_btn.connect("clicked", self.start_installation)
        self._install_btn = install_btn
        self.confirm_check.connect(
            "toggled", lambda c: install_btn.set_sensitive(c.get_active())
        )
        btn_box.append(install_btn)
        page.append(btn_box)

        self.stack.add_named(page, "confirm")

    def setup_page_progress(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.set_margin_top(48)
        page.set_margin_bottom(48)
        page.set_margin_start(64)
        page.set_margin_end(64)
        page.set_valign(Gtk.Align.CENTER)

        self.prog_spinner = Gtk.Spinner()
        self.prog_spinner.set_size_request(48, 48)
        page.append(self.prog_spinner)

        self.prog_title = Gtk.Label(label="Installazione in corso...")
        self.prog_title.add_css_class("accent-title")
        page.append(self.prog_title)

        self.prog_bar = Gtk.ProgressBar()
        self.prog_bar.set_show_text(True)
        self.prog_bar.set_fraction(0.0)
        page.append(self.prog_bar)

        self.prog_status = Gtk.Label(label="Inizializzazione...")
        page.append(self.prog_status)

        self.done_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.done_btn_box.set_halign(Gtk.Align.CENTER)
        self.done_btn_box.set_visible(False)

        reboot_btn = Gtk.Button(label="Riavvia il Computer")
        reboot_btn.add_css_class("suggested-action")
        reboot_btn.set_size_request(160, 42)
        reboot_btn.connect("clicked", lambda b: subprocess.run(["reboot"]))
        self.done_btn_box.append(reboot_btn)

        close_btn = Gtk.Button(label="Chiudi e rimani in Live")
        close_btn.connect("clicked", lambda b: self.close())
        self.done_btn_box.append(close_btn)
        page.append(self.done_btn_box)

        self.stack.add_named(page, "progress")

    def show_error(self, msg, fatal=False):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Attenzione",
            body=msg
        )
        dialog.add_response("ok", "OK")
        if fatal:
            dialog.connect("response", lambda d, r: self.get_application().quit())
        dialog.present()

    def update_progress(self, fraction, text):
        self.prog_bar.set_fraction(fraction)
        self.prog_status.set_text(text)

