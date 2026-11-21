#!/usr/bin/env python3
"""Test del backend XBPS dello Store (punto 11)."""
import importlib.machinery
import importlib.util
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "out/rootfs-overlay/usr/share/hyleos/xbps_backend.py")


def load_module():
    loader = importlib.machinery.SourceFileLoader("xbps_backend", SCRIPT)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class XbpsBackendTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def _fake(self, stdout="", stderr="", rc=0):
        def _run(cmd, timeout=60, env=None):
            return subprocess.CompletedProcess(cmd, rc, stdout, stderr)

        self.mod._run = _run

    def test_split_pkgver(self):
        self.assertEqual(self.mod.split_pkgver("firefox-160.0_1"), ("firefox", "160.0_1"))
        self.assertEqual(self.mod.split_pkgver("lib2geom-1.2_1"), ("lib2geom", "1.2_1"))
        self.assertEqual(self.mod.split_pkgver("bash"), ("bash", ""))

    def test_protected_list(self):
        for pkg in ("base-system", "hyprland", "hyleos-dms", "quickshell",
                    "NetworkManager", "pipewire", "elogind",
                    "linux6.18", "linux6.19", "linux-firmware"):
            self.assertTrue(self.mod.is_protected(pkg), pkg)
        self.assertFalse(self.mod.is_protected("firefox"))

    def test_preflight_allows_plain_install(self):
        self._fake(stdout="zsh-5.9.2_1 install x86_64 repo 1 1\n")
        ok, msg, needs = self.mod.preflight("zsh")
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertFalse(needs)

    def test_preflight_blocks_core_update(self):
        self._fake(
            stdout=(
                "zsh-5.9.2_1 install x86_64 repo 1 1\n"
                "glibc-2.41_1 update x86_64 repo 1 1\n"
                "xbps-0.60_1 update x86_64 repo 1 1\n"
                "linux6.18-6.18.5_1 update x86_64 repo 1 1\n"
            )
        )
        ok, msg, needs = self.mod.preflight("zsh")
        self.assertFalse(ok)
        self.assertTrue(needs)
        self.assertIn("glibc", msg)
        self.assertIn("xbps", msg)
        self.assertIn("linux6.18", msg)

    def test_preflight_already_installed(self):
        self._fake(stdout="", stderr="ERROR: Package `bash' already installed.", rc=0)
        ok, _msg, needs = self.mod.preflight("bash")
        self.assertTrue(ok)
        self.assertFalse(needs)

    def test_preflight_failure_requests_update_when_db_is_stale(self):
        self._fake(stderr="ERROR: repository data is out of date", rc=1)
        ok, _msg, needs = self.mod.preflight("zsh")
        self.assertFalse(ok)
        self.assertTrue(needs)

    def test_preflight_rejects_bad_name(self):
        ok, _msg, _needs = self.mod.preflight("zsh; rm -rf /")
        self.assertFalse(ok)

    def test_can_sync_repos_requires_root_or_writable_db(self):
        import types

        self.mod.os = types.SimpleNamespace(
            geteuid=lambda: 1000, access=lambda path, mode: False, W_OK=2
        )
        self.assertFalse(self.mod._can_sync_repos())
        self.mod.os = types.SimpleNamespace(
            geteuid=lambda: 0, access=lambda path, mode: False, W_OK=2
        )
        self.assertTrue(self.mod._can_sync_repos())

    def test_preflight_unprivileged_uses_cache_without_sync(self):
        self.mod._can_sync_repos = lambda: False
        seen = []

        def _run(cmd, timeout=60, env=None):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "zsh-5.9.2_1 install x86_64 repo 1 1\n", "")

        self.mod._run = _run
        ok, _msg, _needs = self.mod.preflight("zsh")
        self.assertTrue(ok)
        self.assertEqual(seen[0][1], "-ny")

    def test_preflight_root_uses_sync(self):
        self.mod._can_sync_repos = lambda: True
        seen = []

        def _run(cmd, timeout=60, env=None):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "zsh-5.9.2_1 install x86_64 repo 1 1\n", "")

        self.mod._run = _run
        self.mod.preflight("zsh")
        self.assertEqual(seen[0][1], "-Sny")

    def test_preflight_unprivileged_failure_is_deferred_to_root_helper(self):
        self.mod._can_sync_repos = lambda: False

        def _run(cmd, timeout=60, env=None):
            return subprocess.CompletedProcess(cmd, 1, "", "ERROR: permission denied")

        self.mod._run = _run
        ok, msg, needs = self.mod.preflight("zsh")
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertFalse(needs)

    def test_preflight_unprivileged_still_blocks_core_update(self):
        self.mod._can_sync_repos = lambda: False

        def _run(cmd, timeout=60, env=None):
            return subprocess.CompletedProcess(
                cmd, 0, "zsh-5.9.2_1 install x86_64 repo 1 1\nglibc-2.41_1 update x86_64 repo 1 1\n", ""
            )

        self.mod._run = _run
        ok, msg, needs = self.mod.preflight("zsh")
        self.assertFalse(ok)
        self.assertTrue(needs)
        self.assertIn("glibc", msg)

    def test_search_parses_xbps_output(self):
        self._fake(
            stdout=(
                "[-] firefox-160.0_1                          Mozilla Firefox web browser\n"
                "[-] firefox-esr-140.17.0_1                   Firefox ESR\n"
                "[-] firefox-160.0_1                          duplicato\n"
            )
        )
        res = self.mod.search("firefox")
        self.assertEqual([r[2] for r in res], ["firefox", "firefox-esr"])

    def test_remove_protected_never_calls_helper(self):
        called = []

        def _run(cmd, timeout=60, env=None):
            called.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        self.mod._run = _run
        ok, msg = self.mod.remove("hyprland")
        self.assertFalse(ok)
        self.assertIn("protetto", msg)
        self.assertEqual(called, [])

    def test_catalog_puts_recommended_first_and_no_duplicates(self):
        self.mod.installed_packages = lambda: {"firefox": "1", "htop": "2", "zsh": "3"}
        cat = self.mod.catalog()
        names = [e[2] for e in cat]
        self.assertEqual(names[0], "firefox")
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("zsh", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
