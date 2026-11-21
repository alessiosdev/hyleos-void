#!/usr/bin/env python3
"""Regression: errori QML che bloccano l'intera shell HyleOS.

Un `signal` con lo stesso nome di una `property` e' un errore fatale in QML
("Duplicate signal name: invalid override of property change signal"). Se
accade in un Singleton usato da shell.qml, la cascata di errori rende
indisponibile DMS: restano solo sfondo e cursore, senza barra ne' dock.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
OVERLAY = ROOT / "out/rootfs-overlay"
TARGETS = ROOT / "build/qml-lint-targets.txt"

PROPERTY_RE = re.compile(r"^\s*(?:readonly\s+)?property\s+[\w<>\[\].]+\s+(\w+)\s*(?::|=|$)", re.M)
SIGNAL_RE = re.compile(r"^\s*signal\s+(\w+)\s*\(", re.M)


def lint_targets():
    for line in TARGETS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            yield line


class QmlSingletonTest(unittest.TestCase):
    def test_all_lint_targets_exist(self):
        for rel in lint_targets():
            self.assertTrue((OVERLAY / rel).is_file(), f"QML mancante: {rel}")

    def test_no_signal_shadows_a_property(self):
        for rel in lint_targets():
            text = (OVERLAY / rel).read_text()
            properties = set(PROPERTY_RE.findall(text))
            signals = set(SIGNAL_RE.findall(text))
            clash = sorted(properties & signals)
            self.assertFalse(clash, f"{rel}: segnali in conflitto con property: {clash}")

    def test_hyleos_service_uses_non_conflicting_signal(self):
        text = (OVERLAY / "etc/xdg/quickshell/hyleos-dms/Services/HyleOSService.qml").read_text()
        self.assertIn("signal modeChanged(string mode)", text)
        self.assertNotIn("signal windowModeChanged", text)


if __name__ == "__main__":
    unittest.main()
