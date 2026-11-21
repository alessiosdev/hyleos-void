#!/usr/bin/env python3
"""Gestione e rilevamento dei dischi per l'installer HyleOS."""
import os
import re
import json
import stat
import subprocess

try:
    from .command_runner import CommandRunner
except ImportError:  # import come modulo top-level (es. test offline)
    from command_runner import CommandRunner


LIVE_FSTYPES = {"overlay", "iso9660", "squashfs"}
LIVE_MOUNTPOINTS = {"/run/initramfs/live", "/run/initramfs"}
# Punti in cui dracut/iso-scan montano il supporto di avvio o la partizione
# che contiene il file ISO (avvio in loopback).
LIVE_TARGETS = (
    "/run/initramfs/live",
    "/run/initramfs/isoscan",
    "/run/initramfs",
    "/run/archiso/bootmnt",
)


def _result(proc):
    """Normalizza CommandResult / CompletedProcess ai campi usati qui."""
    if isinstance(proc, str):
        return 0, proc, ""
    rc = getattr(proc, "rc", None)
    if rc is None:
        rc = getattr(proc, "returncode", 0)
    return rc, getattr(proc, "stdout", "") or "", getattr(proc, "stderr", "") or ""


class DiskManager:
    def __init__(self, logger=None, runner=None):
        self.logger = logger
        self.runner = runner or CommandRunner(logger=logger)

    def _log(self, msg, level="INFO"):
        if not self.logger:
            return
        try:
            self.logger.log(msg, level)
        except TypeError:
            try:
                self.logger.log(msg)
            except Exception:
                pass
        except Exception:
            pass

    def _out(self, args):
        # check=False: il RuntimeError storico (e il suo messaggio) resta invariato.
        rc, stdout, stderr = _result(self.runner.run(args, check=False))
        if rc:
            raise RuntimeError(stderr.strip() or f"Comando fallito: {' '.join(args)}")
        return stdout

    def inspect_disk(self, disk):
        """Return current kernel identity and partitions, failing on uncertainty."""
        if not isinstance(disk, str) or not re.fullmatch(r"/dev/[A-Za-z0-9._-]+", disk):
            raise ValueError(f"Percorso disco non valido: {disk!r}")
        st = os.stat(disk)
        if not stat.S_ISBLK(st.st_mode):
            raise ValueError(f"{disk} non è un dispositivo a blocchi")
        out = self._out([
            "lsblk", "--json", "--bytes", "--paths", "--output",
            "PATH,NAME,TYPE,SIZE,MODEL,SERIAL,WWN,RO,RM,TRAN,MOUNTPOINTS", disk,
        ])
        devices = json.loads(out).get("blockdevices", [])
        if len(devices) != 1 or devices[0].get("type") != "disk":
            raise ValueError(f"Impossibile identificare {disk} come disco fisico")
        dev = devices[0]
        dev["major_minor"] = f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
        dev["sysfs"] = os.path.realpath(f"/sys/dev/block/{dev['major_minor']}")
        return dev

    @staticmethod
    def identity(device):
        return {
            "path": device.get("path"),
            "name": device.get("name"),
            "size": str(device.get("size") or ""),
            "model": (device.get("model") or "").strip(),
            "serial": (device.get("serial") or "").strip(),
            "wwn": (device.get("wwn") or "").strip(),
            "major_minor": device.get("major_minor"),
            "sysfs": device.get("sysfs"),
        }

    def disk_mounts(self, disk):
        dev = self.inspect_disk(disk)
        mounts = []

        def walk(node):
            path = node.get("path")
            for point in node.get("mountpoints") or []:
                if point and point != "[SWAP]":
                    mounts.append((path, point))
            if node.get("mountpoint") and node.get("mountpoint") != "[SWAP]":
                mounts.append((path, node["mountpoint"]))
            for child in node.get("children") or []:
                walk(child)

        walk(dev)
        return mounts

    def swap_partitions(self, disk):
        dev = self.inspect_disk(disk)
        swaps = []

        def walk(node):
            path = node.get("path")
            points = node.get("mountpoints") or []
            if node.get("mountpoint"):
                points.append(node["mountpoint"])
            if path and "[SWAP]" in points:
                swaps.append(path)
            for child in node.get("children") or []:
                walk(child)

        walk(dev)
        return swaps

    @staticmethod
    def _is_live_target(target):
        return bool(target) and any(target == p or target.startswith(p + "/") for p in LIVE_TARGETS)

    def _is_live_boot(self):
        try:
            cmdline = open("/proc/cmdline", encoding="utf-8").read()
        except OSError:
            cmdline = ""
        return any(marker in cmdline for marker in ("rd.live", "live.autologin"))

    def live_scan(self):
        """Classify physical disks backing the live media.

        Returns a dict with:
          confirmed: disks that are certainly the live boot medium (mounted at a
                     live target, or holding the ISO file used in loopback);
          suspect:   disks that merely contain a live-like filesystem (iso9660,
                     squashfs, overlay) but are not proven to be the boot medium;
          reasons:   {disk_path: human readable reason}.

        Only ``confirmed`` disks must be hidden by the installer. ``suspect``
        disks are shown with a warning: hiding them is what made a target USB
        with a leftover live partition impossible to select.
        """
        confirmed = set()
        suspect = set()
        reasons = {}

        is_live = self._is_live_boot()

        try:
            mounts = json.loads(self._out([
                "findmnt", "--json", "--real", "--output", "SOURCE,TARGET,FSTYPE"
            ])).get("filesystems", [])
        except Exception as exc:
            if is_live:
                raise RuntimeError(f"Non riesco a verificare i mount del supporto live: {exc}")
            mounts = []

        def add(kind, disk, reason):
            if not disk or self._is_loop(disk):
                return
            if kind == "confirmed":
                confirmed.add(disk)
                suspect.discard(disk)
            elif disk not in confirmed:
                suspect.add(disk)
            reasons.setdefault(disk, reason)

        for mount in mounts:
            source = (mount.get("source") or "").split("[", 1)[0]
            target = mount.get("target") or ""
            fstype = (mount.get("fstype") or "").lower()
            if not (fstype in LIVE_FSTYPES or self._is_live_target(target)):
                continue
            if not source.startswith("/dev/"):
                continue
            source = os.path.realpath(source)
            base = os.path.basename(source)
            if base.startswith("loop"):
                # Avvio in loopback: risaliamo al disco che contiene il file ISO.
                backing = self._loop_backing(base)
                if backing and backing.startswith("/"):
                    try:
                        backing_source = self._out(["findmnt", "-n", "-o", "SOURCE", "-T", backing]).strip()
                        if backing_source.startswith("/dev/"):
                            source = os.path.realpath(backing_source.split("[", 1)[0])
                            base = os.path.basename(source)
                    except Exception:
                        pass
            try:
                parent = self._parent_disk(source) or source
                if not self._is_real_disk(parent):
                    continue
                if self._is_live_target(target):
                    add("confirmed", parent, f"supporto live montato su {target}")
                else:
                    add("suspect", parent, f"filesystem live ({fstype}) montato su {target}")
            except Exception:
                pass

        # ISO filesystems can be listed by lsblk even when findmnt uses an overlay root.
        tree = json.loads(self._out(["lsblk", "--json", "--paths", "--output", "PATH,TYPE,FSTYPE,MOUNTPOINTS"]))

        def walk(nodes, parent=None):
            for node in nodes or []:
                path = node.get("path")
                ntype = (node.get("type") or "").lower()
                current_parent = path if ntype == "disk" else parent
                points = node.get("mountpoints") or []
                if node.get("mountpoint"):
                    points.append(node["mountpoint"])
                live_target_mount = any(p and self._is_live_target(p) for p in points)
                is_live_node = (node.get("fstype") or "").lower() in LIVE_FSTYPES or live_target_mount
                if is_live_node and current_parent:
                    if live_target_mount:
                        add("confirmed", current_parent, "supporto live montato nel sistema live")
                    else:
                        add("suspect", current_parent,
                            f"partizione {(node.get('fstype') or 'live')} ({path}) simile al supporto live")
                walk(node.get("children"), current_parent)

        walk(tree.get("blockdevices", []))

        # Protezione extra: il disco che ospita la root in esecuzione non deve
        # mai essere formattato. Serve anche se l'installer viene lanciato da un
        # sistema installato invece che dalla live (dove "/" e' overlayfs).
        try:
            root_source = self._out(["findmnt", "-n", "-o", "SOURCE", "-T", "/"]).strip()
            root_source = root_source.split("[", 1)[0]
            if root_source.startswith("/dev/"):
                root_source = os.path.realpath(root_source)
                parent = self._parent_disk(root_source) or root_source
                if self._is_real_disk(parent):
                    add("confirmed", parent, "disco del sistema in esecuzione (root)")
        except Exception:
            pass

        if is_live and not confirmed:
            detail = "; ".join(f"{d}: {r}" for d, r in sorted(reasons.items())) or "nessun candidato"
            raise RuntimeError(
                "Avvio live rilevato, ma non riesco a identificare con certezza il "
                f"dispositivo di avvio ({detail}); per sicurezza l'installazione è bloccata."
            )
        return {"confirmed": confirmed, "suspect": suspect, "reasons": reasons}

    def live_detection(self):
        """Return only the confirmed live boot disks (safe to hide)."""
        return self.live_scan()["confirmed"]

    def _dev_path(self, name):
        if not name:
            return None
        name = str(name).strip()
        if not name:
            return None
        if name.startswith("/dev/"):
            return name
        return f"/dev/{name}"

    @staticmethod
    def _basename(name):
        return os.path.basename(str(name or "").strip())

    def _is_loop(self, name):
        return self._basename(name).startswith("loop")

    def _loop_backing(self, name):
        loop = self._basename(name)
        try:
            with open(f"/sys/block/{loop}/loop/backing_file", "r") as f:
                backing = f.read().strip()
            if backing:
                self._log(f"Dispositivo live loop {loop} associato a: {backing}")
                return backing
        except Exception:
            pass
        return None

    def _is_real_disk(self, name):
        base = self._basename(name)
        if not base:
            return False
        if base.startswith(("loop", "zram", "ram")):
            return False
        return os.path.exists(f"/dev/{base}") or os.path.exists(f"/sys/block/{base}")

    def _parent_disk(self, dev):
        base = self._basename(dev)
        if not base:
            return None
        if base.startswith("loop"):
            self._loop_backing(base)
            return None
        if base.startswith("sr"):
            return self._dev_path(base)

        try:
            pkname = self._out(["lsblk", "-no", "PKNAME", self._dev_path(base)])
            for token in pkname.split():
                token = token.strip()
                if not re.fullmatch(r"[A-Za-z0-9._-]+", token):
                    continue
                if token.startswith("loop"):
                    self._loop_backing(token)
                    return None
                return self._dev_path(token)
        except Exception:
            pass

        if base.startswith(("nvme", "mmcblk")):
            disk = re.sub(r"p\d+$", "", base)
        else:
            disk = re.sub(r"\d+$", "", base)

        if not disk or disk.startswith("loop"):
            return None
        return self._dev_path(disk)

    def list_disks(self):
        disks = []
        try:
            result = self.runner.run(
                ["lsblk", "-d", "-n", "-J", "-o", "NAME,SIZE,MODEL,TYPE,RM,RO,TRAN"], check=False
            )
            rc, out, stderr = _result(result)
            if rc:
                raise RuntimeError(stderr.strip() or "Comando fallito: lsblk -d -n -J")
            data = json.loads(out)
            for dev in data.get("blockdevices", []):
                name = self._basename(dev.get("name"))
                if not name:
                    continue
                if dev.get("type") != "disk":
                    continue
                is_ro = dev.get("ro") in [1, True, "1"]
                rm = dev.get("rm") in [1, True, "1"]
                if name.startswith(("zram", "loop")):
                    self._log(f"list_disks: salto {name}: dispositivo virtuale")
                    continue
                dev_path = self._dev_path(name)
                if not dev_path or not os.path.exists(dev_path):
                    self._log(f"list_disks: salto {name}: nodo {dev_path} inesistente", "WARN")
                    continue
                if is_ro:
                    # Non lo scartiamo qui: l'installer lo mostra tra i dischi
                    # esclusi con il motivo (es. switch write-protect del
                    # lettore SD), invece di farlo sparire senza spiegazioni.
                    self._log(f"list_disks: {name} in sola lettura (write-protect?)", "WARN")
                identity = self.inspect_disk(dev_path)
                disks.append({**identity, "name": name, "path": dev_path,
                              "size_display": dev.get("size") or str(identity.get("size") or ""),
                              "model": dev.get("model") or "Disco generico",
                              "rm": rm, "ro": is_ro, "tran": dev.get("tran")})
        except Exception as e:
            self._log(f"list_disks error: {e}", "ERROR")
        return disks

    def live_boot_disks(self):
        return self.live_detection()

    def is_live_boot_device(self):
        disks = sorted(self.live_boot_disks())
        return disks[0] if disks else None

    def is_live_disk(self, disk):
        target = self._basename(disk)
        if not target:
            raise ValueError("Dispositivo disco non valido")
        live = self.live_boot_disks()
        return any(self._basename(p) == target for p in live)

    def has_mounted_partitions(self, disk):
        return bool(self.disk_mounts(disk))

    def unmount_disk(self, disk):
        """Unmount every mounted filesystem on ``disk`` (deepest first).

        Returns a list of ``(mountpoint, error)`` for the mounts that could not
        be released. The installer calls this right before wiping the disk the
        user explicitly selected, so auto-mounted USB/SD targets do not block
        the installation.
        """
        failures = []
        try:
            mounts = self.disk_mounts(disk)
        except Exception as exc:
            return [("?", str(exc))]
        targets = sorted({m for _path, m in mounts}, key=len, reverse=True)
        for target in targets:
            if not os.path.ismount(target):
                continue
            try:
                self._out(["umount", target])
                self._log(f"unmount_disk: smontato {target}")
            except Exception as exc:
                self._log(f"unmount_disk: umount {target} fallito: {exc}", "WARN")
                failures.append((target, str(exc)))
        return failures

    def get_mounted_parts(self, disk):
        mounted = []
        try:
            result = self.runner.run(
                ["lsblk", "-n", "-o", "NAME,MOUNTPOINT", disk], check=False
            )
            rc, out, stderr = _result(result)
            if rc:
                raise RuntimeError(stderr.strip() or f"Comando fallito: lsblk {disk}")
            for line in out.splitlines():
                parts = line.split(None, 1)
                if len(parts) >= 2 and parts[1].strip():
                    mounted.append(parts[0])
        except Exception:
            pass
        return mounted

    def disk_size_bytes(self, disk):
        try:
            result = self.runner.run(
                ["lsblk", "-b", "-d", "-n", "-o", "SIZE", disk], check=False
            )
            rc, out, stderr = _result(result)
            if rc:
                raise RuntimeError(stderr.strip() or f"Comando fallito: lsblk {disk}")
            return int(out.strip())
        except Exception as e:
            self._log(f"disk_size_bytes error: {e}", "ERROR")
            return None
