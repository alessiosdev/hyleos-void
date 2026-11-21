#!/usr/bin/env python3
"""Rilevamento e selezione dei dischi.

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


class DiskMixin:
    def setup_page_disk(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(28)
        page.set_margin_bottom(28)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label="Seleziona il Disco di Destinazione")
        title.add_css_class("accent-title")
        title.set_xalign(0)
        page.append(title)

        banner = Gtk.Label(
            label="⚠️ ATTENZIONE: L'installazione cancellerà l'intero disco selezionato e vi installerà Hyle OS con partizionamento GPT/EFI."
        )
        banner.add_css_class("danger-banner")
        banner.set_wrap(True)
        page.append(banner)

        self.disk_group = Adw.PreferencesGroup()
        self.disk_group.set_title("Dischi disponibili")
        page.append(self.disk_group)

        self.excluded_group = Adw.PreferencesGroup()
        self.excluded_group.set_title("Dischi esclusi")
        self.excluded_group.set_description(
            "Il supporto da cui è avviata la live non può essere formattato mentre è in uso."
        )
        self.excluded_group.set_visible(False)
        page.append(self.excluded_group)
        self._excluded_rows = []
        self.disk_warnings = {}

        # Navigation Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.END)

        refresh_btn = Gtk.Button(label="Aggiorna dischi")
        refresh_btn.connect("clicked", lambda b: self.refresh_disks())
        btn_box.append(refresh_btn)

        back_btn = Gtk.Button(label="← Indietro")
        back_btn.connect("clicked", lambda b: self.stack.set_visible_child_name("user"))
        btn_box.append(back_btn)

        next_btn = Gtk.Button(label="Avanti →")
        next_btn.add_css_class("suggested-action")
        next_btn.connect("clicked", self.proceed_to_confirm)
        btn_box.append(next_btn)
        page.append(btn_box)

        self.stack.add_named(page, "disk")

    @staticmethod
    def _disk_base_name(path):
        name = os.path.basename((path or "").strip())
        if name.startswith("nvme") or name.startswith("mmcblk"):
            return re.sub(r'p\d+$', '', name)
        return re.sub(r'\d+$', '', name)

    def is_live_boot_device(self, disk):
        # Non trasformare un errore di rilevamento in un "disco sicuro".
        return self.disk_manager.is_live_disk(disk)

    def refresh_disks(self):
        # Remove old rows
        for row in self._disk_rows:
            try:
                self.disk_group.remove(row)
            except Exception:
                pass
        self._disk_rows = []
        for row in getattr(self, "_excluded_rows", []):
            try:
                self.excluded_group.remove(row)
            except Exception:
                pass
        self._excluded_rows = []
        self.selected_disk = None
        self.available_disks = []
        self.disk_identities = {}
        self.disk_warnings = {}

        try:
            scan = self.disk_manager.live_scan()
            live_disks = set(scan.get("confirmed") or [])
            suspect_disks = set(scan.get("suspect") or []) - live_disks
            live_reasons = scan.get("reasons") or {}
            devices = self.disk_manager.list_disks()
        except Exception as e:
            self.log(f"refresh_disks: controlli dischi non riusciti: {e}", "ERROR")
            self.show_error(f"Non posso verificare in sicurezza i dischi disponibili:\n{e}\nNessun disco verrà mostrato.")
            return

        if not devices:
            self.show_error("Impossibile rilevare i dischi.\nAssicurati che il controller SATA/NVMe sia in modalità AHCI (non RAID/RST).")
            return

        live_bases = {os.path.basename(p) for p in live_disks}
        suspect_bases = {os.path.basename(p) for p in suspect_disks}
        excluded = []

        first_check = None
        for dev in devices:
            name = dev.get("name")
            size = dev.get("size_display") or dev.get("size")
            model = dev.get("model", "Disco generico") or "Disco generico"
            dev_path = dev.get("path")
            base = os.path.basename(dev_path or "")

            if not dev_path:
                continue
            # Escludiamo SOLO il supporto da cui e' partita la live: quello non
            # puo' essere formattato mentre e' in uso.
            if base in live_bases:
                reason = (live_reasons.get(dev_path) or live_reasons.get(f"/dev/{base}")
                          or "supporto di avvio della live")
                self.log(f"refresh_disks: escludo {dev_path}: {reason}", "INFO")
                excluded.append((model, dev_path, reason))
                continue

            if dev.get("ro"):
                reason = "disco in sola lettura (write-protect o lettore SD bloccato)"
                self.log(f"refresh_disks: escludo {dev_path}: {reason}", "WARN")
                excluded.append((model, dev_path, reason))
                continue

            warnings = []
            try:
                current = self.disk_manager.inspect_disk(dev_path)
                mounted = self.disk_manager.disk_mounts(dev_path)
                if mounted:
                    # Non lo nascondiamo: molti desktop auto-montano le USB.
                    # Le partizioni verranno smontate al momento dell'installazione.
                    where = ", ".join(f"{p} su {m}" for p, m in mounted)
                    warnings.append(f"partizioni montate ({where}), verranno smontate")
                    self.log(f"refresh_disks: {dev_path} ha partizioni montate: {where}", "WARN")
                if base in suspect_bases:
                    warnings.append("possibile residuo di supporto live: verifica che non sia la chiavetta di avvio")
                    self.log(f"refresh_disks: {dev_path} classificato come sospetto supporto live", "WARN")
                self.disk_identities[dev_path] = self.disk_manager.identity(current)
                if warnings:
                    self.disk_warnings[dev_path] = warnings
            except Exception as e:
                self.log(f"refresh_disks: impossibile verificare {dev_path}; escluso: {e}", "ERROR")
                excluded.append((model, dev_path, f"verifica fallita: {e}"))
                continue

            row = Adw.ActionRow()
            row.set_title(f"{model} ({dev_path})")
            serial = (dev.get("serial") or dev.get("wwn") or "identificativo non fornito").strip()
            subtitle = f"Dimensione: {size} | ID: {serial}"
            if warnings:
                subtitle += " | ⚠ " + "; ".join(warnings)
            row.set_subtitle(subtitle)

            check = Gtk.CheckButton()
            if first_check is None:
                first_check = check
            else:
                check.set_group(first_check)

            def make_handler(path):
                return lambda btn: self.on_disk_selected(path, btn.get_active())

            check.connect("toggled", make_handler(dev_path))
            check.set_valign(Gtk.Align.CENTER)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            self.disk_group.add(row)
            self._disk_rows.append(row)
            self.available_disks.append(dev_path)

        # Pannello "Dischi esclusi": rende visibile il motivo invece di far
        # sparire il disco senza spiegazioni.
        for model, dev_path, reason in excluded:
            row = Adw.ActionRow()
            row.set_title(f"{model} ({dev_path})")
            row.set_subtitle(f"Escluso: {reason}")
            row.set_sensitive(False)
            self.excluded_group.add(row)
            self._excluded_rows.append(row)
        try:
            self.excluded_group.set_visible(bool(self._excluded_rows))
        except Exception:
            pass

        if not self.available_disks:
            detail = "\n".join(f"• {p} — {r}" for _m, p, r in excluded)
            msg = ("Nessun disco idoneo trovato.\n"
                   "Verifica nel BIOS/UEFI che il controller sia in modalità AHCI e non RAID/RST.")
            if detail:
                msg += "\n\nDischi esclusi:\n" + detail
            if any("supporto live" in r or "live" in r for _m, _p, r in excluded):
                msg += ("\n\nSe il disco che vuoi usare è quello da cui hai avviato la live, "
                        "non può essere formattato mentre è in uso: avvia la live da un'altra "
                        "chiavetta e riprova.")
            if any("sola lettura" in r for _m, _p, r in excluded):
                msg += ("\n\nSe stai usando una scheda SD, controlla che la linguetta di "
                        "protezione del lettore non sia in posizione 'lock'.")
            self.show_error(msg)

    def on_disk_selected(self, path, is_active):
        if is_active:
            self.selected_disk = path

    def proceed_to_confirm(self, btn):
        if not self.selected_disk:
            self.show_error("Nessun disco fisso idoneo selezionato.")
            return
        try:
            current = self.disk_manager.inspect_disk(self.selected_disk)
            identity = self.disk_manager.identity(current)
            if identity != self.disk_identities.get(self.selected_disk):
                raise RuntimeError("Il disco è cambiato da quando è stato rilevato. Torna indietro e aggiorna l'elenco.")
            if self.selected_disk in self.disk_manager.live_boot_disks():
                raise RuntimeError("È il dispositivo da cui è avviata la live e non può essere usato come destinazione.")
            mounted = self.disk_manager.disk_mounts(self.selected_disk)
            if mounted:
                # Verranno smontate automaticamente subito prima della formattazione.
                self.log("Disco selezionato con partizioni montate: " +
                         ", ".join(f"{p} ({m})" for p, m in mounted), "WARN")
        except Exception as e:
            self.show_error(f"Controllo del disco non superato:\n{e}")
            return
        model = (current.get("model") or "Disco generico").strip()
        size_bytes = int(current.get("size") or 0)
        size = f"{size_bytes / (1024 ** 3):.1f} GiB"
        serial = current.get("serial") or current.get("wwn") or identity.get("major_minor")
        self.sum_disk.set_title(f"Disco: {model} ({self.selected_disk})")
        subtitle = f"Dimensione: {size} | Identificativo: {serial} | VERRÀ COMPLETAMENTE CANCELLATO"
        if self.selected_disk in getattr(self, "disk_warnings", {}):
            subtitle += " | ⚠ " + "; ".join(self.disk_warnings[self.selected_disk])
        self.sum_disk.set_subtitle(subtitle)
        username = self.entry_username.get_text().strip().lower() or "(non specificato)"
        fullname = self.entry_fullname.get_text().strip()
        hostname = self.entry_hostname.get_text().strip() or "hyleos"
        keymap = self.selected_keymap
        tz = getattr(self, 'selected_timezone', 'Europe/Rome')
        loc = getattr(self, 'selected_locale', 'it_IT.UTF-8')
        self.sum_user.set_title(f"Utente: {username}")
        self.sum_user.set_subtitle(f"Nome completo: {fullname}")
        self.sum_host.set_title(f"Hostname: {hostname}")
        self.sum_host.set_subtitle(f"Timezone: {tz} | Locale: {loc}")
        self.sum_keymap.set_title(f"Layout tastiera: {keymap}")
        self.sum_keymap.set_subtitle(f"Root password: {'stessa dell\'utente' if getattr(self,'root_password_same',True) else 'gestita'}")
        try:
            self.confirm_check.set_active(False)
            if hasattr(self, '_install_btn'):
                self._install_btn.set_sensitive(False)
        except Exception:
            pass
        self.stack.set_visible_child_name("confirm")

