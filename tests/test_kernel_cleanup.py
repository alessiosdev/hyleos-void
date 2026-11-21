#!/usr/bin/env python3
"""Test di /usr/libexec/hyleos/kernel-cleanup.

Usa una finta /boot e un finto vkpurge nel PATH: nessun kernel reale viene
toccato.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "out/rootfs-overlay/usr/libexec/hyleos/kernel-cleanup")


class KernelCleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hyleos-kernel-")
        self.boot = os.path.join(self.tmp, "boot")
        self.bin = os.path.join(self.tmp, "bin")
        self.log = os.path.join(self.tmp, "vkpurge.log")
        os.makedirs(self.boot)
        os.makedirs(self.bin)
        self._write_vkpurge()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_vkpurge(self):
        path = os.path.join(self.bin, "vkpurge")
        with open(path, "w") as f:
            f.write(
                "#!/bin/sh\n"
                'case "$1" in\n'
                '  list) cat "$VKPURGE_LIST_FILE" ;;\n'
                '  rm) echo "$2" >> "$VKPURGE_LOG" ;;\n'
                '  *) exit 2 ;;\n'
                "esac\n"
            )
        os.chmod(path, 0o755)

    def _kernels(self, *versions):
        for v in versions:
            with open(os.path.join(self.boot, "vmlinuz-" + v), "w") as f:
                f.write("kernel")

    def _run(self, *args, running="6.4.1_1", keep="2", list_file=None):
        env = dict(os.environ)
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env["HYLEOS_BOOT_DIR"] = self.boot
        env["HYLEOS_RUNNING_KERNEL"] = running
        env["HYLEOS_KEEP_KERNELS"] = keep
        env["VKPURGE_LOG"] = self.log
        env["VKPURGE_LIST_FILE"] = list_file or os.path.join(self.tmp, "vklist")
        return subprocess.run(
            ["bash", SCRIPT, *args], env=env, capture_output=True, text=True
        )

    def _write_list(self, *versions):
        path = os.path.join(self.tmp, "vklist")
        with open(path, "w") as f:
            f.write("\n".join(versions) + ("\n" if versions else ""))
        return path

    def _removed(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [line.strip() for line in f if line.strip()]

    def test_keeps_two_newest_and_removes_older(self):
        self._kernels("6.1.1_1", "6.2.1_1", "6.3.1_1", "6.4.1_1")
        lf = self._write_list("6.1.1_1", "6.2.1_1", "6.3.1_1", "6.4.1_1")
        res = self._run(list_file=lf)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._removed(), ["6.1.1_1", "6.2.1_1"])

    def test_running_kernel_is_never_removed(self):
        # Il kernel in uso è vecchio: deve essere protetto comunque.
        self._kernels("6.1.1_1", "6.2.1_1", "6.3.1_1", "6.4.1_1")
        lf = self._write_list("6.1.1_1", "6.2.1_1", "6.3.1_1", "6.4.1_1")
        res = self._run(running="6.2.1_1", list_file=lf)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._removed(), ["6.1.1_1"])

    def test_fewer_kernels_than_keep_removes_nothing(self):
        self._kernels("6.3.1_1", "6.4.1_1")
        lf = self._write_list("6.3.1_1", "6.4.1_1")
        res = self._run(list_file=lf)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._removed(), [])

    def test_dry_run_removes_nothing(self):
        self._kernels("6.1.1_1", "6.2.1_1", "6.3.1_1", "6.4.1_1")
        lf = self._write_list("6.1.1_1", "6.2.1_1", "6.3.1_1", "6.4.1_1")
        res = self._run("--dry-run", list_file=lf)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._removed(), [])
        self.assertIn("2", res.stdout)

    def test_custom_keep(self):
        self._kernels("6.1.1_1", "6.2.1_1", "6.3.1_1", "6.4.1_1")
        lf = self._write_list("6.1.1_1", "6.2.1_1", "6.3.1_1", "6.4.1_1")
        res = self._run("--keep", "3", list_file=lf)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._removed(), ["6.1.1_1"])

    def test_invalid_keep_is_rejected(self):
        self._kernels("6.1.1_1")
        res = self._run("--keep", "due")
        self.assertEqual(res.returncode, 2)

    def test_list_mode_does_not_remove(self):
        self._kernels("6.1.1_1", "6.4.1_1")
        lf = self._write_list("6.1.1_1")
        res = self._run("--list", list_file=lf)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._removed(), [])
        self.assertIn("6.4.1_1", res.stdout)
        self.assertIn("in uso", res.stdout)

    def test_missing_vkpurge_removes_nothing(self):
        self._kernels("6.1.1_1", "6.2.1_1", "6.3.1_1", "6.4.1_1")
        # PATH con i soli comandi di base, senza vkpurge.
        base_bin = os.path.join(self.tmp, "basebin")
        os.makedirs(base_bin)
        for tool in ("find", "sed", "grep", "sort", "date", "mkdir", "printf", "cat"):
            src = shutil.which(tool)
            if src:
                os.symlink(src, os.path.join(base_bin, tool))
        env = dict(os.environ)
        env["PATH"] = base_bin
        env["HYLEOS_BOOT_DIR"] = self.boot
        env["HYLEOS_RUNNING_KERNEL"] = "6.4.1_1"
        res = subprocess.run(
            ["/bin/bash", SCRIPT], env=env, capture_output=True, text=True
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("vkpurge non installato", res.stdout)
        self.assertEqual(self._removed(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
