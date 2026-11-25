#!/usr/bin/env python3
"""Fasi dell'installazione HyleOS.

Ogni ``Job`` incapsula una fase dell'installer: i comandi e le logiche sono
quelli della worker storica, con i percorsi di destinazione presi da
``ctx.paths`` e i comandi esterni eseguiti sempre tramite ``ctx.run``.
"""

from __future__ import annotations

import abc
import glob
import hashlib
import os
import re
import shlex
import stat
import tempfile
import uuid
from dataclasses import dataclass

from installer.core.install_context import (
    BootloaderError,
    ConfigurationError,
    CopyError,
    DiskValidationError,
    DracutLiveConfigError,
    FstabError,
    InitramfsError,
    PreflightError,
    TargetSetupError,
)


_TYPED_ERRORS = (
    PreflightError,
    DiskValidationError,
    TargetSetupError,
    CopyError,
    FstabError,
    ConfigurationError,
    DracutLiveConfigError,
    InitramfsError,
    BootloaderError,
)

# Marker delle configurazioni dracut appartenenti alla live (M2).
_LIVE_DRACUT_MARKERS = (
    "vmklive",
    "dmsquash-live",
    "dmsquash",
    "rd.live",
    "live.autologin",
    "root=live:",
    "livenet",
    "autoinstaller",
)
_DISABLE_PREFIX = "# hyleos-installer: disabled live-only directive: "

# Le 22 esclusioni rsync della worker storica, verbatim. Quella che nella
# worker era scritta con il punto di lavoro dedicato viene costruita da
# ctx.paths.root, così il valore runtime resta identico.
_RSYNC_EXCLUDES = (
    "/dev/*",
    "/proc/*",
    "/sys/*",
    "/tmp/*",
    "/run/*",
    None,  # sostituito con f"{ctx.paths.root}/*"
    "/media/*",
    "/lost+found",
    "/boot/efi/*",
    "/var/tmp/*",
    "/var/cache/xbps/*",
    "/var/log/*",
    "/home/anon/*",
    "/root/.cache/*",
    "/var/lib/NetworkManager/*",
    "/var/lib/bluetooth/*",
    "/etc/NetworkManager/system-connections/*",
    "/etc/udev/rules.d/70-persistent-net.rules",
    "/rootfs-overlay",
    "/usr/sbin/void-installer",
    "/usr/bin/void-installer",
    "/usr/bin/hyleos-installer.live",
)


def _p(ctx, *parts: str) -> str:
    """Percorso dentro la root di destinazione, sempre da ctx.paths."""
    return os.path.join(ctx.paths.root, *parts)


def _first_line(text) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _stdout(result) -> str:
    return getattr(result, "stdout", "") or ""


def _rc(result) -> int:
    rc = getattr(result, "rc", None)
    if rc is None:
        rc = getattr(result, "returncode", 0)
    return int(rc or 0)


def _stderr(result) -> str:
    return getattr(result, "stderr", "") or ""


