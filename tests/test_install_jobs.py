#!/usr/bin/env python3
"""Test offline del refactor Calamares-style dell'installer HyleOS.

Esegui da qualunque directory:
    python3 -m unittest tests.test_install_jobs -v

Nessun comando tocca dischi reali: tutti i comandi passano da FakeRunner e
i percorsi di destinazione sono directory temporanee.
"""
import ast
import contextlib
import importlib.util
import inspect
import io
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HYLEOS = os.path.join(ROOT, "out", "rootfs-overlay", "usr", "share", "hyleos")
sys.path.insert(0, HYLEOS)

from installer.core import engine, jobs  # noqa: E402
from installer.core.install_context import (  # noqa: E402
    DiskValidationError,
    FstabError,
    InstallContext,
    InstallError,
    LogPaths,
    MountStack,
    PartitionLayout,
    TargetPaths,
    UiCallbacks,
)

EXPECTED_JOB_NAMES = [
    "preflight_safety",
    "prepare_disk",
    "partition",
    "format",
    "mount_target",
    "copy_rootfs",
    "mount_chroot",
    "fstab",
    "configure_system",
    "initramfs",
    "bootloader",
]


@dataclass
class FakeResult:
    """Sostituto di CommandResult: nessun subprocess viene avviato."""

    rc: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def returncode(self):
        return self.rc


class FakeRunner:
    """Registra gli argv e restituisce risposte prestabilite."""

    def __init__(self, responses=None, default_rc=0):
        self.responses = {}
        for key, value in (responses or {}).items():
            normalized = tuple(key) if isinstance(key, (list, tuple)) else key
            self.responses[normalized] = value
        self.default_rc = default_rc
        self.commands = []

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.commands.append((argv, dict(kwargs)))
        full_key = tuple(argv)
        short_key = argv[0] if argv else None
        key = full_key if full_key in self.responses else short_key
        result = self.responses.get(key, FakeResult(rc=self.default_rc))
        if callable(result):
            result = result(argv, kwargs)
        return result

    def argv(self, first):
        return [cmd for cmd, _kwargs in self.commands if cmd and cmd[0] == first]

    def has(self, first):
        return bool(self.argv(first))


class LocalFsRunner(FakeRunner):
    """FakeRunner che esegue solo mkdir/mv/cp dentro una root temporanea."""

    def __init__(self, root, responses=None):
        super().__init__(responses)
        self.root = os.path.realpath(root)

    def _local(self, path):
        return os.path.realpath(path).startswith(self.root + os.sep)

    def run(self, argv, **kwargs):
        argv = list(argv)
        if argv and argv[0] == "mkdir" and "-p" in argv:
            for path in argv[1:]:
                if path != "-p" and self._local(path):
                    os.makedirs(path, exist_ok=True)
            return super().run(argv, **kwargs)
        if argv and argv[0] == "mv":
            src, dst = argv[1], argv[2]
            if self._local(src) and self._local(dst):
                shutil.move(src, dst)
            return super().run(argv, **kwargs)
        if argv and argv[0] == "cp":
            src, dst = argv[-2], argv[-1]
            if self._local(src) and self._local(dst):
                shutil.copy2(src, dst)
            return super().run(argv, **kwargs)
        return super().run(argv, **kwargs)


class RecordingUI:
    """Callback UI con contatori e storico, in sostituzione di UiCallbacks."""

    def __init__(self):
        self.progress_values = []
        self.logs = []
        self.success_calls = 0
        self.failure_messages = []
        self.finished_calls = 0

    def progress(self, value, text=""):
        self.progress_values.append((float(value), text))

    def log(self, level, message):
        self.logs.append((level, str(message)))

    def success(self):
        self.success_calls += 1

    def failure(self, message):
        self.failure_messages.append(str(message))

    def finished(self):
        self.finished_calls += 1


class FakeJob:
    """Job minimale usato per pilotare l'engine senza toccare il sistema."""

    def __init__(self, name, weight=0.0, execute=None, rollback=None):
        self.name = name
        self.progress_weight = weight
        self._execute = execute
        self._rollback = rollback
        self.executed = 0
        self.rollback_calls = 0
        self.error_type = RuntimeError

    def run(self, ctx):
        self.executed += 1
        if self._execute is not None:
            self._execute(ctx)

    def rollback(self, ctx):
        self.rollback_calls += 1
        if self._rollback is not None:
            self._rollback(ctx)


