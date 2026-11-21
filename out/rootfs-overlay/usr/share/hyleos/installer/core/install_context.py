#!/usr/bin/env python3
"""Stato condiviso del motore di installazione HyleOS.

Tutti i servizi (comandi, mount, callback UI, percorsi) passano da qui: le
fasi in ``jobs.py`` non eseguono comandi direttamente.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from .command_runner import CommandRunner
except ImportError:  # import come modulo top-level (es. test offline)
    try:
        from command_runner import CommandRunner
    except ImportError:  # pragma: no cover - solo in contesti di import ibridi
        CommandRunner = None


# ---------------------------------------------------------------------------
# Errori typed
# ---------------------------------------------------------------------------

class InstallError(Exception):
    """Errore base dell'installazione, con fase e dettagli opzionali."""

    def __init__(self, message: Any = "", job: str = "", details: Any = None):
        self.job = job or ""
        self.message = str(message)
        self.details = details
        super().__init__(self.message)


class PreflightError(InstallError):
    pass


class DiskValidationError(InstallError):
    pass


class TargetSetupError(InstallError):
    pass


class CopyError(InstallError):
    pass


class FstabError(InstallError):
    pass


class ConfigurationError(InstallError):
    pass


class DracutLiveConfigError(InstallError):
    pass


class InitramfsError(InstallError):
    pass


class BootloaderError(InstallError):
    pass


class CleanupError(InstallError):
    pass


# ---------------------------------------------------------------------------
# Dati di contesto
# ---------------------------------------------------------------------------

@dataclass
class InstallOptions:
    username: str = ""
    fullname: str = ""
    password: str = ""
    hostname: str = ""
    keymap: str = ""
    timezone: str = ""
    locale: str = ""
    root_password_mode: str = "same"
    root_password: Optional[str] = None


@dataclass
class PrivilegeContext:
    is_root: bool = False
    elevation: Optional[str] = None
    session_env: dict = field(default_factory=dict)


@dataclass
class TargetPaths:
    """Percorsi dentro la root di destinazione (``ctx.paths``)."""

    root: str = "/mnt"

    @property
    def efi(self) -> str:
        return os.path.join(self.root, "boot", "efi")

    @property
    def etc(self) -> str:
        return os.path.join(self.root, "etc")

    @property
    def fstab(self) -> str:
        return os.path.join(self.etc, "fstab")

    @property
    def hostname(self) -> str:
        return os.path.join(self.etc, "hostname")

    @property
    def hosts(self) -> str:
        return os.path.join(self.etc, "hosts")

    @property
    def rc_conf(self) -> str:
        return os.path.join(self.etc, "rc.conf")

    @property
    def locale_conf(self) -> str:
        return os.path.join(self.etc, "locale.conf")

    @property
    def grub_default(self) -> str:
        return os.path.join(self.etc, "default", "grub")

    @property
    def dracut_conf_dir(self) -> str:
        return os.path.join(self.etc, "dracut.conf.d")

    @property
    def dracut_quarantine(self) -> str:
        return os.path.join(self.root, "var", "lib", "hyleos", "dracut-live-quarantine")

    @property
    def installer_log_target(self) -> str:
        return os.path.join(self.root, "var", "log", "hyleos-installer.log")

    @property
    def var_log(self) -> str:
        return os.path.join(self.root, "var", "log")

    def home(self, username: str) -> str:
        return os.path.join(self.root, "home", username)


@dataclass
class PartitionLayout:
    disk: str = ""
    p1: str = ""
    p2: str = ""
    naming: str = ""
    uuid_root: str = ""
    uuid_efi: str = ""


@dataclass
class LogPaths:
    host: str = "/tmp/hyleos-installer.log"
    target_rel: str = "var/log/hyleos-installer.log"


def _noop(*_args, **_kwargs):
    return None


