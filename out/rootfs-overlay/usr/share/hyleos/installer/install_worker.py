#!/usr/bin/env python3
"""Facciata UI del worker di installazione.

Le fasi di installazione vivono in ``installer.core.engine``; qui restano solo
il contratto usato da ``installer.py``/``ui.py``: validazione iniziale, avvio
del thread, shim dei comandi e callback verso la UI.
"""
import os
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib

from installer.core.command_runner import CommandRunner
from installer.core.engine import InstallerEngine
from installer.core.install_context import (
    InstallContext, InstallOptions, PrivilegeContext,
    TargetPaths, PartitionLayout, LogPaths, UiCallbacks,
)
from installer.core.validation import validate_username, validate_hostname


_SESSION_ENV_KEYS = (
    "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "XDG_SESSION_TYPE", "HYPRLAND_INSTANCE_SIGNATURE", "DISPLAY", "XAUTHORITY",
)


class InstallWorkerMixin:
    def _ensure_runner(self):
        """Runner condiviso con il motore: creato una volta sola per la window."""
        runner = getattr(self, "runner", None)
        if runner is None:
            runner = CommandRunner(logger=self)
            self.runner = runner
        return runner

    def start_installation(self, btn):
        self._ensure_runner()
        disk = self.selected_disk
        expected_identity = self.disk_identities.get(disk)
        if not expected_identity:
            self.show_error("Il disco non ha un'identità verificata. Aggiorna l'elenco e selezionalo di nuovo.")
            return
        ok, username = validate_username(self.entry_username.get_text())
        if not ok:
            self.show_error(username)
            return
        ok, hostname = validate_hostname(self.entry_hostname.get_text())
        if not ok:
            self.show_error(hostname)
            return
        fullname = self.entry_fullname.get_text().strip()
        password = self.entry_password.get_text()
        keymap = self.selected_keymap
        timezone = getattr(self, 'selected_timezone', 'Europe/Rome')
        locale = getattr(self, 'selected_locale', 'it_IT.UTF-8')
        root_same = getattr(self, 'root_password_same', True)
        root_password = getattr(self, 'root_password', None)
        if not root_same and root_password is None:
            self.show_error("Password root non specificata ma richiesta separata.")
            return
        try:
            btn.set_sensitive(False)
        except Exception:
            pass
        # Check disk space
        try:
            out = self.run_cmd(["lsblk", "-b", "-d", "-n", "-o", "SIZE", disk])
            size_bytes = int(out.strip())
        except Exception as e:
            self.log(f"disk size check failed: {e}", "ERROR")
            self.show_error(f"Impossibile determinare la dimensione del disco selezionato:\n{e}")
            try:
                btn.set_sensitive(True)
            except Exception:
                pass
            return
        if size_bytes < 25 * 1024 * 1024 * 1024:
            self.show_error("Spazio insufficiente.\nSono necessari almeno 25 GB sul disco selezionato.")
            try:
                btn.set_sensitive(True)
            except Exception:
                pass
            return
        self.install_in_progress = True
        # Clear log
        try:
            open(self.install_log_path, "w").close()
        except Exception:
            pass
        self.log_entries = []
        self._installer_mounts = []
        self.stack.set_visible_child_name("progress")
        self.prog_spinner.start()
        threading.Thread(target=self.run_install_worker, args=(disk, expected_identity, username, fullname, password, hostname, keymap, timezone, locale, root_same, root_password), daemon=True).start()

    def run_cmd(self, argv, check=True, input_text=None, timeout=None, max_lines=None):
        """Shim sul CommandRunner: mantiene il ritorno come stringa stdout."""
        runner = self._ensure_runner()
        kwargs = dict(input_data=input_text, check=check, timeout=timeout)
        if max_lines is None:
            proc = runner.run(argv, **kwargs)
        else:
            # Il runner ha già loggato CMD/RC; qui limitiamo solo i dettagli.
            proc = runner.run(argv, quiet=True, **kwargs)
            limit = max(0, int(max_lines))
            for label, text in (("STDOUT", proc.stdout), ("STDERR", proc.stderr)):
                lines = (text or "").splitlines()
                for line in lines[:limit]:
                    self.log(f"{label}: {line}")
                if len(lines) > limit:
                    self.log(f"{label}: ... {len(lines) - limit} righe omesse")
        return proc.stdout or ""

    def run_install_worker(self, disk, expected_identity, username, fullname, password, hostname, keymap, timezone, locale, root_same, root_password):
        options = InstallOptions(
            username=username, fullname=fullname, password=password,
            hostname=hostname, keymap=keymap, timezone=timezone, locale=locale,
            root_password_mode="same" if root_same else "separate",
            root_password=password if root_same else root_password,
        )
        privilege = PrivilegeContext(
            is_root=(os.geteuid() == 0),
            elevation=None,
            session_env={key: os.environ[key] for key in _SESSION_ENV_KEYS if key in os.environ},
        )
        ui = UiCallbacks(
            progress=lambda fraction, text: GLib.idle_add(self.update_progress, fraction, text),
            success=lambda: GLib.idle_add(self.on_install_success),
            failure=lambda message: GLib.idle_add(self.on_install_failure, message),
            finished=lambda: GLib.idle_add(setattr, self, "install_in_progress", False),
            log=lambda level, message: self.log(message, level),
        )
        ctx = InstallContext(
            options=options, privilege=privilege,
            target=TargetPaths(root="/mnt"), partitions=PartitionLayout(disk=disk),
            logs=LogPaths(host=self.install_log_path), ui=ui,
            runner=self._ensure_runner(), disk_manager=self.disk_manager,
            live_disks=frozenset(), live_reasons={},
            expected_identity=expected_identity, current_identity=None,
        )
        return InstallerEngine().run(ctx)

    def on_install_success(self):
        self.prog_spinner.stop()
        self.prog_title.set_text("Installazione Completata con Successo!")
        self.prog_bar.set_fraction(1.0)
        self.prog_status.set_text("Hyle OS è ora installato sul tuo disco fisso. Puoi riavviare per iniziare.")
        self.prog_status.add_css_class("success-banner")
        self.done_btn_box.set_visible(True)

    def on_install_failure(self, err_msg):
        self.install_in_progress = False
        self.prog_spinner.stop()
        self.prog_title.set_text("Errore durante l'installazione")
        full_msg = f"{err_msg}\n\nLog completo: {self.install_log_path}\nÈ possibile copiare il log su una chiavetta USB per l'invio al supporto."
        self.prog_status.set_text(full_msg)
        self.prog_status.set_wrap(True)
        self.prog_status.add_css_class("danger-banner")
        try:
            last = "\n".join(self.log_entries[-40:]) if self.log_entries else ""
            if last:
                lbl = Gtk.Label(label="Ultime righe del log:\n" + last)
                lbl.set_wrap(True)
                lbl.set_xalign(0)
                self.main_box.append(lbl)
        except Exception:
            pass