def make_ctx(root, runner=None, ui=None, exists=None, disk="/dev/sda", **kwargs):
    ctx = InstallContext(
        disk=disk,
        partitions=PartitionLayout(disk=disk),
        target=TargetPaths(root=root),
        logs=LogPaths(host=os.path.join(root, "host-installer.log")),
        ui=ui or RecordingUI(),
        runner=runner if runner is not None else FakeRunner(),
        sleep=lambda *_a, **_kw: None,
        exists=exists if exists is not None else (lambda _path: True),
        **kwargs,
    )
    return ctx


class FakeDiskManager:
    """DiskManager con esito configurabile per i controlli di J1."""

    def __init__(self, is_live=False, identity="identity-A", inspected=None,
                 size=64 * 1024**3, mounts=None, swaps=None,
                 release_failures=None, runner=None):
        self._is_live = is_live
        self._identity = identity
        self._inspected = inspected if inspected is not None else {"size": size, "ro": 0}
        self._size = size
        self._mounts = mounts or []
        self._swaps = swaps or []
        self.release_failures = list(release_failures or [])
        self.release_calls = []
        self._runner = runner
        self.unmount_calls = []

    def is_live_disk(self, disk):
        return self._is_live

    def inspect_disk(self, disk):
        return dict(self._inspected)

    def identity(self, info):
        return self._identity

    def disk_size_bytes(self, disk):
        return self._size

    def disk_mounts(self, disk):
        return list(self._mounts)

    def unmount_disk(self, disk):
        self.unmount_calls.append(disk)
        return []

    def release_disk(self, disk):
        self.release_calls.append(disk)
        if self._runner is not None:
            for swap in self._swaps:
                self._runner.run(["swapoff", swap], check=False)
            targets = []
            for item in self._mounts:
                target = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else item
                if target and target not in targets:
                    targets.append(target)
            for target in targets:
                self._runner.run(["umount", target], check=False)
            self._runner.run(["udevadm", "settle", "--timeout=10"], check=False)
        return [tuple(failure) for failure in self.release_failures]