def _is_busy(result) -> bool:
    return "busy" in (_stdout(result) + " " + _stderr(result)).lower()


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _atomic_write(path: str, content: str) -> None:
    """Scrittura atomica con preservazione dei permessi (usata da M2)."""
    directory = os.path.dirname(path) or "."
    mode = stat.S_IMODE(os.stat(path).st_mode)
    fd, tmp_path = tempfile.mkstemp(prefix=".hyleos-dracut-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _uuid(ctx, part: str) -> str:
    """UUID reale della partizione: blkid, poi fallback lsblk."""
    uid = ""
    try:
        uid = _first_line(_stdout(ctx.run(["blkid", "-s", "UUID", "-o", "value", part], check=False)))
    except Exception as exc:
        ctx.ui.log("WARN", f"blkid UUID fallito per {part}: {exc}")
    if not uid:
        try:
            uid = _first_line(_stdout(ctx.run(["lsblk", "-n", "-o", "UUID", part], check=False)))
        except Exception as exc:
            ctx.ui.log("WARN", f"lsblk UUID fallito per {part}: {exc}")
    return uid


def _swap_partitions(ctx, disk: str) -> list[str]:
    """Partizioni swap del disco scelto (stessa logica della worker)."""
    manager = ctx.disk_manager
    discover = getattr(manager, "swap_partitions", None)
    if callable(discover):
        return list(discover(disk) or [])
    result = ctx.run(["lsblk", "-n", "-o", "NAME,MOUNTPOINT", disk], check=False)
    swaps = []
    for line in _stdout(result).splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2 and "[SWAP]" in fields[1]:
            name = fields[0].strip()
            if name:
                swaps.append(name if name.startswith("/dev/") else "/dev/" + name)
    return swaps


def _disk_mounts(ctx, disk: str) -> list:
    """Mount delle partizioni del disco, con punto di mount quando disponibile."""
    manager = ctx.disk_manager
    detailed = getattr(manager, "disk_mounts", None)
    if callable(detailed):
        return list(detailed(disk) or [])
    return [(part, "") for part in manager.get_mounted_parts(disk)]


def _release_disk(ctx, disk: str) -> list[tuple[str, str]]:
    """Rilascia il disco prima di scriverci: swapoff, umount e settle."""
    manager = ctx.disk_manager
    release = getattr(manager, "release_disk", None)
    if callable(release):
        return list(release(disk) or [])
    failures = []
    for swap_part in _swap_partitions(ctx, disk):
        result = ctx.run(["swapoff", swap_part], check=False)
        if _rc(result) != 0:
            failures.append((swap_part, _stderr(result).strip() or f"rc {_rc(result)}"))
    unmount = getattr(manager, "unmount_disk", None)
    if callable(unmount):
        failures.extend(unmount(disk) or [])
    ctx.run(["udevadm", "settle", "--timeout=10"], check=False)
    return failures


def _dracut_candidate_files(ctx) -> list[str]:
    """Solo dracut.conf e dracut.conf.d/*.conf dentro la destinazione."""
    candidates = []
    main_conf = os.path.join(ctx.paths.etc, "dracut.conf")
    if ctx.exists(main_conf):
        candidates.append(main_conf)
    conf_dir = ctx.paths.dracut_conf_dir
    if ctx.exists(conf_dir):
        candidates.extend(sorted(glob.glob(os.path.join(conf_dir, "*.conf"))))
    return candidates


def _live_markers(line: str) -> list[str]:
    lowered = line.lower()
    return [marker for marker in _LIVE_DRACUT_MARKERS if marker in lowered]


def _neutralize_dracut_live(ctx) -> None:
    """M2: neutralizza le configurazioni dracut live-only nel sistema copiato.

    Ispeziona solo dracut.conf e dracut.conf.d/*..conf, ignora i commenti,
    sposta in quarantena i file interamente live-only e commenta solo le righe
    marcate nei file misti. Nessun file viene cancellato.
    """
    quarantine_dir = ctx.paths.dracut_quarantine
    try:
        ctx.run(["mkdir", "-p", quarantine_dir], check=False)
    except Exception as exc:
        raise DracutLiveConfigError(
            f"Impossibile creare la directory di quarantena dracut {quarantine_dir}: {exc}"
        ) from exc
    manifest = os.path.join(quarantine_dir, "manifest.txt")

    for path in _dracut_candidate_files(ctx):
        try:
            with open(path, "rb") as handle:
                original_bytes = handle.read()
            text = original_bytes.decode("utf-8")
        except Exception as exc:
            raise DracutLiveConfigError(f"Impossibile leggere {path}: {exc}") from exc

        lines = text.splitlines(keepends=True)
        active = [
            (index, line)
            for index, line in enumerate(lines)
            if line.strip() and not line.lstrip().startswith("#")
        ]
        flagged = []
        for index, line in active:
            markers = _live_markers(line)
            if markers:
                flagged.append((index, markers))

        if not flagged:
            # Config senza marker: non va toccata.
            continue

        all_live = len(flagged) == len(active)
        rule = ("all-live" if all_live else "partial") + ":" + ", ".join(
            sorted({marker for _index, markers in flagged for marker in markers})
        )
        sha256 = hashlib.sha256(original_bytes).hexdigest()
        basename = os.path.basename(path)
        quarantined = os.path.join(quarantine_dir, basename + ".pre-hyleos")
        reason = f"direttive dracut live-only ({rule}) in {path}"

        try:
            if all_live:
                ctx.run(["mv", path, quarantined])
                ctx.ui.log("INFO", f"Dracut live: {path} spostato in quarantena ({quarantined})")
                ctx.dracut_quarantine.append((path, reason + "; file spostato in quarantena"))
            else:
                ctx.run(["cp", "-p", path, quarantined])
                new_lines = list(lines)
                for index, _markers in flagged:
                    new_lines[index] = _DISABLE_PREFIX + lines[index]
                _atomic_write(path, "".join(new_lines))
                ctx.ui.log(
                    "INFO",
                    f"Dracut live: {len(flagged)} direttive live-only commentate in {path}; "
                    f"originale conservato in {quarantined}",
                )
                ctx.dracut_quarantine.append(
                    (path, reason + f"; {len(flagged)} righe commentate, originale in {quarantined}")
                )
            with open(manifest, "a", encoding="utf-8") as handle:
                handle.write(f"{path}\t{rule}\t{sha256}\n")
            ctx.ui.log("INFO", f"Dracut live: manifest aggiornato per {path} (sha256 {sha256})")
        except DracutLiveConfigError:
            raise
        except Exception as exc:
            raise DracutLiveConfigError(
                f"Neutralizzazione configurazione dracut live fallita per {path}: {exc}"
            ) from exc


def _copy_installer_log(ctx) -> None:
    """Copia il log nel sistema installato, come nella worker storica."""
    source = getattr(ctx, "install_log_path", None)
    target = ctx.paths.installer_log_target
    if not source or not ctx.exists(source):
        ctx.ui.log("WARN", "Log dell'installer non disponibile: copia nel sistema installata saltata")
        return
    try:
        ctx.run(["mkdir", "-p", os.path.dirname(target) or ctx.paths.var_log], check=False)
        ctx.run(["cp", "-p", source, target], check=False)
        ctx.run(["chmod", "0644", target], check=False)
        ctx.ui.log("INFO", f"Log dell'installer copiato in {target}")
    except Exception as exc:
        ctx.ui.log("WARN", f"copy log to target failed: {exc}")


@dataclass(frozen=True)
class Job(abc.ABC):
    """Singola fase dell'installazione, con peso sul progresso complessivo."""

    name: str
    progress_weight: float
    error_type: type[Exception] = RuntimeError

    @abc.abstractmethod
    def _execute(self, ctx) -> None:
        """Esegue la fase usando esclusivamente l'API di ctx."""

    def run(self, ctx) -> None:
        try:
            self._execute(ctx)
        except _TYPED_ERRORS:
            raise
        except Exception as exc:
            raise self.error_type(f"Fase '{self.name}' fallita: {exc}") from exc

    def rollback(self, ctx) -> None:
        """Di default non c'è nulla da annullare."""
        return None


@dataclass(frozen=True)
class PreflightSafetyJob(Job):
    name: str = "preflight_safety"
    progress_weight: float = 0.02
    error_type: type[Exception] = PreflightError

    def _execute(self, ctx) -> None:
        if not ctx.exists("/sys/firmware/efi"):
            raise PreflightError(
                "Hyle OS richiede un avvio UEFI per installare il bootloader. "
                "Riavvia il sistema in modalità UEFI e riprova. Nessuna modifica è stata fatta al disco."
            )

        disk = ctx.disk
        ctx.ui.log("INFO", f"Verifica sicurezza disco: {disk}")
        if not isinstance(disk, str) or not re.fullmatch(r"/dev/[A-Za-z0-9._-]+", disk):
            raise PreflightError(f"Percorso disco non valido: {disk!r}")
        if ctx.disk_manager.is_live_disk(disk) or disk in set(ctx.live_disks or ()):
            reasons = ctx.live_reasons or {}
            reason = reasons.get(disk) or reasons.get(os.path.basename(disk)) or "supporto di avvio della live"
            ctx.ui.log("WARN", f"Disco live rilevato ({disk}): {reason}")
            raise DiskValidationError(
                f"Disco live rilevato ({disk}): nessuna modifica verrà apportata al disco di avvio."
            )

        current_identity = ctx.disk_manager.identity(ctx.disk_manager.inspect_disk(disk))
        if current_identity != ctx.expected_identity:
            raise DiskValidationError(
                "L'identità del disco è cambiata dopo la selezione. Nessuna modifica è stata fatta: "
                "riavvia la procedura e seleziona di nuovo il disco."
            )

        disk_info = ctx.disk_manager.inspect_disk(disk)
        from_inspect = int(disk_info.get("size") or 0)
        measured = ctx.disk_manager.disk_size_bytes(disk)
        disk_size = int(measured or from_inspect)
        if disk_info.get("ro") in (1, True, "1"):
            raise PreflightError(f"Il disco {disk} è in sola lettura.")
        if disk_size < 25 * 1024 * 1024 * 1024:
            raise PreflightError(f"Disco troppo piccolo ({disk_size} byte): sono necessari almeno 25 GiB.")

        mounted = _disk_mounts(ctx, disk)
        if mounted:
            # Il disco è stato scelto esplicitamente dall'utente e verrà
            # cancellato: smontiamo le sue partizioni (spesso auto-montate
            # da udisks2/desktop) invece di rifiutare l'installazione.
            ctx.ui.log(
                "WARN",
                "Disco con partizioni montate, smonto prima di procedere: "
                + ", ".join(f"{part} su {point}" for part, point in mounted),
            )
            failures = ctx.disk_manager.unmount_disk(disk)
            if failures:
                raise PreflightError(
                    "Impossibile smontare alcune partizioni del disco selezionato: "
                    + ", ".join(f"{point} ({error})" for point, error in failures)
                )
            mounted = _disk_mounts(ctx, disk)
            if mounted:
                raise PreflightError(
                    "Il disco ha ancora partizioni montate dopo lo smontaggio: "
                    + ", ".join(f"{part} su {point}" for part, point in mounted)
                )

        # Il punto di lavoro è dedicato: non smontiamo mount preesistenti
        # che potrebbero appartenere a un'altra attività dell'utente.
        root = ctx.paths.root
        result = ctx.run(["findmnt", "-rn", "-R", "-o", "TARGET", root], check=False)
        if _rc(result) not in (0, 1):
            raise PreflightError(
                f"Impossibile verificare i mount sotto {root}: {_stdout(result).strip()}"
            )
        occupied = [
            line for line in _stdout(result).splitlines() if line == root or line.startswith(root + "/")
        ]
        if occupied:
            raise PreflightError(
                f"Il punto di lavoro {root} è già in uso (" + ", ".join(occupied)
                + "). Smonta quei filesystem e riprova."
            )
        ctx.ui.log("INFO", f"Verifica sicurezza disco OK: {disk} ({disk_size} byte)")


@dataclass(frozen=True)
class PrepareDiskJob(Job):
    name: str = "prepare_disk"
    progress_weight: float = 0.03
    error_type: type[Exception] = DiskValidationError

    def _execute(self, ctx) -> None:
        disk = ctx.disk
        ctx.ui.log("INFO", "Preparazione disco...")
        failures = _release_disk(ctx, disk)
        if failures:
            raise DiskValidationError(
                "Rilascio disco incompleto: mount/swap residui su "
                + ", ".join(f"{target} ({error})" for target, error in failures)
            )
        ctx.run(["wipefs", "--all", "--force", disk])
        ctx.run(["sgdisk", "--zap-all", disk])
        ctx.run(["udevadm", "settle", "--timeout=10"], check=False)
        ctx.run(["partprobe", disk])
        try:
            reread = ctx.run(["blockdev", "--rereadpt", disk], check=False)
        except Exception as exc:
            ctx.ui.log(
                "WARN",
                f"blockdev --rereadpt non disponibile su {disk} ({exc}): "
                "partprobe ha aggiornato la tabella, proseguo in best-effort",
            )
        else:
            if _rc(reread) != 0:
                detail = _stderr(reread).strip() or f"rc {_rc(reread)}"
                if _is_busy(reread):
                    ctx.ui.log(
                        "WARN",
                        f"blockdev --rereadpt ignorato su {disk} ({detail}): "
                        "partprobe ha aggiornato la tabella, proseguo in best-effort",
                    )
                else:
                    ctx.ui.log("WARN", f"blockdev --rereadpt best-effort fallito su {disk}: {detail}")
        ctx.run(["udevadm", "settle", "--timeout=10"], check=False)
        ctx.sleep(2)


@dataclass(frozen=True)
class PartitionJob(Job):
    name: str = "partition"
    progress_weight: float = 0.05
    error_type: type[Exception] = TargetSetupError

    def _execute(self, ctx) -> None:
        disk = ctx.disk
        ctx.ui.log("INFO", f"Fase partizionamento: creazione GPT e partizioni su {disk}")
        ctx.run(["parted", "-s", disk, "mklabel", "gpt"])
        ctx.run(["parted", "-s", disk, "mkpart", "EFI", "fat32", "1MiB", "513MiB"])
        ctx.run(["parted", "-s", disk, "set", "1", "esp", "on"])
        ctx.run(["parted", "-s", disk, "mkpart", "HyleOS", "ext4", "513MiB", "100%"])

        ctx.run(["partprobe", disk])
        partx_error = ""
        for attempt in range(3):
            result = ctx.run(["partx", "-u", disk], check=False)
            if _rc(result) == 0:
                partx_error = ""
                break
            partx_error = _stderr(result).strip() or f"rc {_rc(result)}"
            if not _is_busy(result) or attempt == 2:
                break
            ctx.sleep(1)
        if partx_error:
            ctx.ui.log("WARN", f"partx -u best-effort su {disk} non riuscito: {partx_error}")
        ctx.run(["udevadm", "settle", "--timeout=10"], check=False)
        try:
            reread = ctx.run(["blockdev", "--rereadpt", disk], check=False)
        except Exception as exc:
            ctx.ui.log(
                "WARN",
                f"blockdev --rereadpt non disponibile su {disk} ({exc}): "
                "partprobe ha aggiornato la tabella, proseguo con la verifica dei nodi",
            )
        else:
            if _rc(reread) != 0:
                detail = _stderr(reread).strip() or f"rc {_rc(reread)}"
                if _is_busy(reread):
                    ctx.ui.log(
                        "WARN",
                        f"blockdev --rereadpt ignorato su {disk} ({detail}): "
                        "partprobe ha aggiornato la tabella, proseguo con la verifica dei nodi",
                    )
                else:
                    ctx.ui.log("WARN", f"blockdev --rereadpt best-effort fallito su {disk}: {detail}")
        ctx.run(["udevadm", "settle", "--timeout=10"], check=False)

        # Detect partition names (nvme0n1p1 vs sda1)
        naming = "p" if ("nvme" in disk or "mmcblk" in disk or "loop" in disk) else ""
        p1 = f"{disk}{naming}1"
        p2 = f"{disk}{naming}2"
        for _ in range(15):
            if ctx.exists(p1) and ctx.exists(p2):
                break
            ctx.sleep(1)
        if not ctx.exists(p1) or not ctx.exists(p2):
            raise TargetSetupError(f"Partizioni non create correttamente: {p1}, {p2}")

        ctx.partitions.disk = disk
        ctx.partitions.naming = naming
        ctx.partitions.p1 = p1
        ctx.partitions.p2 = p2


@dataclass(frozen=True)
class FormatJob(Job):
    name: str = "format"
    progress_weight: float = 0.10
    error_type: type[Exception] = TargetSetupError

    def _execute(self, ctx) -> None:
        p1 = ctx.partitions.p1
        p2 = ctx.partitions.p2
        ctx.ui.log("INFO", f"Fase formattazione: mkfs.vfat {p1}, mkfs.ext4 {p2}")
        ctx.run(["mkfs.vfat", "-F32", "-n", "EFI", p1])
        ctx.run(["mkfs.ext4", "-F", "-L", "HyleOS", p2])

        # UUID reali, riletti dopo la formattazione.
        ctx.partitions.uuid_root = _uuid(ctx, p2)
        ctx.partitions.uuid_efi = _uuid(ctx, p1)
        if not ctx.partitions.uuid_root:
            raise TargetSetupError(f"UUID non rilevato per la partizione root {p2}.")
        if not ctx.partitions.uuid_efi:
            raise TargetSetupError(f"UUID non rilevato per la partizione EFI {p1}.")


@dataclass(frozen=True)
class MountTargetJob(Job):
    name: str = "mount_target"
    progress_weight: float = 0.05
    error_type: type[Exception] = TargetSetupError

    def _execute(self, ctx) -> None:
        root = ctx.paths.root
        efi = ctx.paths.efi
        ctx.ui.log("INFO", f"Fase mount: {ctx.partitions.p2} su {root} e {ctx.partitions.p1} su {efi}")
        ctx.run(["mkdir", "-p", root])
        ctx.mounts.mount(ctx, ctx.partitions.p2, root)
        ctx.run(["mkdir", "-p", efi])
        ctx.mounts.mount(ctx, ctx.partitions.p1, efi)


@dataclass(frozen=True)
class CopyRootfsJob(Job):
    name: str = "copy_rootfs"
    progress_weight: float = 0.55
    error_type: type[Exception] = CopyError

    def _execute(self, ctx) -> None:
        root = ctx.paths.root
        ctx.ui.log("INFO", f"Fase copia: rsync del sistema live verso {root}")
        ctx.ui.progress(
            0.35,
            "Copia del sistema Hyle OS sul disco con rsync (questo richiederà un paio di minuti)...",
        )
        excludes = [root + "/*" if item is None else item for item in _RSYNC_EXCLUDES]
        rsync_cmd = ["rsync", "-aHAXx", "--numeric-ids", "--delete-delay"]
        for exclusion in excludes:
            rsync_cmd += ["--exclude", exclusion]
        rsync_cmd += ["/", root + "/"]
        ctx.ui.log("INFO", f"Esecuzione rsync: {' '.join(shlex.quote(str(c)) for c in rsync_cmd)}")
        result = ctx.run(rsync_cmd, max_lines=200)
        ctx.ui.log("INFO", f"RSYNC returncode: {_rc(result)}")
        for line in _stdout(result).splitlines()[:50]:
            ctx.ui.log("INFO", f"RSYNC_OUT: {line}")
        stderr = getattr(result, "stderr", "") or ""
        for line in stderr.splitlines()[:50]:
            ctx.ui.log("INFO", f"RSYNC_ERR: {line}")
        if _rc(result) != 0:
            raise CopyError(
                f"Copia del sistema non completata (rsync codice {_rc(result)}). Consulta il log per i dettagli."
            )

        required_target_paths = (
            _p(ctx, "bin", "sh"),
            _p(ctx, "etc", "os-release"),
            _p(ctx, "etc", "passwd"),
            _p(ctx, "usr", "bin", "xbps-reconfigure"),
            _p(ctx, "usr", "bin", "dracut"),
            _p(ctx, "usr", "bin", "grub-mkconfig"),
        )
        missing = [path for path in required_target_paths if not ctx.exists(path)]
        if missing:
            raise CopyError("Copia incompleta; mancano file essenziali: " + ", ".join(missing))

        # Rimuovi file specifici della live
        ctx.run(
            [
                "rm",
                "-f",
                _p(ctx, "usr", "sbin", "void-installer"),
                _p(ctx, "usr", "bin", "hyleos-installer.live"),
            ],
            check=False,
        )
        ctx.run(["rmdir", _p(ctx, "mnt", "target")], check=False)

        # Ricrea directory vitali per mount virtuali
        for directory in (
            "dev",
            "proc",
            "sys",
            "tmp",
            "run",
            "mnt",
            "media",
            "var/tmp",
            "boot/efi",
            "boot/grub",
            "sys/firmware/efi/efivars",
        ):
            ctx.run(["mkdir", "-p", _p(ctx, *directory.split("/"))], check=False)
        ctx.run(["chmod", "1777", _p(ctx, "tmp"), _p(ctx, "var", "tmp")], check=False)


@dataclass(frozen=True)
class MountChrootJob(Job):
    name: str = "mount_chroot"
    progress_weight: float = 0.01
    error_type: type[Exception] = TargetSetupError

    def _execute(self, ctx) -> None:
        # Prepara i mount virtuali per il chroot (servono a useradd, xbps, dracut, grub).
        ctx.ui.log("INFO", "Fase mount chroot: bind /dev, /proc, /sys, /run")
        for source, parts in (
            ("/dev", ("dev",)),
            ("/proc", ("proc",)),
            ("/sys", ("sys",)),
            ("/run", ("run",)),
        ):
            destination = _p(ctx, *parts)
            ctx.run(["mkdir", "-p", destination], check=False)
            # efivars è coperto dal bind di /sys; la directory viene comunque creata.
            ctx.mounts.mount(ctx, source, destination, recursive=True, make_rslave=True)
        ctx.run(["mkdir", "-p", _p(ctx, "sys", "firmware", "efi", "efivars")], check=False)


@dataclass(frozen=True)
class FstabJob(Job):
    name: str = "fstab"
    progress_weight: float = 0.04
    error_type: type[Exception] = FstabError

    def _execute(self, ctx) -> None:
        ctx.ui.log("INFO", f"Fase fstab: generazione {ctx.paths.fstab}")
        uuid_root = ctx.partitions.uuid_root
        uuid_efi = ctx.partitions.uuid_efi
        if not uuid_root:
            uuid_root = _uuid(ctx, ctx.partitions.p2)
            ctx.partitions.uuid_root = uuid_root
        if not uuid_efi:
            uuid_efi = _uuid(ctx, ctx.partitions.p1)
            ctx.partitions.uuid_efi = uuid_efi
        if not uuid_root:
            raise FstabError(f"UUID non rilevato per la partizione root {ctx.partitions.p2}.")
        if not uuid_efi:
            raise FstabError(f"UUID non rilevato per la partizione EFI {ctx.partitions.p1}.")

        fstab_content = (
            "# /etc/fstab: static file system information.\n"
            f"UUID={uuid_root}    /            ext4    defaults,noatime    0    1\n"
            f"UUID={uuid_efi}    /boot/efi    vfat    umask=0077,shortname=winnt    0    2\n"
            "tmpfs              /tmp         tmpfs   defaults,nosuid,nodev    0    0\n"
        )
        _write(ctx.paths.fstab, fstab_content)


@dataclass(frozen=True)
class ConfigureSystemJob(Job):
    name: str = "configure_system"
    progress_weight: float = 0.03
    error_type: type[Exception] = ConfigurationError

    def _execute(self, ctx) -> None:
        # M2: prima di qualsiasi altra configurazione, neutralizza le
        # direttive dracut appartenenti alla live.
        _neutralize_dracut_live(ctx)

        root = ctx.paths.root
        options = ctx.options
        username = options.username
        hostname = options.hostname
        keymap = options.keymap
        timezone = options.timezone
        locale = options.locale

        ctx.ui.log("INFO", f"Fase configurazione sistema e utente '{username}'")

        _write(ctx.paths.hostname, f"{hostname}\n")

        try:
            hosts_content = _read(ctx.paths.hosts) if ctx.exists(ctx.paths.hosts) else ""
            if not re.search(
                r"(?m)^\s*127\.0\.1\.1\s+.*\b" + re.escape(hostname) + r"\b", hosts_content
            ):
                if hosts_content and not hosts_content.endswith("\n"):
                    hosts_content += "\n"
                hosts_content += f"127.0.1.1\t{hostname}\n"
                _write(ctx.paths.hosts, hosts_content)
                ctx.ui.log("INFO", f"/etc/hosts aggiornato con 127.0.1.1 {hostname}")
        except Exception as exc:
            ctx.ui.log("WARN", f"aggiornamento /etc/hosts fallito: {exc}")

        # Create User inside target
        q_fullname = shlex.quote(options.fullname)
        chroot_user_script = f"""
        set -e
        GROUPS=""
        for g in audio video wheel input storage; do
            if grep -q "^${{g}}:" /etc/group; then
                GROUPS="${{GROUPS}}${{GROUPS:+,}}${{g}}"
            fi
        done
        if [ -n "$GROUPS" ]; then
            useradd -m -c {q_fullname} -G "$GROUPS" -s /bin/bash "{username}"
        else
            useradd -m -c {q_fullname} -s /bin/bash "{username}"
        fi
        if [ -d /etc/skel ]; then
            cp -rn /etc/skel/. "/home/{username}/" 2>/dev/null || true
        fi
        chown -R "{username}:{username}" "/home/{username}"
        mkdir -p /etc/sudoers.d
        echo "%wheel ALL=(ALL:ALL) ALL" > /etc/sudoers.d/99-hyleos-wheel
        chmod 0440 /etc/sudoers.d/99-hyleos-wheel
        if command -v doas >/dev/null 2>&1; then
            echo "permit persist :wheel" > /etc/doas.conf
            chmod 0400 /etc/doas.conf
        fi
        """
        ctx.run(["chroot", root, "sh", "-c", chroot_user_script])
        ctx.run(
            ["chroot", root, "chpasswd", "-c", "SHA512"],
            input_text=f"{username}:{options.password}\n",
        )
        if options.root_password_mode in ("same", "same_as_user", "user", True):
            root_pw = options.password
        elif options.root_password:
            root_pw = options.root_password
        else:
            raise ConfigurationError("Password root non specificata ma richiesta separata.")
        ctx.run(["chroot", root, "chpasswd", "-c", "SHA512"], input_text=f"root:{root_pw}\n")

        # Verifica che l'utente esista davvero e che i gruppi principali siano applicati
        try:
            uid = _stdout(ctx.run(["chroot", root, "id", "-u", username])).strip()
            groups_out = _stdout(ctx.run(["chroot", root, "id", "-nG", username])).strip()
        except Exception as exc:
            raise ConfigurationError(f"Verifica utente '{username}' fallita: {exc}") from exc
        if not uid:
            raise ConfigurationError(
                f"Verifica utente '{username}' fallita: id -u non ha restituito un UID."
            )
        existing_groups = set()
        try:
            for line in _read(_p(ctx, "etc", "group")).splitlines():
                if ":" in line:
                    existing_groups.add(line.split(":", 1)[0])
        except Exception as exc:
            ctx.ui.log("WARN", f"lettura {_p(ctx, 'etc', 'group')} fallita: {exc}")
        check_groups = [
            group for group in ("audio", "video", "wheel", "input", "storage")
            if group in existing_groups
        ]
        missing_groups = [group for group in check_groups if group not in groups_out.split()]
        if missing_groups:
            raise ConfigurationError(
                f"Utente '{username}' creato ma gruppi mancanti: {', '.join(missing_groups)}"
            )
        ctx.ui.log("INFO", f"Utente '{username}' verificato (UID {uid}, gruppi: {groups_out})")

        # Allinea il layout di Hyprland alla scelta fatta nell'installer.
        # Hyprland >= 0.57 usa esclusivamente hyprland.lua (hyprland.conf
        # non viene piu' letto), quindi aggiorniamo la chiave Lua.
        for hypr_conf in (
            os.path.join(ctx.paths.home(username), ".config", "hypr", "hyprland.lua"),
            _p(ctx, "etc", "skel", ".config", "hypr", "hyprland.lua"),
        ):
            if ctx.exists(hypr_conf):
                try:
                    hypr_text = _read(hypr_conf)
                    hypr_text = re.sub(
                        r'(?m)^(\s*kb_layout\s*=\s*)"[^"]*"',
                        r'\g<1>"' + re.escape(keymap) + '"',
                        hypr_text,
                    )
                    _write(hypr_conf, hypr_text)
                except Exception as exc:
                    ctx.ui.log("WARN", f"hypr kb_layout update failed: {exc}")

        # Applica timezone e locale
        try:
            tz_src = f"/usr/share/zoneinfo/{timezone}"
            if not timezone or not ctx.exists(tz_src):
                ctx.ui.log("WARN", f"Timezone selezionata '{timezone}' non valida, uso fallback UTC")
                tz_src = "/usr/share/zoneinfo/UTC"
            if ctx.exists(tz_src):
                ctx.run(["rm", "-f", _p(ctx, "etc", "localtime")], check=False)
                ctx.run(["ln", "-s", tz_src, _p(ctx, "etc", "localtime")])
                ctx.ui.log("INFO", f"Timezone impostata: {tz_src}")
            else:
                ctx.ui.log("WARN", f"Impossibile impostare {_p(ctx, 'etc', 'localtime')}: zoneinfo non disponibile")

            # rc.conf: preserva le altre righe, sostituisce KEYMAP e TIMEZONE
            kept = []
            if ctx.exists(ctx.paths.rc_conf):
                kept = [
                    line for line in _read(ctx.paths.rc_conf).splitlines(keepends=True)
                    if not re.match(r"^\s*#?\s*(KEYMAP|TIMEZONE)=", line)
                ]
            rc_content = "".join(kept)
            if rc_content and not rc_content.endswith("\n"):
                rc_content += "\n"
            rc_content += f'KEYMAP="{keymap}"\nTIMEZONE="{timezone}"\n'
            _write(ctx.paths.rc_conf, rc_content)

            _write(ctx.paths.locale_conf, f"LANG={locale}\nLC_ALL={locale}\n")

            # Abilita il locale in /etc/default/libc-locales (gestisce commenti con/senza spazio e righe attive)
            libc_locales = _p(ctx, "etc", "default", "libc-locales")
            if ctx.exists(libc_locales):
                original = _read(libc_locales)
                locale_re = re.compile(r"(?m)^[ \t]*#?[ \t]*" + re.escape(locale) + r"[ \t]+.*$")
                updated, count = locale_re.subn(f"{locale} UTF-8", original)
                if count == 0 and locale not in ("C", "C.UTF-8", "POSIX"):
                    updated = original.rstrip("\n") + f"\n{locale} UTF-8\n"
                if updated != original:
                    _write(libc_locales, updated)
                ctx.ui.log("INFO", f"Locale '{locale}' abilitato in libc-locales ({count} righe aggiornate)")
            else:
                ctx.ui.log("WARN", f"{libc_locales} assente, salto l'abilitazione del locale")

            # Genera il locale (best-effort)
            ctx.ui.log("INFO", "Generazione locale con xbps-reconfigure -f glibc-locales")
            try:
                ctx.run(["chroot", root, "xbps-reconfigure", "-f", "glibc-locales"], check=False)
            except Exception as exc:
                ctx.ui.log("WARN", f"xbps-reconfigure glibc-locales failed (non fatal): {exc}")
        except Exception as exc:
            ctx.ui.log("ERROR", f"timezone/locale setup failed: {exc}")

        # L'autologin "-a anon" nelle conf di agetty non è più valido perché
        # l'utente anon viene eliminato; lo rimuoviamo esplicitamente da tty1
        # per evitare un loop di login fallito.
        ctx.ui.log("INFO", "Pulizia tracce della live (utente anon e file live)")

        try:
            ctx.run(["chroot", root, "userdel", "-f", "-r", "anon"])
        except Exception as exc:
            # NON eseguire userdel sull'host: cancellerebbe l'utente della
            # live invece di quello del sistema installato.
            ctx.ui.log("WARN", f"userdel anon nel target fallito: {exc}")
        ctx.run(
            [
                "rm",
                "-rf",
                _p(ctx, "home", "anon"),
                _p(ctx, "var", "spool", "mail", "anon"),
                _p(ctx, "var", "spool", "mail", "anon-mail"),
            ],
            check=False,
        )

        # La live avvia agetty-tty1 con "live.autologin" e quindi il file
        # conf copiato nel target contiene "-a anon": rimuoviamo l'autologin.
        try:
            getty_conf = _p(ctx, "etc", "sv", "agetty-tty1", "conf")
            if ctx.exists(getty_conf):
                original = _read(getty_conf)
                updated = re.sub(
                    r'\s+(?:-a\s+[^\s"]+|--autologin(?:=|\s+)[^\s"]+)', "", original
                )
                if updated != original:
                    _write(getty_conf, updated)
                    ctx.ui.log("INFO", "Autologin live rimosso da agetty-tty1")
        except Exception as exc:
            ctx.ui.log("WARN", f"Pulizia autologin agetty fallita: {exc}")

        for path in (
            _p(ctx, "etc", "sudoers.d", "99-void-live"),
            _p(ctx, "etc", "default", "live.conf"),
            _p(ctx, "etc", "polkit-1", "rules.d", "void-live.rules"),
            _p(ctx, "usr", "share", "polkit-1", "rules.d", "50-hyleos-installer-live.rules"),
        ):
            ctx.run(["rm", "-f", path], check=False)

        # Pulizia tracce della live
        for issue in (_p(ctx, "etc", "issue"), _p(ctx, "etc", "issue.net")):
            try:
                _write(issue, "")
            except Exception:
                pass

        # Genera un machine-id valido e univoco per il sistema installato.
        # Un file vuoto NON è valido e impedisce a dbus-daemon di partire.
        fresh_machine_id = uuid.uuid4().hex
        for machine_id_path in (
            _p(ctx, "etc", "machine-id"),
            _p(ctx, "var", "lib", "dbus", "machine-id"),
        ):
            try:
                ctx.run(["mkdir", "-p", os.path.dirname(machine_id_path)], check=False)
                _write(machine_id_path, fresh_machine_id + "\n")
            except Exception as exc:
                ctx.ui.log("WARN", f"machine-id write failed ({machine_id_path}): {exc}")

        ctx.run(
            [
                "rm",
                "-f",
                _p(ctx, "root", ".bash_history"),
                _p(ctx, "home", "anon", ".bash_history"),
            ],
            check=False,
        )
        for ssh_key in glob.glob(_p(ctx, "etc", "ssh", "ssh_host_*")):
            ctx.run(["rm", "-f", ssh_key], check=False)
        ctx.run(
            ["find", ctx.paths.var_log, "-type", "f", "-name", "*.log", "-delete"], check=False
        )
        ctx.run(
            ["find", ctx.paths.var_log, "-type", "f", "-name", "*-old", "-delete"], check=False
        )
        for log_file in (
            _p(ctx, "var", "log", "wtmp"),
            _p(ctx, "var", "log", "btmp"),
            _p(ctx, "var", "log", "lastlog"),
        ):
            try:
                _write(log_file, "")
            except Exception:
                pass
        for cache_dir in (_p(ctx, "var", "cache", "xbps"), _p(ctx, "var", "cache", "fontconfig")):
            ctx.run(["find", cache_dir, "-type", "f", "-delete"], check=False)
        ctx.run(
            [
                "rm",
                "-rf",
                _p(ctx, "root", ".cache"),
                _p(ctx, "root", ".local", "share", "xorg"),
            ],
            check=False,
        )

        # NOTA: oltre alla pulizia di agetty-tty1 fatta sopra, non tocchiamo
        # /etc/runit, /etc/sv o /var/service.
        ctx.ui.log("INFO", f"Sostituzione riferimenti 'anon' con '{username}' nei file testuali")
        for root_dir in (_p(ctx, "etc", "profile.d"), _p(ctx, "usr", "bin")):
            if not ctx.exists(root_dir):
                continue
            try:
                for path in glob.glob(os.path.join(root_dir, "**"), recursive=True):
                    if not os.path.isfile(path):
                        continue
                    try:
                        content = _read(path)
                        if re.search(r"\banon\b", content):
                            _write(path, re.sub(r"\banon\b", username, content))
                    except Exception:
                        continue
            except Exception:
                pass
        for autostart_dir in (
            os.path.join(ctx.paths.home(username), ".config", "autostart"),
            _p(ctx, "etc", "skel", ".config", "autostart"),
            _p(ctx, "etc", "xdg", "autostart"),
        ):
            for autostart in glob.glob(os.path.join(autostart_dir, "*.desktop")):
                try:
                    content = _read(autostart)
                    if re.search(r"\banon\b", content):
                        _write(autostart, re.sub(r"\banon\b", username, content))
                except Exception:
                    pass


@dataclass(frozen=True)
class InitramfsJob(Job):
    name: str = "initramfs"
    progress_weight: float = 0.04
    error_type: type[Exception] = InitramfsError

    def _execute(self, ctx) -> None:
        root = ctx.paths.root
        ctx.ui.log("INFO", "Fase dracut: rigenerazione initramfs")

        # Verifica che i mount virtuali del chroot siano ancora attivi
        virtual_mounts = [_p(ctx, name) for name in ("dev", "proc", "sys", "run")]
        missing_mounts = [mount for mount in virtual_mounts if not ctx.exists(mount)]
        if missing_mounts:
            ctx.ui.log("WARN", f"Mount virtuali mancanti nel chroot: {', '.join(missing_mounts)}")

        # Rigenera servizi e configurazioni pacchetti come neko-installer
        ctx.run(["chroot", root, "xbps-reconfigure", "-a"])

        # Rigenerazione initramfs con catena di fallback
        ctx.ui.log("INFO", "dracut: tentativo --no-hostonly --force")
        try:
            ctx.run(["chroot", root, "dracut", "--no-hostonly", "--force"])
        except Exception:
            ctx.ui.log("WARN", "dracut: tentativo --no-hostonly fallito, provo --regenerate-all --force")
            try:
                ctx.run(["chroot", root, "dracut", "--regenerate-all", "--force"])
            except Exception:
                ctx.ui.log("WARN", "dracut: fallback --regenerate-all fallito, provo xbps-reconfigure -f linux")
                ctx.run(["chroot", root, "xbps-reconfigure", "-f", "linux"])

        # Allinea la cmdline del GRUB installato a quella della live (splash)
        grub_path = ctx.paths.grub_default
        grub_lines = _read(grub_path).splitlines(keepends=True) if ctx.exists(grub_path) else []
        grub_lines = [
            line for line in grub_lines
            if not line.startswith("GRUB_CMDLINE_LINUX_DEFAULT=")
            and not line.startswith("GRUB_TIMEOUT=")
        ]
        if grub_lines and not grub_lines[-1].endswith("\n"):
            grub_lines[-1] += "\n"
        grub_lines.append('GRUB_CMDLINE_LINUX_DEFAULT="quiet splash loglevel=3 vt.global_cursor_default=0"\n')
        grub_lines.append("GRUB_TIMEOUT=3\n")
        _write(grub_path, "".join(grub_lines))


@dataclass(frozen=True)
class BootloaderJob(Job):
    name: str = "bootloader"
    progress_weight: float = 0.06
    error_type: type[Exception] = BootloaderError

    def _execute(self, ctx) -> None:
        root = ctx.paths.root
        ctx.ui.log("INFO", "Fase grub: installazione bootloader UEFI e BOOTX64.EFI")
        efi_in_chroot = "/" + os.path.relpath(ctx.paths.efi, root).replace(os.sep, "/")
        chroot_grub_script = f"""
        set -e
        mkdir -p /boot/grub {efi_in_chroot}/EFI/BOOT
        if ! grub-install --target=x86_64-efi --efi-directory={efi_in_chroot} --bootloader-id="HyleOS" --recheck; then
            echo "grub-install con NVRAM fallito, provo fallback --removable"
            grub-install --target=x86_64-efi --efi-directory={efi_in_chroot} --bootloader-id="HyleOS" --removable --recheck
        fi
        SRC=""
        if [ -f {efi_in_chroot}/EFI/HyleOS/grubx64.efi ]; then
            SRC={efi_in_chroot}/EFI/HyleOS/grubx64.efi
        elif [ -f {efi_in_chroot}/EFI/BOOT/BOOTX64.EFI ]; then
            SRC={efi_in_chroot}/EFI/BOOT/BOOTX64.EFI
        fi
        if [ -z "$SRC" ]; then
            echo "ERRORE: grubx64.efi/BOOTX64.EFI non trovato dopo grub-install" >&2
            exit 1
        fi
        if [ "$SRC" != "{efi_in_chroot}/EFI/BOOT/BOOTX64.EFI" ]; then
            cp -f "$SRC" {efi_in_chroot}/EFI/BOOT/BOOTX64.EFI
        fi
        if ! test -s {efi_in_chroot}/EFI/BOOT/BOOTX64.EFI; then
            echo "ERRORE: {efi_in_chroot}/EFI/BOOT/BOOTX64.EFI mancante o vuoto" >&2
            exit 1
        fi
        grub-mkconfig -o /boot/grub/grub.cfg
        test -s /boot/grub/grub.cfg
        grep -Eq '^[[:space:]]*(linux|linuxefi)[[:space:]]' /boot/grub/grub.cfg
        grep -Eq '^[[:space:]]*initrd[[:space:]]' /boot/grub/grub.cfg
        if command -v efibootmgr >/dev/null 2>&1; then
            efibootmgr -v || true
        fi
        echo "OK: {efi_in_chroot}/EFI/BOOT/BOOTX64.EFI presente"
        """
        ctx.run(["chroot", root, "sh", "-c", chroot_grub_script])

        bootx64 = os.path.join(ctx.paths.efi, "EFI", "BOOT", "BOOTX64.EFI")
        if not (os.path.isfile(bootx64) and os.path.getsize(bootx64) > 0):
            raise BootloaderError(f"Verifica GRUB fallita: {bootx64} mancante o vuoto.")
        ctx.ui.log("INFO", f"Verifica BOOTX64.EFI OK: {bootx64} ({os.path.getsize(bootx64)} byte)")

        _copy_installer_log(ctx)


JOB_ORDER = (
    PreflightSafetyJob(),
    PrepareDiskJob(),
    PartitionJob(),
    FormatJob(),
    MountTargetJob(),
    CopyRootfsJob(),
    MountChrootJob(),
    FstabJob(),
    ConfigureSystemJob(),
    InitramfsJob(),
    BootloaderJob(),
)
