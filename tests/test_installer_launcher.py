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

    def _run(self, argv, uid):
        env = dict(os.environ)
        env["PATH"] = self.stub_dir + os.pathsep + "/usr/bin:/bin"
        env["STUB_UID"] = str(uid)
        env["LAUNCH_RECORD"] = self.launch_record
        env["PKEXEC_RECORD"] = self.pkexec_record
        env.update(SESSION_VALUES)
        proc = subprocess.run([self.wrapper] + list(argv), env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0,
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
        self.assertIn('subject.user() == "anon"', text)
        self.assertIn("polkit.Result.YES", text)
        self.assertIn("subject.active", text)
        self.assertIn("subject.local", text)
        self.assertIn("polkit.Result.NOT_HANDLED", text)


if __name__ == "__main__":
    unittest.main()
