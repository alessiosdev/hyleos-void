#!/usr/bin/env python3
"""Controlli pre-volo dell'installer (root, UEFI, strumenti).

Estratto da installer.py (punto 8 della checklist): il codice è lo stesso,
spostato in un modulo per poterlo leggere e modificare senza toccare la UI.
"""
import os
import sys
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


class PreflightMixin:
    def preflight_fail(self, msg):
        self.log(f"Preflight fallito: {msg}", "ERROR")
        try:
            print(f"PREFLIGHT ERROR: {msg}", file=sys.stderr, flush=True)
        except Exception:
            pass
        self.show_error(msg, fatal=True)
        return False

    def run_preflight_checks(self):
        try:
            self.log("Preflight: verifica privilegi root")
            if os.geteuid() != 0:
                return self.preflight_fail("Questo installer deve essere eseguito come root (pkexec/sudo/doas).")
            self.log("Preflight: verifica avvio UEFI")
            if not os.path.isdir("/sys/firmware/efi"):
                return self.preflight_fail("Avvio non in modalità UEFI rilevato.\nHyleOS supporta attualmente solo installazioni UEFI x86_64.\nSe hai bisogno di supporto BIOS/CSM apri una segnalazione.")
            self.log("Preflight: verifica architettura")
            machine = os.uname().machine
            if machine not in ("x86_64", "amd64"):
                return self.preflight_fail(f"Architettura non supportata: {machine}.\nHyleOS supporta solo x86_64.")
            self.log("Preflight: verifica comandi necessari")
            required = [
                "parted", "mkfs.vfat", "mkfs.ext4", "rsync", "dracut",
                "grub-install", "grub-mkconfig", "chpasswd",
                "useradd", "userdel", "lsblk", "blkid", "wipefs", "sgdisk",
                "udevadm", "partprobe", "findmnt", "mount", "umount", "chroot",
                "sync", "blockdev", "swapoff", "fuser", "find", "rmdir",
                "chmod", "mkdir", "cp", "rm", "tar", "sh",
            ]
            missing = [c for c in required if not shutil.which(c)]
            if missing:
                return self.preflight_fail("Comandi necessari mancanti nella live:\n" + ", ".join(missing))
            self.log("Preflight: verifica comando opzionale efibootmgr")
            if not shutil.which("efibootmgr"):
                self.log("Preflight: efibootmgr assente, verrà usato il fallback GRUB removable", "WARN")
            if not os.path.isdir("/sys/firmware/efi/efivars"):
                self.log("Preflight: /sys/firmware/efi/efivars assente, verrà usato il fallback GRUB removable", "WARN")
            elif not os.access("/sys/firmware/efi/efivars", os.W_OK):
                self.log("Preflight: /sys/firmware/efi/efivars non scrivibile, verrà usato il fallback GRUB removable", "WARN")
            self.log("Preflight: tutti i controlli superati")
            return True
        except Exception as e:
            self.log(f"Preflight exception: {e}", "ERROR")
            self.log(traceback.format_exc(), "TRACEBACK")
            return self.preflight_fail(f"Errore nei controlli preliminari: {e}")

