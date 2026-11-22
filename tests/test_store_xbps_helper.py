#!/usr/bin/env python3
"""Test dell'helper XBPS: simulazione -Rny prima della rimozione reale.

Usa un finto xbps-remove nel PATH: nessun pacchetto reale viene toccato.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(ROOT, "out/rootfs-overlay/usr/libexec/hyleos/hyleos-store-xbps")


class StoreXbpsHelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.bin = os.path.join(self.tmp.name, "bin")
        os.mkdir(self.bin)
        self.log = os.path.join(self.tmp.name, "remove.log")
        self._write_fake()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_fake(self):
        path = os.path.join(self.bin, "xbps-remove")
        with open(path, "w") as handle:
            handle.write(
                "#!/bin/sh\n"
                'case "$1" in\n'
                "  -Rny)\n"
                '    printf \'%s\\n\' "$2-1.0_1 remove x86_64 repo 1 1"\n'
                '    if [ "${HYLEOS_FAKE_SAFE:-0}" != "1" ]; then\n'
                '      printf \'%s\\n\' "hyprland-0.57.1_1 remove x86_64 repo 1 1"\n'
                "    fi\n"
                "    ;;\n"
                "  -Ry)\n"
                '    printf \'REAL_REMOVE %s\\n\' "$2" >> "$HYLEOS_FAKE_LOG"\n'
                "    ;;\n"
                "  *) exit 2 ;;\n"
                "esac\n"
            )
        os.chmod(path, 0o755)

    def _run(self, safe=False):
        env = dict(os.environ)
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env["HYLEOS_FAKE_LOG"] = self.log
        env["HYLEOS_FAKE_SAFE"] = "1" if safe else "0"
        return subprocess.run(
            ["sh", HELPER, "remove", "firefox"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_remove_blocks_when_preview_touches_protected(self):
        res = self._run()
        self.assertEqual(res.returncode, 6, res.stderr)
        self.assertIn("hyprland", res.stderr)
        self.assertFalse(os.path.exists(self.log))

    def test_remove_runs_when_preview_is_clean(self):
        res = self._run(safe=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(self.log) as handle:
            self.assertIn("REAL_REMOVE firefox", handle.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
