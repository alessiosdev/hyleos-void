#!/usr/bin/env python3
"""Test offline del launcher pkexec/polkit dell'installer HyleOS.

Nessuna elevazione reale: ``id``, ``pkexec`` e ``python3`` sono stub creati in
una PATH temporanea. Il wrapper sotto test e' una copia in tmpdir, l'overlay
non viene modificato.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY = os.path.join(ROOT, "out", "rootfs-overlay")
WRAPPER_SRC = os.path.join(OVERLAY, "usr", "bin", "hyleos-installer")
POLICY = os.path.join(OVERLAY, "usr", "share", "polkit-1", "actions",
                      "org.hyleos.installer.policy")
RULE = os.path.join(OVERLAY, "usr", "share", "polkit-1", "rules.d",
                    "50-hyleos-installer-live.rules")
APPLICATIONS_DIR = os.path.join(OVERLAY, "usr", "share", "applications")
GENERATED_APPLICATIONS = (
    os.path.join(ROOT, "packages", "hyleos-installer", "files", "applications"),
)
PACKAGE_TEMPLATE = os.path.join(ROOT, "packages", "hyleos-installer", "template")
GENERATED_WRAPPER = os.path.join(ROOT, "packages", "hyleos-installer", "files",
                                 "bin", "hyleos-installer")
GENERATED_RULE = os.path.join(ROOT, "packages", "hyleos-installer", "files",
                              "polkit", "50-hyleos-installer-live.rules")
CANONICAL_DESKTOP = "org.hyleos.installer.desktop"
ALIAS_DESKTOP = "hyleos-installer.desktop"
LAUNCH_LOG = "/tmp/hyleos-installer-launch.log"
PY_PATH = "/usr/share/hyleos/installer.py"
SESSION_KEYS = (
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_SESSION_TYPE",
    "HYPRLAND_INSTANCE_SIGNATURE",
    "DISPLAY",
    "XAUTHORITY",
)
SESSION_VALUES = {
    "WAYLAND_DISPLAY": "wayland-7",
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    "XDG_SESSION_TYPE": "wayland",
    "HYPRLAND_INSTANCE_SIGNATURE": "sig-xyz",
    "DISPLAY": ":9",
    "XAUTHORITY": "/tmp/fake-xauth",
}


ID_STUB = """#!/bin/sh
echo "${STUB_UID:-1000}"
"""

PKEXEC_STUB = """#!/bin/sh
printf '%s\\n' "$@" > "$PKEXEC_RECORD"
export STUB_UID=0
if [ -n "${PKEXEC_FAIL:-}" ]; then
    echo "pkexec: autorizzazione negata" >&2
    exit "$PKEXEC_FAIL"