class JobOrderTest(unittest.TestCase):
    def test_exact_order_unique_names_and_total_weight(self):
        names = [job.name for job in jobs.JOB_ORDER]
        self.assertEqual(names, EXPECTED_JOB_NAMES)
        self.assertEqual(len(names), 11)
        self.assertEqual(len(set(names)), 11)
        total = sum(float(job.progress_weight) for job in jobs.JOB_ORDER)
        self.assertAlmostEqual(total, 0.98, places=9)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hyleos-engine-")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "var", "log"), exist_ok=True)
        with open(os.path.join(self.root, "host-installer.log"), "w", encoding="utf-8") as fh:
            fh.write("fake installer log\n")
        self.ui = RecordingUI()
        self.runner = FakeRunner()

    def ctx(self):
        return make_ctx(self.root, runner=self.runner, ui=self.ui, exists=os.path.exists)

    def test_jobs_run_in_order_and_progress_is_monotonic(self):
        executed = []

        def emit(name, weight):
            def _run(ctx):
                executed.append(name)
                ctx.ui.progress(0.0, f"{name}:0")
                ctx.ui.progress(0.5, f"{name}:50")
                ctx.ui.progress(1.0, f"{name}:100")
            return _run

        fake_jobs = [
            FakeJob("preflight_safety", 0.20, emit("preflight_safety", 0.20)),
            FakeJob("format", 0.30, emit("format", 0.30)),
            FakeJob("bootloader", 0.48, emit("bootloader", 0.48)),
        ]
        ctx = self.ctx()
        with mock.patch.object(engine, "JOB_ORDER", fake_jobs):
            result = engine.InstallerEngine().run(ctx)

        self.assertTrue(result)
        self.assertEqual(executed, ["preflight_safety", "format", "bootloader"])
        self.assertEqual(ctx.completed_jobs, ["preflight_safety", "format", "bootloader"])
        values = [value for value, _text in self.ui.progress_values]
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[-1], 1.0)
        self.assertAlmostEqual(values[2], 0.20)
        self.assertAlmostEqual(values[5], 0.50)
        self.assertEqual(self.ui.success_calls, 1)
        self.assertEqual(self.ui.finished_calls, 1)

    def test_local_progress_is_mapped_into_job_window(self):
        seen = []

        def _run(ctx):
            ctx.ui.progress(0.5, "metà")

        fake_jobs = [FakeJob("single", 0.98, _run)]
        ctx = self.ctx()
        with mock.patch.object(engine, "JOB_ORDER", fake_jobs):
            self.assertTrue(engine.InstallerEngine().run(ctx))

        for value, text in self.ui.progress_values:
            if text == "metà":
                seen.append(value)
        self.assertEqual(len(seen), 1)
        self.assertAlmostEqual(seen[0], 0.49)
        self.assertEqual(self.ui.finished_calls, 1)

    def test_uneven_weights_and_decreasing_local_progress_never_go_back(self):
        def _run(ctx):
            ctx.ui.progress(1.0, "up")
            ctx.ui.progress(0.0, "down")

        fake_jobs = [
            FakeJob("first", 0.10, _run),
            FakeJob("second", 0.88, lambda ctx: ctx.ui.progress(0.0, "next")),
        ]
        ctx = self.ctx()
        with mock.patch.object(engine, "JOB_ORDER", fake_jobs):
            self.assertTrue(engine.InstallerEngine().run(ctx))

        values = [value for value, _text in self.ui.progress_values]
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(values[1], 0.10)
        self.assertAlmostEqual(values[2], 0.10)
        self.assertAlmostEqual(values[3], 1.0)

    def test_mid_job_failure_rolls_back_unmounts_and_finalizes(self):
        executed = []
        rollbacks = []

        def ok(name):
            def _run(_ctx):
                executed.append(name)
            return _run

        def boom(_ctx):
            executed.append("format")
            raise RuntimeError("boom-test")

        fake_jobs = [
            FakeJob("preflight_safety", 0.02, ok("preflight_safety"),
                    lambda _ctx: rollbacks.append("preflight_safety")),
            FakeJob("prepare_disk", 0.03, ok("prepare_disk"),
                    lambda _ctx: rollbacks.append("prepare_disk")),
            FakeJob("format", 0.10, boom,
                    lambda _ctx: rollbacks.append("format")),
            FakeJob("bootloader", 0.06, ok("bootloader")),
        ]
        ctx = self.ctx()
        ctx.mounts = MountStack(entries=["/mnt", "/mnt/dev", "/mnt/boot/efi",
                                         "/mnt/sys/firmware/efi/efivars"])
        with mock.patch.object(engine, "JOB_ORDER", fake_jobs):
            result = engine.InstallerEngine().run(ctx)

        self.assertFalse(result)
        self.assertEqual(executed, ["preflight_safety", "prepare_disk", "format"])
        self.assertEqual(ctx.completed_jobs, ["preflight_safety", "prepare_disk"])
        self.assertEqual(rollbacks, ["prepare_disk", "preflight_safety"])
        self.assertEqual([cmd for cmd, _ in self.runner.commands if cmd[0] == "sync"], [["sync"]])
        umounts = [cmd for cmd, _ in self.runner.commands if cmd[0] == "umount"]
        self.assertEqual(umounts, [
            ["umount", "-R", "/mnt/sys/firmware/efi/efivars"],
            ["umount", "-R", "/mnt/boot/efi"],
            ["umount", "-R", "/mnt/dev"],
            ["umount", "-R", "/mnt"],
        ])
        self.assertEqual(self.ui.failure_messages, ["boom-test"])
        self.assertEqual(self.ui.finished_calls, 1)
        self.assertEqual(self.ui.success_calls, 0)
        log_target = os.path.join(self.root, "var", "log", "hyleos-installer.log")
        with open(log_target, encoding="utf-8") as fh:
            self.assertIn("fake installer log", fh.read())


class PartitionNamingTest(unittest.TestCase):
    CASES = (
        ("/dev/nvme0n1", "p", "nvme0n1p1", "nvme0n1p2"),
        ("/dev/mmcblk0", "p", "mmcblk0p1", "mmcblk0p2"),
        ("/dev/loop0", "p", "loop0p1", "loop0p2"),
        ("/dev/sda", "", "sda1", "sda2"),
        ("/dev/vda", "", "vda1", "vda2"),
    )

    def test_partition_names_for_all_disk_types(self):
        for disk, naming, p1, p2 in self.CASES:
            with self.subTest(disk=disk):
                runner = FakeRunner()
                ctx = make_ctx("/tmp/unused", runner=runner,
                               exists=lambda path, a=p1, b=p2: path in ("/dev/" + a, "/dev/" + b))
                ctx.disk = disk
                ctx.partitions.disk = disk
                jobs.PartitionJob().run(ctx)
                self.assertEqual(ctx.partitions.naming, naming)
                self.assertEqual(ctx.partitions.p1, "/dev/" + p1)
                self.assertEqual(ctx.partitions.p2, "/dev/" + p2)
                self.assertTrue(runner.has("parted"))
                self.assertTrue(runner.has("partprobe"))

    def test_missing_partitions_raise_target_setup_error(self):
        ctx = make_ctx("/tmp/unused", exists=lambda _path: False)
        with self.assertRaises(jobs.TargetSetupError):
            jobs.PartitionJob().run(ctx)


