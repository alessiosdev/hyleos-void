#!/usr/bin/env python3
"""Test del flusso di aggiornamento: AppImage come utente, sistema con XBPS."""
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY = os.path.join(ROOT, "out/rootfs-overlay")

USER_UPDATE = "usr/bin/hyleos-user-update"
SYSTEM_UPDATE = "usr/bin/hyleos-system-update"
HYLE_CLI = "usr/bin/hyle"
WELCOME = "usr/share/hyleos/welcome.py"
APPIMAGE_UPDATE = "usr/bin/hyleos-appimage-update"
HYPRLAND_LUA = "etc/skel/.config/hypr/hyprland.lua"

# Vecchio backend costruito a pezzi: non va riscritto letteralmente nei sorgenti.
OLD_BACKEND = "flat" + "pak"
OLD_HUB = "Fl" + "athub"


class UserUpdateTest(unittest.TestCase):
    def read(self, relpath):
        with open(os.path.join(OVERLAY, relpath), encoding="utf-8") as handle:
            return handle.read()

    def test_user_update_has_no_privilege_escalation(self):
        text = self.read(USER_UPDATE)
        for tool in ("pkexec", "doas", "sudo", "exec "):
            self.assertNotIn(tool, text, "%s in %s" % (tool, USER_UPDATE))

    def test_appimage_update_runs_before_xbps_system_update(self):
        text = self.read(USER_UPDATE)
        appimage = "/usr/bin/hyleos-appimage-update"
        system = "/usr/bin/hyleos-system-update"
        # Lo script usa variabili: risolviamo i percorsi per verificare l'ordine.
        self.assertIn("APPIMAGE_UPDATER=" + appimage, text)
        self.assertIn("SYSTEM_UPDATER=" + system, text)
        # Nuova forma: chiamata diretta (senza exec) e propagazione del codice.
        system_call = '"$SYSTEM_UPDATER" "$@"'
        self.assertIn(system_call, text)
        self.assertIn('exit "$rc"', text)

        resolved = text.replace('"$APPIMAGE_UPDATER"', appimage)
        resolved = resolved.replace(system_call, system + ' "$@"')
        match = re.search(r'"?%s"?\s+"\$@"' % re.escape(appimage), resolved)
        appimage_call = match.start() if match else -1
        system_at = resolved.find(system + ' "$@"')
        self.assertGreater(appimage_call, -1, "chiamata ad hyleos-appimage-update mancante")
        self.assertGreater(system_at, -1, "chiamata a hyleos-system-update mancante")
        self.assertLess(appimage_call, system_at, "AppImage aggiornate dopo XBPS")

    # ------------------------------------------------------------------
    # Esecuzione dinamica di una copia temporanea: nessun file di /usr/bin
    # viene toccato e i comandi sono sostituiti da fake nella tempdir.
    # ------------------------------------------------------------------

    def _write_executable(self, path, content):
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(path, 0o755)
        return path

    def _run_user_update(self, uid, system_rc, args=()):
        """Esegue lo script con fake appimage/system/id; ritorna CompletedProcess."""
        with tempfile.TemporaryDirectory(prefix="hyleos-user-update-test-") as tmp:
            bindir = os.path.join(tmp, "bin")
            os.makedirs(bindir)

            appimage = self._write_executable(
                os.path.join(bindir, "fake-appimage-update"),
                "#!/bin/sh\necho APPIMAGE_PHASE_OK\nexit 0\n",
            )
            system = self._write_executable(
                os.path.join(bindir, "fake-system-update"),
                "#!/bin/sh\necho SYSTEM_PHASE\nexit %d\n" % system_rc,
            )
            # La suite puo' girare come root: un fake `id` simula l'utente.
            self._write_executable(
                os.path.join(bindir, "id"), "#!/bin/sh\necho %d\n" % uid
            )

            source = self.read(USER_UPDATE)
            patched, appimage_count = re.subn(
                r"(?m)^APPIMAGE_UPDATER=.*$", "APPIMAGE_UPDATER=" + appimage, source
            )
            patched, system_count = re.subn(
                r"(?m)^SYSTEM_UPDATER=.*$", "SYSTEM_UPDATER=" + system, patched
            )
            self.assertEqual(appimage_count, 1, "APPIMAGE_UPDATER= non univoco")
            self.assertEqual(system_count, 1, "SYSTEM_UPDATER= non univoco")

            copy = self._write_executable(
                os.path.join(tmp, "hyleos-user-update"), patched
            )
            env = os.environ.copy()
            env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
            return subprocess.run(
                ["/bin/sh", copy, *args],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
                check=False,
            )

    def _assert_phase_order(self, result):
        self.assertIn("APPIMAGE_PHASE_OK", result.stdout)
        self.assertIn("SYSTEM_PHASE", result.stdout)
        self.assertLess(
            result.stdout.index("APPIMAGE_PHASE_OK"),
            result.stdout.index("SYSTEM_PHASE"),
            "fase AppImage non preceduta rispetto a quella di sistema",
        )

    def test_wrapper_success_has_no_failure_message(self):
        result = self._run_user_update(1000, 0)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("fallito", output)
        self.assertNotIn("annullato", output)
        self.assertIn("AppImage: processati.", output)
        self._assert_phase_order(result)

    def test_wrapper_polkit_cancel_non_root(self):
        result = self._run_user_update(1000, 126)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 126)
        self.assertIn("annullato o non autorizzato (Polkit, codice 126)", output)
        self.assertIn("AppImage: processati.", output)
        self.assertNotIn("completato", output)
        self._assert_phase_order(result)

    def test_wrapper_generic_failure_non_root(self):
        result = self._run_user_update(1000, 1)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1)
        self.assertIn("fallito (codice 1)", output)
        self.assertIn("AppImage: processati.", output)
        self.assertNotIn("completato", output)
        self._assert_phase_order(result)

    def test_wrapper_polkit_cancel_root_is_a_failure(self):
        result = self._run_user_update(0, 126)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 126)
        self.assertIn("saltati (root)", output)
        self.assertIn("fallito (codice 126)", output)
        self.assertNotIn("annullato", output)
        self.assertNotIn("APPIMAGE_PHASE_OK", result.stdout)

    def test_wrapper_check_with_rc1_is_silent(self):
        result = self._run_user_update(1000, 1, args=("--check",))
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1)
        self.assertIn("AppImage: verificati.", output)
        self.assertNotIn("fallito", output)
        self._assert_phase_order(result)

    def test_no_old_backend_in_update_scripts(self):
        for relpath in (SYSTEM_UPDATE, HYLE_CLI, WELCOME, APPIMAGE_UPDATE):
            text = self.read(relpath).lower()
            self.assertNotIn(OLD_BACKEND, text, relpath)
            self.assertNotIn(OLD_HUB.lower(), text, relpath)

    def test_xwayland_is_enabled(self):
        text = self.read(HYPRLAND_LUA)
        match = re.search(r"xwayland\s*=\s*\{(.*?)\}", text, re.S)
        self.assertIsNotNone(match, "tabella xwayland mancante")
        self.assertRegex(match.group(1), r"\benabled\s*=\s*true\b")


if __name__ == "__main__":
    unittest.main()