fi
exec "$@"
"""

PYTHON_STUB = """#!/bin/sh
{
  echo "ARGS"
  printf '<%s>\\n' "$@"
  echo "ENV"
  for key in WAYLAND_DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS \\
             XDG_SESSION_TYPE HYPRLAND_INSTANCE_SIGNATURE DISPLAY XAUTHORITY; do
    eval "value=\\${$key-__UNSET__}"
    printf '%s=%s\\n' "$key" "$value"
  done
} > "$LAUNCH_RECORD"
"""


def write_stub(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def parse_record(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    args = []
    env = {}
    section = None
    for line in lines:
        if line == "ARGS":
            section = "args"
            continue
        if line == "ENV":
            section = "env"
            continue
        if section == "args" and line.startswith("<") and line.endswith(">"):
            args.append(line[1:-1])
        elif section == "env" and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return args, env


def installer_desktop_entries(directory):
    entries = []
    if not os.path.isdir(directory):
        return entries
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not name.endswith(".desktop") or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            if any(line.strip() == "Name=Installa Hyle OS" for line in fh):
                entries.append(name)
    return entries


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hyleos-launcher-")
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.stub_dir = os.path.join(self.base, "stub")
        os.makedirs(self.stub_dir, exist_ok=True)
        write_stub(os.path.join(self.stub_dir, "id"), ID_STUB)
        write_stub(os.path.join(self.stub_dir, "pkexec"), PKEXEC_STUB)
        write_stub(os.path.join(self.stub_dir, "python3"), PYTHON_STUB)
        self.wrapper = os.path.join(self.base, "hyleos-installer")
        shutil.copy2(WRAPPER_SRC, self.wrapper)
        os.chmod(self.wrapper, 0o755)
        self.launch_record = os.path.join(self.base, "python.record")
        self.pkexec_record = os.path.join(self.base, "pkexec.record")
        self._remove_launch_log()
        self.addCleanup(self._remove_launch_log)

    def _remove_launch_log(self):
        try:
            os.unlink(LAUNCH_LOG)
        except OSError:
            pass

    def _launch_log(self):
        with open(LAUNCH_LOG, encoding="utf-8") as fh:
            return fh.read()

    def _run(self, argv, uid, expected_rc=0, extra_env=None, path_dirs=None):
        env = dict(os.environ)
        if path_dirs is None:
            path_dirs = self.stub_dir + os.pathsep + "/usr/bin:/bin"
        env["PATH"] = path_dirs
        env["STUB_UID"] = str(uid)
        env["LAUNCH_RECORD"] = self.launch_record
        env["PKEXEC_RECORD"] = self.pkexec_record
        env.update(SESSION_VALUES)
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run([self.wrapper] + list(argv), env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=20)
        self.assertEqual(proc.returncode, expected_rc,
                         f"wrapper rc={proc.returncode}: {proc.stderr}")
        return proc

    def _pkexec_argv(self):
        self.assertTrue(os.path.isfile(self.pkexec_record), "pkexec non invocato")
        with open(self.pkexec_record, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def test_non_root_no_tty_uses_pkexec_with_seven_session_vars(self):
        self._run(["--hyleos-env", "EVIL=1", "--hyleos-env",
                   "WAYLAND_DISPLAY=spoofed", "--keep", "value with space"], uid=1000)
        recorded = self._pkexec_argv()
        self.assertEqual(recorded[0], self.wrapper)
        self.assertEqual(recorded.count("--hyleos-env"), 7)
        expected_pairs = ["%s=%s" % (key, SESSION_VALUES[key]) for key in SESSION_KEYS]
        for pair in expected_pairs:
            self.assertIn(pair, recorded)
        self.assertEqual([recorded[i + 1] for i, arg in enumerate(recorded)
                          if arg == "--hyleos-env"], expected_pairs)
        self.assertEqual(recorded[-2:], ["--keep", "value with space"])
        self.assertNotIn("EVIL=1", recorded)
        self.assertNotIn("spoofed", recorded)

        python_args, env = parse_record(self.launch_record)
        self.assertEqual(python_args, [PY_PATH, "--keep", "value with space"])
        self.assertNotIn("--hyleos-env", python_args)
        for key in SESSION_KEYS:
            self.assertEqual(env[key], SESSION_VALUES[key])

    def test_root_runs_python_without_hyleos_env_arguments(self):
        self._run(["--hyleos-env", "WAYLAND_DISPLAY=root-value",
                   "--hyleos-env", "NOT_ALLOWED=nope",
                   "--hyleos-env", "DISPLAY=:1", "--only-arg"], uid=0)
        self.assertFalse(os.path.exists(self.pkexec_record),
                         "pkexec non deve essere usato da root")
        python_args, env = parse_record(self.launch_record)
        self.assertEqual(python_args, [PY_PATH, "--only-arg"])
        self.assertNotIn("--hyleos-env", python_args)
        self.assertEqual(env["WAYLAND_DISPLAY"], "root-value")
        self.assertEqual(env["DISPLAY"], ":1")
        self.assertEqual(env["XDG_RUNTIME_DIR"], SESSION_VALUES["XDG_RUNTIME_DIR"])
        self.assertNotIn("NOT_ALLOWED", env)

    def test_non_root_discards_injected_hyleos_env_pairs(self):
        self._run(["--hyleos-env", "DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/evil",
                   "--hyleos-env", "XAUTHORITY=/tmp/evil-xauth"], uid=1000)
        recorded = self._pkexec_argv()
        self.assertEqual(recorded.count("--hyleos-env"), 7)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/evil", recorded)
        self.assertNotIn("XAUTHORITY=/tmp/evil-xauth", recorded)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS=" + SESSION_VALUES["DBUS_SESSION_BUS_ADDRESS"], recorded)
        self.assertIn("XAUTHORITY=" + SESSION_VALUES["XAUTHORITY"], recorded)
        _python_args, env = parse_record(self.launch_record)
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], SESSION_VALUES["DBUS_SESSION_BUS_ADDRESS"])
        self.assertEqual(env["XAUTHORITY"], SESSION_VALUES["XAUTHORITY"])

    def test_non_root_uses_wayland_defaults_when_session_vars_are_empty(self):
        self._run([], uid=1000,
                  extra_env={"WAYLAND_DISPLAY": "", "XDG_RUNTIME_DIR": ""})
        recorded = self._pkexec_argv()
        self.assertIn("WAYLAND_DISPLAY=wayland-0", recorded)
        self.assertIn("XDG_RUNTIME_DIR=/run/user/1000", recorded)

    def test_pkexec_launch_writes_log_without_sensitive_values(self):
        self._run(["--password=segreto-uguale", "--keep", "value with space"], uid=1000)
        record = self._launch_log()
        self.assertRegex(record, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z rc=0 ")
        self.assertIn("argv=pkexec ", record)
        self.assertIn("--hyleos-env WAYLAND_DISPLAY=<redacted>", record)
        self.assertRegex(record, r"(?<!\d)args=3(?!\d)")
        for token in ("segreto-uguale", "value with space", "--password", "--keep", "flags="):
            with self.subTest(token=token):
                self.assertNotIn(token, record)
        for value in SESSION_VALUES.values():
            self.assertNotIn(value, record)

    def test_secret_passed_separately_is_never_logged(self):
        self._run(["--password", "-segreto-posizionale"], uid=1000)
        record = self._launch_log()
        self.assertRegex(record, r"(?<!\d)args=2(?!\d)")
        self.assertNotIn("segreto-posizionale", record)
        self.assertNotIn("--password", record)
        for value in SESSION_VALUES.values():
            self.assertNotIn(value, record)

    def test_newline_argument_does_not_add_log_lines(self):
        self._run(["--nota", "prima\nrc=99 argv=INJECTED\nsegreto"], uid=1000)
        lines = self._launch_log().splitlines()
        self.assertEqual(len(lines), 1,
                         "un argomento con newline non deve iniettare righe di log")
        record = lines[0]
        self.assertRegex(record, r"(?<!\d)args=2(?!\d)")
        for token in ("INJECTED", "prima", "segreto", "--nota"):
            with self.subTest(token=token):
                self.assertNotIn(token, record)

    def test_pkexec_failure_is_logged_and_reported(self):
        proc = self._run(["--avvia"], uid=1000, expected_rc=7,
                         extra_env={"PKEXEC_FAIL": "7"})
        self.assertIn("Avvio dell'installer fallito", proc.stderr)
        self.assertIn("codice 7", proc.stderr)
        record = self._launch_log()
        self.assertIn("rc=7 ", record)
        self.assertIn("argv=pkexec ", record)
        for value in SESSION_VALUES.values():
            self.assertNotIn(value, record)

    def test_missing_pkexec_logs_127_and_exits_127(self):
        isolated = os.path.join(self.base, "no-pkexec")
        os.makedirs(isolated, exist_ok=True)
        write_stub(os.path.join(isolated, "id"), ID_STUB)
        write_stub(os.path.join(isolated, "readlink"),
                   '#!/bin/sh\nprintf "%s\\n" "$2"\n')
        write_stub(os.path.join(isolated, "date"),
                   '#!/bin/sh\necho 2026-11-21T00:00:00Z\n')
        proc = self._run(["--avvia"], uid=1000, expected_rc=127,
                         path_dirs=isolated)
        self.assertIn("Servono i privilegi di root", proc.stderr)
        record = self._launch_log()
        self.assertIn("rc=127 ", record)
        self.assertIn("pkexec-non-disponibile", record)

    def test_root_launch_does_not_write_log(self):
        self._run(["--only-arg"], uid=0)
        self.assertFalse(os.path.exists(LAUNCH_LOG),
                         "il ramo root non deve scrivere il log in /tmp (C4)")

    def test_all_elevation_branches_use_the_same_log_function(self):
        with open(WRAPPER_SRC, encoding="utf-8") as fh:
            source = fh.read()
        for fragment in ("python3 \"$PY\" \"$@\"", "doas env", "sudo env",
                         "pkexec \"$SELF\""):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)
        self.assertEqual(source.count('log_launch "$RC"'), 3)
        self.assertEqual(source.count("log_launch 127"), 1)
        for stale in ("exec python3", "exec doas", "exec sudo", "exec pkexec"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, source)

    def test_root_branch_never_writes_launch_log(self):
        with open(WRAPPER_SRC, encoding="utf-8") as fh:
            source = fh.read()
        start = source.index('if [ "$UID_NOW" -eq 0 ]; then')
        end = source.index("\nfi", start)
        root_block = source[start:end]
        self.assertNotIn("log_launch", root_block)
        self.assertNotIn("LAUNCH_LOG", root_block)
        self.assertNotIn(">>", root_block)
        self.assertIn('exit "$RC"', root_block)


class DesktopEntryTest(unittest.TestCase):
    def test_single_installer_desktop_entry_in_overlay_and_payloads(self):
        for directory in (APPLICATIONS_DIR,) + GENERATED_APPLICATIONS:
            with self.subTest(directory=directory):
                self.assertEqual(installer_desktop_entries(directory),
                                 [CANONICAL_DESKTOP])

    def test_alias_symlink_is_absent(self):
        self.assertFalse(os.path.lexists(os.path.join(APPLICATIONS_DIR, ALIAS_DESKTOP)),
                         "alias rimosso per ADR-0007")

    def test_template_installs_only_canonical_desktop(self):
        with open(PACKAGE_TEMPLATE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn(ALIAS_DESKTOP, text)
        self.assertIn(CANONICAL_DESKTOP, text)
        self.assertIn("revision=6", text)


class GeneratedPayloadTest(unittest.TestCase):
    def assert_same_bytes(self, generated, canonical):
        with open(generated, "rb") as fh:
            generated_bytes = fh.read()
        with open(canonical, "rb") as fh:
            canonical_bytes = fh.read()
        self.assertEqual(generated_bytes, canonical_bytes)

    def test_generated_wrapper_matches_canonical_source(self):
        self.assert_same_bytes(GENERATED_WRAPPER, WRAPPER_SRC)

    def test_generated_rule_matches_canonical_source(self):
        self.assert_same_bytes(GENERATED_RULE, RULE)

    def test_generated_rule_has_no_subject_active(self):
        with open(GENERATED_RULE, encoding="utf-8") as fh:
            self.assertNotIn("subject.active", fh.read())

    def test_live_rule_uses_subject_user_property_in_overlay_and_payload(self):
        for path in (RULE, GENERATED_RULE):
            with self.subTest(path=path):
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                self.assertIn('subject.user == "anon"', text)
                self.assertNotRegex(text, r"subject\.user\(\)")


class PolicyTest(unittest.TestCase):
    def test_policy_xml_is_valid_and_action_id_is_correct(self):
        tree = ET.parse(POLICY)
        root = tree.getroot()
        self.assertEqual(root.tag, "policyconfig")
        actions = [a for a in root.findall("action")
                   if a.get("id") == "org.hyleos.installer.launch"]
        self.assertEqual(len(actions), 1)
        action = actions[0]
        defaults = action.find("defaults")
        self.assertIsNotNone(defaults)
        self.assertEqual(defaults.findtext("allow_any"), "no")
        self.assertEqual(defaults.findtext("allow_inactive"), "no")
        self.assertEqual(defaults.findtext("allow_active"), "auth_admin")
        annotations = {
            node.get("key"): (node.text or "").strip()
            for node in action.findall("annotate")
        }
        self.assertEqual(annotations.get("org.freedesktop.policykit.exec.path"),
                         "/usr/bin/hyleos-installer")
        self.assertEqual(annotations.get("org.freedesktop.policykit.exec.allow_gui"), "true")

    def test_live_rule_allows_anon_and_is_inert_elsewhere(self):
        with open(RULE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('action.id == "org.hyleos.installer.launch"', text)
        self.assertIn('subject.user == "anon"', text)
        self.assertNotRegex(text, r"subject\.user\(\)")
        self.assertIn("polkit.Result.YES", text)
        self.assertNotIn("subject.active", text)
        self.assertIn("subject.local", text)
        self.assertIn("polkit.Result.NOT_HANDLED", text)


if __name__ == "__main__":
    unittest.main()