class ReleaseAndBusyTest(unittest.TestCase):
    BUSY_STDERR = "blockdev: ioctl error on BLKRRPART: Device or resource busy"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hyleos-a2-")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def _prepare_ctx(self, runner, ui, dm=None, exists=None):
        ctx = make_ctx(self.root, runner=runner, ui=ui, exists=exists)
        if dm is not None:
            ctx.disk_manager = dm
        return ctx

    def test_release_disk_runs_before_wipefs(self):
        runner = FakeRunner()
        ui = RecordingUI()
        dm = FakeDiskManager(mounts=[("/dev/sda2", "/mnt/target")],
                             swaps=["/dev/sda3"], runner=runner)
        ctx = self._prepare_ctx(runner, ui, dm)
        jobs.PrepareDiskJob().run(ctx)

        self.assertEqual(dm.release_calls, ["/dev/sda"])
        commands = [argv for argv, _kwargs in runner.commands]
        wipe = commands.index(["wipefs", "--all", "--force", "/dev/sda"])
        self.assertLess(commands.index(["swapoff", "/dev/sda3"]), wipe)
        self.assertLess(commands.index(["umount", "/mnt/target"]), wipe)
        settle = next(i for i, argv in enumerate(commands)
                      if argv[:2] == ["udevadm", "settle"])
        self.assertLess(settle, wipe)

    def test_release_failure_is_fail_closed_and_mentions_mountpoint(self):
        runner = FakeRunner()
        ui = RecordingUI()
        dm = FakeDiskManager(
            mounts=[("/dev/sda2", "/mnt/residuo")],
            release_failures=[("/mnt/residuo", "mount ancora attivo")],
            runner=runner,
        )
        ctx = self._prepare_ctx(runner, ui, dm)
        with self.assertRaises(DiskValidationError) as caught:
            jobs.PrepareDiskJob().run(ctx)

        self.assertIn("/mnt/residuo", str(caught.exception))
        self.assertFalse(runner.has("wipefs"))
        self.assertEqual(dm.release_calls, ["/dev/sda"])

    def test_prepare_disk_tolerates_blockdev_busy(self):
        runner = FakeRunner({
            ("blockdev", "--rereadpt", "/dev/sda"): FakeResult(
                rc=1, stderr=self.BUSY_STDERR),
        })
        ui = RecordingUI()
        ctx = self._prepare_ctx(runner, ui)
        jobs.PrepareDiskJob().run(ctx)

        self.assertTrue(runner.has("partprobe"))
        self.assertTrue(any(level == "WARN" and "blockdev" in message
                            for level, message in ui.logs))

    def test_partition_tolerates_blockdev_busy(self):
        runner = FakeRunner({
            ("blockdev", "--rereadpt", "/dev/sda"): FakeResult(
                rc=1, stderr=self.BUSY_STDERR),
        })
        ui = RecordingUI()
        ctx = self._prepare_ctx(
            runner, ui, exists=lambda path: path in ("/dev/sda1", "/dev/sda2"))
        jobs.PartitionJob().run(ctx)

        self.assertEqual(ctx.partitions.p1, "/dev/sda1")
        self.assertEqual(ctx.partitions.p2, "/dev/sda2")
        self.assertTrue(any(level == "WARN" and "blockdev" in message
                            for level, message in ui.logs))

    def test_prepare_disk_tolerates_missing_blockdev(self):
        def missing_blockdev(_argv, _kwargs):
            raise FileNotFoundError("blockdev")

        runner = FakeRunner({
            ("blockdev", "--rereadpt", "/dev/sda"): missing_blockdev,
        })
        ui = RecordingUI()
        ctx = self._prepare_ctx(runner, ui)
        jobs.PrepareDiskJob().run(ctx)

        self.assertTrue(runner.has("partprobe"))
        self.assertTrue(any(level == "WARN" and "blockdev" in message
                            and "non disponibile" in message
                            for level, message in ui.logs))

    def test_partition_tolerates_missing_blockdev(self):
        def missing_blockdev(_argv, _kwargs):
            raise FileNotFoundError("blockdev")

        runner = FakeRunner({
            ("blockdev", "--rereadpt", "/dev/sda"): missing_blockdev,
        })
        ui = RecordingUI()
        ctx = self._prepare_ctx(
            runner, ui, exists=lambda path: path in ("/dev/sda1", "/dev/sda2"))
        jobs.PartitionJob().run(ctx)

        self.assertEqual(ctx.partitions.p1, "/dev/sda1")
        self.assertEqual(ctx.partitions.p2, "/dev/sda2")
        self.assertTrue(any(level == "WARN" and "blockdev" in message
                            and "non disponibile" in message
                            for level, message in ui.logs))

    def test_partx_retries_three_times_on_busy(self):
        calls = []

        def partx_response(_argv, _kwargs):
            calls.append(1)
            if len(calls) < 3:
                return FakeResult(rc=1, stderr="partx: BLKRRPART Device or resource busy")
            return FakeResult(rc=0)

        runner = FakeRunner({("partx", "-u", "/dev/sda"): partx_response})
        ui = RecordingUI()
        ctx = self._prepare_ctx(runner, ui)
        jobs.PartitionJob().run(ctx)

        self.assertEqual(len(runner.argv("partx")), 3)
        self.assertEqual(len(calls), 3)

    def test_real_disk_manager_release_disk_with_fake_runner(self):
        from installer.core import disk_manager as disk_manager_mod

        path_calls = []
        point_calls = []

        def paths_response(_argv, _kwargs):
            path_calls.append(1)
            return FakeResult(stdout="/dev/sda1\n/dev/sda2\n/dev/sda3\n")

        def points_response(_argv, _kwargs):
            point_calls.append(1)
            if len(point_calls) <= 2:
                return FakeResult(stdout=(
                    "/dev/sda1 [SWAP]\n"
                    "/dev/sda2 /mnt/target\n"
                    "/dev/sda3 /mnt/target/nested\n"
                ))
            return FakeResult(stdout="")

        runner = FakeRunner({
            ("lsblk", "-ln", "-o", "PATH", "/dev/sda"): paths_response,
            ("lsblk", "-ln", "-o", "PATH,MOUNTPOINT", "/dev/sda"): points_response,
        })
        manager = disk_manager_mod.DiskManager(runner=runner)
        with mock.patch.object(manager, "inspect_disk",
                               side_effect=OSError("no real disk in unit test")):
            failures = manager.release_disk("/dev/sda")

        self.assertEqual(failures, [])
        self.assertEqual(len(path_calls), 2)
        self.assertEqual(len(point_calls), 4)
        self.assertEqual(runner.argv("swapoff"), [["swapoff", "/dev/sda1"]])
        self.assertEqual(runner.argv("umount"), [
            ["umount", "/mnt/target/nested"],
            ["umount", "/mnt/target"],
        ])
        self.assertTrue(runner.has("udevadm"))

    def test_release_disk_detects_child_mounted_after_settle(self):
        from installer.core import disk_manager as disk_manager_mod

        path_calls = []

        def paths_response(_argv, _kwargs):
            path_calls.append(1)
            if len(path_calls) == 1:
                return FakeResult(stdout="/dev/sda1\n")
            return FakeResult(stdout="/dev/sda1\n/dev/sda2\n")

        def points_response(_argv, _kwargs):
            return FakeResult(stdout="/dev/sda2 /mnt/target\n")

        runner = FakeRunner({
            ("lsblk", "-ln", "-o", "PATH", "/dev/sda"): paths_response,
            ("lsblk", "-ln", "-o", "PATH,MOUNTPOINT", "/dev/sda"): points_response,
        })
        manager = disk_manager_mod.DiskManager(runner=runner)
        with mock.patch.object(manager, "inspect_disk",
                               side_effect=OSError("no real disk in unit test")):
            failures = manager.release_disk("/dev/sda")

        self.assertEqual([name for name, _error in failures], ["/mnt/target"])
        self.assertIn("mount ancora attivo: /mnt/target", failures[0][1])
        self.assertEqual(len(path_calls), 2)


class FstabTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hyleos-fstab-")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "etc"), exist_ok=True)

    def ctx(self, runner):
        ctx = make_ctx(self.root, runner=runner)
        ctx.partitions.p1 = "/dev/sda1"
        ctx.partitions.p2 = "/dev/sda2"
        return ctx

    def test_fstab_uses_blkid_uuids(self):
        runner = FakeRunner({
            ("blkid", "-s", "UUID", "-o", "value", "/dev/sda2"): FakeResult(stdout="ROOT-UUID-1234\n"),
            ("blkid", "-s", "UUID", "-o", "value", "/dev/sda1"): FakeResult(stdout="EFI-UUID-ABCD\n"),
        })
        ctx = self.ctx(runner)
        jobs.FstabJob().run(ctx)
        with open(ctx.paths.fstab, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("UUID=ROOT-UUID-1234    /            ext4    defaults,noatime    0    1", content)
        self.assertIn("UUID=EFI-UUID-ABCD    /boot/efi    vfat    umask=0077,shortname=winnt    0    2", content)
        self.assertIn("tmpfs", content)
        self.assertEqual(ctx.partitions.uuid_root, "ROOT-UUID-1234")
        self.assertEqual(ctx.partitions.uuid_efi, "EFI-UUID-ABCD")

    def test_fstab_falls_back_to_lsblk(self):
        runner = FakeRunner({
            ("blkid", "-s", "UUID", "-o", "value", "/dev/sda2"): FakeResult(stdout=""),
            ("blkid", "-s", "UUID", "-o", "value", "/dev/sda1"): FakeResult(stdout=""),
            ("lsblk", "-n", "-o", "UUID", "/dev/sda2"): FakeResult(stdout="LSBLK-ROOT\n"),
            ("lsblk", "-n", "-o", "UUID", "/dev/sda1"): FakeResult(stdout="LSBLK-EFI\n"),
        })
        ctx = self.ctx(runner)
        jobs.FstabJob().run(ctx)
        with open(ctx.paths.fstab, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("UUID=LSBLK-ROOT", content)
        self.assertIn("UUID=LSBLK-EFI", content)
        self.assertEqual(len(runner.argv("lsblk")), 2)

    def test_empty_uuids_raise_fstab_error(self):
        ctx = self.ctx(FakeRunner())
        with self.assertRaises(FstabError) as caught:
            jobs.FstabJob().run(ctx)
        self.assertIn("UUID", str(caught.exception))
        self.assertFalse(os.path.exists(ctx.paths.fstab))


def load_live_scan_module():
    """Riutilizza i 5 casi di tests/test_live_scan.py senza duplicarli."""
    path = os.path.join(ROOT, "tests", "test_live_scan.py")
    spec = importlib.util.spec_from_file_location("hyleos_test_live_scan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DiskSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hyleos-safety-")
        self.addCleanup(self.tmp.cleanup)

    def test_reuse_live_scan_cases(self):
        module = load_live_scan_module()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = module.main()
        self.assertEqual(result, 0, out.getvalue())
        self.assertIn("5/5 test superati", out.getvalue())

    def _run_preflight_then_prepare(self, dm, expected_identity="identity-A"):
        runner = FakeRunner({
            ("findmnt", "-rn", "-R", "-o", "TARGET", "/mnt"): FakeResult(rc=1, stdout=""),
        })
        ctx = make_ctx(self.tmp.name, runner=runner)
        ctx.disk_manager = dm
        ctx.expected_identity = expected_identity
        ctx.ui.log("INFO", "setup")
        fake_jobs = [jobs.PreflightSafetyJob(), jobs.PrepareDiskJob()]
        with mock.patch.object(engine, "JOB_ORDER", fake_jobs):
            self.assertFalse(engine.InstallerEngine().run(ctx))
        return runner

    def test_live_disk_preflight_denies_and_never_wipes(self):
        runner = self._run_preflight_then_prepare(FakeDiskManager(is_live=True))
        self.assertFalse(runner.has("wipefs"))
        # Il contratto richiesto e' DiskValidationError: il codice in produzione
        # solleva PreflightError (entrambi sono InstallError).
        with self.assertRaises(DiskValidationError):
            jobs.PreflightSafetyJob().run(self._preflight_ctx(FakeDiskManager(is_live=True)))

    def test_identity_mismatch_preflight_denies_and_never_wipes(self):
        runner = self._run_preflight_then_prepare(
            FakeDiskManager(identity="identity-B"), expected_identity="identity-A")
        self.assertFalse(runner.has("wipefs"))
        with self.assertRaises(DiskValidationError):
            jobs.PreflightSafetyJob().run(self._preflight_ctx(FakeDiskManager(identity="identity-B")))

    def _preflight_ctx(self, dm):
        ctx = make_ctx(self.tmp.name, runner=FakeRunner())
        ctx.disk_manager = dm
        ctx.expected_identity = "identity-A"
        return ctx


class DracutLiveNeutralizationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hyleos-dracut-")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.conf_dir = os.path.join(self.root, "etc", "dracut.conf.d")
        os.makedirs(self.conf_dir, exist_ok=True)
        self.live = os.path.join(self.conf_dir, "live.conf")
        self.mixed = os.path.join(self.conf_dir, "mixed.conf")
        self.plain = os.path.join(self.conf_dir, "plymouth.conf")
        self.live_text = 'add_dracutmodules+=" dmsquash-live "\n'
        self.mixed_text = ('add_dracutmodules+=" dmsquash-live "\n'
                           'add_dracutmodules+=" plymouth "\n')
        self.plain_text = 'add_dracutmodules+=" plymouth "\n'
        for path, text in ((self.live, self.live_text),
                           (self.mixed, self.mixed_text),
                           (self.plain, self.plain_text)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)

    def test_neutralization_uses_pre_hyleos_and_preserves_plain_config(self):
        runner = LocalFsRunner(self.root)
        ctx = make_ctx(self.root, runner=runner, exists=os.path.exists)
        jobs._neutralize_dracut_live(ctx)

        quarantine = os.path.join(self.root, "var", "lib", "hyleos", "dracut-live-quarantine")
        self.assertTrue(os.path.isfile(os.path.join(quarantine, "live.conf.pre-hyleos")))
        self.assertTrue(os.path.isfile(os.path.join(quarantine, "mixed.conf.pre-hyleos")))
        self.assertFalse(os.path.exists(self.live))
        with open(self.mixed, encoding="utf-8") as fh:
            mixed_after = fh.read()
        self.assertIn(jobs._DISABLE_PREFIX + 'add_dracutmodules+=" dmsquash-live "', mixed_after)
        self.assertIn('add_dracutmodules+=" plymouth "', mixed_after)
        self.assertNotIn(jobs._DISABLE_PREFIX + 'add_dracutmodules+=" plymouth "', mixed_after)
        with open(self.plain, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.plain_text)
        all_files = []
        for base, _dirs, files in os.walk(self.root):
            all_files.extend(os.path.join(base, name) for name in files)
        self.assertFalse([p for p in all_files if p.endswith((".bak", ".orig"))])
        manifest = os.path.join(quarantine, "manifest.txt")
        self.assertTrue(os.path.isfile(manifest))
        with open(manifest, encoding="utf-8") as fh:
            manifest_text = fh.read()
        self.assertIn("all-live", manifest_text)
        self.assertIn("partial", manifest_text)

    def test_configure_system_invokes_neutralization_first(self):
        tree = ast_parse(jobs.ConfigureSystemJob._execute)
        first = tree.body[0].value
        self.assertIsInstance(first, ast.Call)
        self.assertEqual(getattr(first.func, "id", None), "_neutralize_dracut_live")
        source = inspect.getsource(jobs._neutralize_dracut_live)
        self.assertIn(".pre-hyleos", source)
        self.assertNotIn(".bak", source)
        self.assertNotIn(".orig", source)


class LiveRuleCleanupTest(unittest.TestCase):
    def test_live_installer_rule_is_in_the_live_file_cleanup_list(self):
        source = inspect.getsource(jobs.ConfigureSystemJob._execute)
        self.assertIn('_p(ctx, "etc", "polkit-1", "rules.d", "void-live.rules")', source)
        self.assertIn(
            '_p(ctx, "usr", "share", "polkit-1", "rules.d", '
            '"50-hyleos-installer-live.rules")',
            source,
        )
        self.assertIn('ctx.run(["rm", "-f", path], check=False)', source)


def ast_parse(func):
    import ast
    import textwrap
    source = textwrap.dedent(inspect.getsource(func)).strip()
    return ast.parse(source).body[0]


if __name__ == "__main__":
    unittest.main()
