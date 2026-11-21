#!/usr/bin/env python3
import sys
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

_HYLEOS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HYLEOS_DIR not in sys.path:
    sys.path.insert(0, _HYLEOS_DIR)
from installer.core.disk_manager import DiskManager
from installer.core.validation import (
    validate_username, validate_fullname, validate_hostname,
    validate_password, validate_root_password, validate_timezone, validate_locale,
)
from installer.preflight import PreflightMixin
from installer.disks import DiskMixin
from installer.install_worker import InstallWorkerMixin
from installer.ui import UiMixin

class InstallerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.hyleos.installer",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = InstallerWindow(application=self)
        win.present()
        win.run_preflight_checks()

class InstallerWindow(PreflightMixin, DiskMixin, InstallWorkerMixin, UiMixin, Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("Hyle OS Installer")
        self.set_default_size(880, 620)
        self.install_in_progress = False
        self.install_log_path = "/tmp/hyleos-installer.log"
        self.log_entries = []
        self.disk_manager = DiskManager(logger=self)
        self.disk_identities = {}
        self._installer_mounts = []
        self.root_password = None

        # Style provider for accent colors
        css_provider = Gtk.CssProvider()
        css = """
        .accent-title { font-size: 26px; font-weight: 800; color: #9a82db; }
        .danger-banner { background-color: rgba(207, 102, 121, 0.15); border: 1px solid #cf6679; border-radius: 12px; padding: 12px; }
        .success-banner { background-color: rgba(76, 175, 80, 0.15); border: 1px solid #4caf50; border-radius: 12px; padding: 12px; }
        """
        css_provider.load_from_data(css.encode('utf-8'))
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Main Layout
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        # HeaderBar
        self.header = Adw.HeaderBar()
        self.main_box.append(self.header)

        # ViewStack (Pages)
        self.stack = Adw.ViewStack()
        self.main_box.append(self.stack)

        # Data state
        self.selected_disk = None
        self.selected_keymap = "it"
        self.available_disks = []
        self._disk_rows = []
        self.root_password_same = True
        self.selected_timezone = "Europe/Rome"
        self.selected_locale = "it_IT.UTF-8"

        self.connect("close-request", self.on_close_request)

        self.setup_page_welcome()
        self.setup_page_user()
        self.setup_page_disk()
        self.setup_page_confirm()
        self.setup_page_progress()

        self.stack.set_visible_child_name("welcome")

    def on_close_request(self, *args):
        if getattr(self, 'install_in_progress', False):
            return True
        return False

    def log(self, message, level="INFO"):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{ts}] [{level}] {message}"
        self.log_entries.append(entry)
        try:
            with open(self.install_log_path, "a") as f:
                f.write(entry + "\n")
                f.flush()
        except Exception:
            pass
        if level in ("WARN", "ERROR", "TRACEBACK"):
            try:
                print(entry, file=sys.stderr, flush=True)
            except Exception:
                pass


def main():
    app = InstallerApp()
    return app.run(sys.argv)

if __name__ == '__main__':
    sys.exit(main())
