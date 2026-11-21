#!/usr/bin/env python3
"""Test di coerenza del packaging (punti 4, 5, 6).

Non esegue xbps-src: verifica che i template e la build non regrediscano verso
il vecchio repository esterno o verso gli shim.
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "packages")
MKISO = os.path.join(ROOT, "build/mkiso.sh")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class PackagingTest(unittest.TestCase):
    def test_no_external_hyprland_repo_or_shims(self):
        mkiso = read(MKISO)
        # I commenti possono citare la storia; il codice no.
        code = "\n".join(
            line for line in mkiso.splitlines() if not line.lstrip().startswith("#")
        )
        for needle in (
            "sofijacom",
            "libdisplay-info0.2",
            "libqt6-private-api",
            "REPO_HYPRLAND",
        ):
            self.assertNotIn(needle, code, f"mkiso.sh usa ancora {needle}")

    def test_mkiso_uses_own_repository(self):
        mkiso = read(MKISO)
        self.assertIn("HYLEOS_REPO_DIR", mkiso)
        self.assertIn("x86_64-repodata", mkiso)
        self.assertIn("HYLEOS_REPO_PKGS", mkiso)
        # Tutti i pacchetti HyleOS devono finire nella live, altrimenti i file
        # restano non registrati in XBPS e gli update non li toccano.
        for pkg in (
            "hyleos-base",
            "hyleos-theme",
            "hyleos-dms",
            "hyleos-session",
            "hyleos-installer",
            "hyleos-store",
            "hyle",
            "hyleos-desktop",
        ):
            self.assertIn(pkg, mkiso, f"{pkg} non è in HYLEOS_REPO_PKGS")

    def test_dms_pins_exact_quickshell_version(self):
        tpl = read(os.path.join(PKG, "hyleos-dms/template"))
        # XBPS esprime un pin esatto come "nome-versione_revisione": la forma
        # "nome=versione" non è un pattern valido e la build la rifiuta.
        self.assertRegex(tpl, r"quickshell-0\.3\.2_1")
        tpl_desktop = read(os.path.join(PKG, "hyleos-desktop/template"))
        self.assertRegex(tpl_desktop, r"quickshell-0\.3\.2_1")

    def test_dms_package_version_follows_overlay(self):
        tpl = read(os.path.join(PKG, "hyleos-dms/template"))
        version = read(
            os.path.join(
                ROOT,
                "out/rootfs-overlay/etc/xdg/quickshell/hyleos-dms/VERSION",
            )
        ).strip()
        # Il file upstream può avere il prefisso "v"; XBPS non lo accetta.
        version = version.lstrip("v")
        m = re.search(r"^version=(\S+)$", tpl, re.M)
        self.assertIsNotNone(m, "version mancante nel template hyleos-dms")
        self.assertEqual(m.group(1), version)

    def test_every_template_source_exists(self):
        problems = []
        for entry in sorted(os.listdir(PKG)):
            tpl = os.path.join(PKG, entry, "template")
            if not os.path.isfile(tpl):
                continue
            files = os.path.join(PKG, entry, "files")
            text = read(tpl)
            for line in text.splitlines():
                line = line.strip()
                m = re.match(r"(?:vinstall|vcopy)\s+\$\{FILESDIR\}/(\S+)", line)
                if not m:
                    continue
                if not os.path.exists(os.path.join(files, m.group(1))):
                    problems.append(f"{entry}: files/{m.group(1)}")
        self.assertEqual(problems, [], "sorgenti mancanti: " + ", ".join(problems))

    def test_meta_packages_are_present(self):
        for pkg in ("hyleos-base", "hyleos-desktop"):
            self.assertTrue(
                os.path.isfile(os.path.join(PKG, pkg, "template")),
                f"template {pkg} mancante",
            )

    def test_protected_list_in_sync(self):
        helper = read(
            os.path.join(
                ROOT, "out/rootfs-overlay/usr/libexec/hyleos/hyleos-store-xbps"
            )
        )
        backend = read(
            os.path.join(ROOT, "out/rootfs-overlay/usr/share/hyleos/xbps_backend.py")
        )
        for pkg in (
            "base-system",
            "hyprland",
            "hyleos-dms",
            "quickshell",
            "NetworkManager",
            "pipewire",
            "elogind",
        ):
            self.assertIn(pkg, helper, f"{pkg} assente dall'helper root")
            self.assertIn(pkg, backend, f"{pkg} assente dal backend Python")

    def test_session_ships_updater_polkit_action(self):
        template = read(os.path.join(PKG, "hyleos-session/template"))
        self.assertIn("org.hyleos.update.system.policy", template)
        self.assertIn("polkit", template)
        policy = read(
            os.path.join(
                ROOT,
                "out/rootfs-overlay/usr/share/polkit-1/actions/"
                "org.hyleos.update.system.policy",
            )
        )
        self.assertIn("/usr/bin/hyleos-system-update", policy)

    def test_root_helper_protects_future_kernels(self):
        helper = os.path.join(
            ROOT, "out/rootfs-overlay/usr/libexec/hyleos/hyleos-store-xbps"
        )
        for pkg in ("linux6.19", "linux-firmware", "hyprland", "quickshell"):
            res = subprocess.run(
                ["sh", helper, "remove", pkg],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 3, f"{pkg}: {res.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