@dataclass
class UiCallbacks:
    progress: Callable[[float, str], None] = field(default=_noop)
    success: Callable[[], None] = field(default=_noop)
    failure: Callable[[str], None] = field(default=_noop)
    finished: Callable[[], None] = field(default=_noop)
    log: Callable[[str, str], None] = field(default=_noop)


def _rc(result: Any) -> int:
    if result is None:
        return 0
    value = getattr(result, "rc", None)
    if value is None:
        value = getattr(result, "returncode", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class MountStack:
    """Mount creati dall'installer, smontati in ordine inverso di profondità."""

    entries: list = field(default_factory=list)

    def mount(self, ctx, src, dst, recursive: bool = False,
              make_rslave: bool = False):
        argv = ["mount"]
        if recursive:
            argv.append("--rbind")
        argv.extend([str(src), str(dst)])
        result = ctx.run(argv)
        if _rc(result):
            raise InstallError(f"Mount di {src} su {dst} fallito", details=result)
        if make_rslave:
            slave = ctx.run(["mount", "--make-rslave", str(dst)])
            if _rc(slave):
                raise InstallError(f"--make-rslave fallito per {dst}", details=slave)
        # Registrazione solo dopo il successo di tutti i comandi di mount.
        self.entries.append(str(dst))
        return result

    def unmount_all(self, ctx) -> list[str]:
        residues: list[str] = []
        for target in sorted((str(item) for item in self.entries),
                             key=len, reverse=True):
            try:
                probe = ctx.run(["mountpoint", "-q", target], check=False)
            except Exception as exc:
                ctx.ui.log("WARN", f"Verifica mountpoint {target} fallita: {exc}")
                residues.append(target)
                continue
            if _rc(probe) != 0:
                continue
            try:
                result = ctx.run(["umount", "-R", target], check=False)
                rc = _rc(result)
            except Exception as exc:
                ctx.ui.log("WARN", f"Smontaggio {target} fallito: {exc}")
                residues.append(target)
                continue
            if rc:
                ctx.ui.log("WARN", f"Smontaggio {target} fallito (rc={rc})")
                residues.append(target)
        return residues


@dataclass
class InstallContext:
    """Contesto di esecuzione condiviso tra worker, engine e job."""

    options: InstallOptions = field(default_factory=InstallOptions)
    privilege: PrivilegeContext = field(default_factory=PrivilegeContext)
    target: TargetPaths = field(default_factory=TargetPaths)
    partitions: PartitionLayout = field(default_factory=PartitionLayout)
    logs: LogPaths = field(default_factory=LogPaths)
    ui: UiCallbacks = field(default_factory=UiCallbacks)
    runner: Any = None
    disk_manager: Any = None
    live_disks: Any = field(default_factory=frozenset)
    live_reasons: dict = field(default_factory=dict)
    expected_identity: Any = None
    current_identity: Any = None
    disk: str = ""
    mounts: MountStack = field(default_factory=MountStack)
    sleep: Callable = field(default=time.sleep)
    exists: Callable = field(default=os.path.exists)
    current_job: str = ""
    completed_jobs: list = field(default_factory=list)
    dracut_quarantine: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def __post_init__(self):
        if not self.disk:
            self.disk = getattr(self.partitions, "disk", "") or ""
        elif not getattr(self.partitions, "disk", ""):
            self.partitions.disk = self.disk
        if self.mounts is None:
            self.mounts = MountStack()
        if self.runner is None and CommandRunner is not None:
            self.runner = CommandRunner()

    @property
    def paths(self) -> TargetPaths:
        """Alias di ``target``: ``target=`` e ``paths`` puntano allo stesso oggetto."""
        return self.target

    @property
    def install_log_path(self) -> str:
        return self.logs.host

    def run(self, argv, **kwargs):
        return self.runner.run(argv, **kwargs)

    # Alias storico usato dai chiamanti più vecchi.
    run_cmd = run

    def run_text(self, argv, **kwargs) -> str:
        return self.run(argv, **kwargs).stdout
