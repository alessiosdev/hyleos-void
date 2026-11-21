#!/usr/bin/env python3
"""Test del rilevamento firma repository (punto 4).

XBPS firma l'indice dentro x86_64-repodata (tar con index-meta.plist) e ogni
pacchetto con un .sig2: questi test bloccano i due errori facili (cercare un
.sig che non esiste, oppure ignorare i pacchetti non firmati).
"""
import io
import os
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "build/lib-repo.sh")
REPO_KEY = os.path.join(ROOT, "build/repo-key.sh")


class RepoSignatureTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hyleos-repo-test.")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _state(self):
        res = subprocess.run(
            ["sh", "-c", '. "$1"; repo_signature_state "$2"', "sh", LIB, self.dir],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def _repodata(self, signed=True):
        members = {"index.plist": plistlib.dumps({})}
        if signed:
            members["index-meta.plist"] = plistlib.dumps(
                {"signature-by": "Test", "public-key": b"fake"}
            )
        self._repodata_tar(members)

    def _repodata_tar(self, members, directory=None):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        target = directory or self.dir
        with open(os.path.join(target, "x86_64-repodata"), "wb") as f:
            f.write(buf.getvalue())

    def _pkg(self, sig=True):
        pkg = os.path.join(self.dir, "hyle-1.0.0_1.x86_64.xbps")
        with open(pkg, "wb") as f:
            f.write(b"fake")
        if sig:
            with open(pkg + ".sig2", "wb") as f:
                f.write(b"fake-signature")

    def test_signed_index_and_packages(self):
        self._repodata(signed=True)
        self._pkg(sig=True)
        self.assertEqual(self._state(), "signed")

    def test_unsigned_index(self):
        self._repodata(signed=False)
        self._pkg(sig=True)
        self.assertEqual(self._state(), "unsigned")

    def test_signed_index_but_unsigned_package(self):
        self._repodata(signed=True)
        self._pkg(sig=False)
        self.assertEqual(self._state(), "unsigned")

    def test_missing_index(self):
        self._pkg(sig=True)
        self.assertEqual(self._state(), "unsigned")

    def test_build_repo_cleans_before_signing(self):
        with open(os.path.join(ROOT, "build/build-repo.sh"), encoding="utf-8") as f:
            script = f.read()
        clean = script.index('rm -f "$HYLEOS_REPO_DIR"/*-repodata*')
        add = script.index('xbps-rindex -a "$HYLEOS_REPO_DIR"')
        sign_pkg = script.index('-S "$f"')
        sign_idx = script.index('-s "$HYLEOS_REPO_DIR"')
        self.assertLess(clean, add)
        self.assertLess(add, sign_pkg)
        self.assertLess(sign_pkg, sign_idx)

    def test_build_repo_passes_key_explicitly(self):
        with open(os.path.join(ROOT, "build/build-repo.sh"), encoding="utf-8") as f:
            script = f.read()
        # La chiave si risolve dall'HOME dell'utente che lancia la build.
        self.assertIn('HYLEOS_PRIVKEY="${HYLEOS_PRIVKEY:-$HOME/.ssh/id_rsa}"', script)
        self.assertIn('--privkey "$HYLEOS_PRIVKEY"', script)
        # Dopo la firma lo script si autoverifica prima di dichiarare il repo pronto.
        self.assertIn('repo_signature_state "$HYLEOS_REPO_DIR"', script)

    def test_build_repo_does_not_elevate(self):
        # xbps-src rifiuta root: lo script deve girare come utente normale e
        # fermarsi con un messaggio chiaro se lanciato da root.
        with open(os.path.join(ROOT, "build/build-repo.sh"), encoding="utf-8") as f:
            script = f.read()
        self.assertNotIn("exec \"$PRIV\"", script)
        self.assertNotIn("doas env", script)
        self.assertIn('[ "$(id -u)" -eq 0 ]', script)
        self.assertIn("xbuilder", script)

    def test_iso_and_publish_use_real_signature_detection(self):
        for name in ("build/mkiso.sh", "build/publish-repo.sh"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                script = f.read()
            self.assertIn("repo_signature_state", script, name)
            self.assertNotIn("*-repodata.sig*", script, name)

    def test_repo_key_writes_plist_named_by_fingerprint(self):
        key = os.path.join(self.dir, "key.pem")
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "rsa", "-b", "2048", "-m", "PEM", "-N", "", "-f", key],
            check=True,
        )
        pub = subprocess.run(
            ["openssl", "rsa", "-in", key, "-pubout"], capture_output=True, check=True
        ).stdout
        repodir = os.path.join(self.dir, "repo")
        os.makedirs(repodir)
        self._repodata_tar(
            {
                "index.plist": plistlib.dumps({}),
                "index-meta.plist": plistlib.dumps(
                    {"public-key": pub, "signature-by": "Test <t@example.org>"}
                ),
            },
            repodir,
        )

        out = os.path.join(self.dir, "keys")
        res = subprocess.run(
            [REPO_KEY, "--out", out, repodir], capture_output=True, text=True, check=True
        )
        path = res.stdout.strip()
        self.assertTrue(os.path.isfile(path), path)

        ssh = subprocess.run(
            ["ssh-keygen", "-y", "-f", key], capture_output=True, check=True
        ).stdout
        fpr = subprocess.run(
            ["ssh-keygen", "-E", "md5", "-lf", "-"], input=ssh, capture_output=True, check=True
        ).stdout.decode()
        expected = fpr.split()[1][len("MD5:"):]
        self.assertEqual(os.path.basename(path), expected + ".plist")

        with open(path, "rb") as f:
            self.assertEqual(plistlib.load(f)["public-key"], pub)

    def test_repo_key_refuses_unsigned_index(self):
        self._repodata_tar({"index.plist": plistlib.dumps({})})
        res = subprocess.run(
            [REPO_KEY, "--out", os.path.join(self.dir, "keys"), self.dir],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
