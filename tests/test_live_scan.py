#!/usr/bin/env python3
"""Test offline della classificazione dei dischi live (DiskManager.live_scan).

Esegui da qualunque directory:
    python3 tests/test_live_scan.py

Non tocca dischi reali: simula findmnt/lsblk e verifica che:
  - il supporto di avvio (dd o loopback) sia classificato "confirmed";
  - una chiavetta con un residuo iso9660 non venga nascosta (sia "suspect");
  - una live non identificabile blocchi l'installazione;
  - su sistema installato non ci siano dischi live.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "out", "rootfs-overlay", "usr", "share", "hyleos"))

from installer.core.disk_manager import DiskManager  # noqa: E402


class FakeDiskManager(DiskManager):
    def __init__(self, findmnt, tree, cmdline="rd.live=1 live.autologin", pkname=None, backing=None):
        super().__init__()
        self._findmnt = findmnt
        self._tree = tree
        self._cmdline = cmdline
        self._pkname = pkname or {}
        self._backing = backing or {}

    def _out(self, args):
        if args[0] == "findmnt" and "--json" in args:
            return json.dumps(self._findmnt)
        if args[0] == "findmnt" and "-T" in args:
            target = args[args.index("-T") + 1]
            if target in self._backing:
                return self._backing[target] + "\n"
            raise RuntimeError("not found")
        if args[0] == "lsblk" and "-no" in args:
            dev = args[-1]
            if dev in self._pkname:
                return self._pkname[dev] + "\n"
            raise RuntimeError("no pkname")
        if args[0] == "lsblk" and "--json" in args:
            return json.dumps(self._tree)
        raise RuntimeError(f"unexpected: {args}")

    def _is_real_disk(self, name):
        return str(name).startswith("/dev/")

    def _is_live_boot(self):
        return bool(self._cmdline)


def node(path, ntype, fstype=None, mountpoints=None, children=None):
    return {"path": path, "type": ntype, "fstype": fstype,
            "mountpoints": mountpoints or [], "children": children or []}


def case(name, dm, expect_confirmed, expect_suspect):
    scan = dm.live_scan()
    ok = scan["confirmed"] == set(expect_confirmed) and scan["suspect"] == set(expect_suspect)
    print(("PASS" if ok else "FAIL"), name)
    print("   confirmed:", sorted(scan["confirmed"]), "| suspect:", sorted(scan["suspect"]))
    if not ok:
        print("   atteso confirmed:", sorted(expect_confirmed), "suspect:", sorted(expect_suspect))
        print("   reasons:", scan["reasons"])
    return ok


def main():
    results = []

    # 1) dd su /dev/sdb: iso9660 montato su /run/initramfs/live; nvme e' il target.
    findmnt = {"filesystems": [
        {"source": "/dev/sdb", "target": "/run/initramfs/live", "fstype": "iso9660"},
    ]}
    tree = {"blockdevices": [
        node("/dev/sdb", "disk", "iso9660", [None]),
        node("/dev/nvme0n1", "disk", None, [None], [
            node("/dev/nvme0n1p1", "part", "ntfs", [None]),
        ]),
    ]}
    results.append(case("dd: live=sdb, target=nvme0n1", FakeDiskManager(findmnt, tree),
                        ["/dev/sdb"], []))

    # 2) loopback: loop0 su /run/initramfs/live, file ISO su /dev/sdb1.
    findmnt = {"filesystems": [
        {"source": "/dev/loop0", "target": "/run/initramfs/live", "fstype": "squashfs"},
        {"source": "/dev/sdb1", "target": "/run/initramfs/isoscan", "fstype": "ext4"},
    ]}
    tree = {"blockdevices": [
        node("/dev/sdb", "disk", None, [None], [
            node("/dev/sdb1", "part", "ext4", ["/run/initramfs/isoscan"]),
        ]),
        node("/dev/loop0", "loop", "squashfs", ["/run/initramfs/live"]),
        node("/dev/sda", "disk", None, [None]),
    ]}
    dm = FakeDiskManager(findmnt, tree,
                         pkname={"/dev/sdb1": "sdb"},
                         backing={"/dev/loop0": "/run/initramfs/isoscan/hyleos.iso",
                                  "/run/initramfs/isoscan/hyleos.iso": "/dev/sdb1"})
    results.append(case("loopback: live=sdb, target=sda", dm, ["/dev/sdb"], []))

    # 3) live da /dev/sda; il target /dev/sdb ha una vecchia partizione iso9660
    #    NON montata: deve essere sospetto, non confermato (prima veniva nascosto).
    findmnt = {"filesystems": [
        {"source": "/dev/sda1", "target": "/run/initramfs/isoscan", "fstype": "ext4"},
        {"source": "/dev/loop0", "target": "/run/initramfs/live", "fstype": "squashfs"},
    ]}
    tree = {"blockdevices": [
        node("/dev/sda", "disk", None, [None], [
            node("/dev/sda1", "part", "ext4", ["/run/initramfs/isoscan"]),
        ]),
        node("/dev/loop0", "loop", "squashfs", ["/run/initramfs/live"]),
        node("/dev/sdb", "disk", None, [None], [
            node("/dev/sdb1", "part", "iso9660", [None]),
            node("/dev/sdb2", "part", "ext4", ["/run/media/user/OLD"]),
        ]),
    ]}
    dm = FakeDiskManager(findmnt, tree,
                         pkname={"/dev/sda1": "sda"},
                         backing={"/dev/loop0": "/run/initramfs/isoscan/hyleos.iso",
                                  "/run/initramfs/isoscan/hyleos.iso": "/dev/sda1"})
    results.append(case("target con residuo iso9660 -> sospetto", dm, ["/dev/sda"], ["/dev/sdb"]))

    # 4) live non identificabile: deve bloccare (raise).
    findmnt = {"filesystems": []}
    tree = {"blockdevices": [node("/dev/sda", "disk", None, [None])]}
    try:
        FakeDiskManager(findmnt, tree).live_scan()
        print("FAIL live non identificabile: nessuna eccezione")
        results.append(False)
    except RuntimeError as exc:
        print("PASS live non identificabile -> RuntimeError")
        print("   ", str(exc)[:110])
        results.append(True)

    # 5) sistema installato (non live): il disco di root non deve mai comparire.
    findmnt = {"filesystems": [
        {"source": "/dev/sda2", "target": "/", "fstype": "ext4"},
    ]}
    tree = {"blockdevices": [node("/dev/sda", "disk", None, [None], [
        node("/dev/sda2", "part", "ext4", ["/"]),
    ])]}
    dm = FakeDiskManager(findmnt, tree, cmdline="",
                         pkname={"/dev/sda2": "sda"},
                         backing={"/": "/dev/sda2"})
    results.append(case("sistema installato: root escluso", dm, ["/dev/sda"], []))

    print("\n%d/%d test superati" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
