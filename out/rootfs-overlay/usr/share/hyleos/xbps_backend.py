#!/usr/bin/env python3
"""Backend XBPS per HyleOS Store.

Regole:

  * XBPS è il backend prioritario per le applicazioni desktop; le app
    portatili non presenti nei repository sono gestite come AppImage utente.
  * Prima di installare si esegue una simulazione XBPS: `-Sny <pkg>` se si
    possono sincronizzare i repository (root o /var/db/xbps scrivibile),
    altrimenti `-ny <pkg>` sulla cache locale. Il controllo autorevole resta
    nel helper root, che rifà sempre `-Sny`. Se la transazione richiede di
    aggiornare xbps o componenti di base, l'installazione singola viene
    bloccata e l'utente invita ad aggiornare prima il sistema.
  * I componenti vitali non sono disinstallabili dallo Store.

Il controllo autorevole è comunque nel helper root
/usr/libexec/hyleos/hyleos-store-xbps: questo modulo serve a dare un messaggio
chiaro subito, senza chiedere la password per poi fallire.
"""

import os
import re
import shutil
import subprocess

# Componenti che non devono mai essere rimossi dallo Store. Duplicata nel
# helper root: il client non è fidato.
PROTECTED_PACKAGES = {
    "base-system",
    "hyprland",
    "hyleos-dms",
    "hyleos-desktop",
    "hyleos-store",
    "hyleos-session",
    "quickshell",
    "NetworkManager",
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "elogind",
    "dbus",
    "xbps",
    "glibc",
    "linux",
    "linux6.6",
    "linux6.12",
    "linux6.18",
}

# Pacchetti che, se compaiono in una transazione come "update", indicano che il
# sistema non è aggiornato: installare un'app singola in quel momento può
# lasciare il sistema in uno stato misto.
CORE_UPDATE_BLOCKERS = {
    "xbps",
    "glibc",
    "base-system",
    "hyprland",
    "hyleos-dms",
    "hyleos-desktop",
    "hyleos-store",
    "quickshell",
    "mesa",
    "wayland",
    "elogind",
    "dbus",
}

# Catalogo iniziale: app desktop leggere, installabili con XBPS.
RECOMMENDED = [
    ("Firefox", "Browser web", "firefox"),
    ("Chromium", "Browser web open source", "chromium"),
    ("LibreOffice", "Suite da ufficio completa", "libreoffice"),
    ("GIMP", "Editor di immagini", "gimp"),
    ("Inkscape", "Grafica vettoriale", "inkscape"),
    ("VLC", "Lettore multimediale", "vlc"),
    ("mpv", "Lettore video leggero", "mpv"),
    ("Audacity", "Registrazione e editing audio", "audacity"),
    ("OBS Studio", "Registrazione e streaming", "obs"),
    ("Krita", "Pittura digitale", "krita"),
    ("Blender", "Modellazione e animazione 3D", "blender"),
    ("Thunderbird", "Client email", "thunderbird"),
    ("Telegram Desktop", "Messaggistica", "telegram-desktop"),
    ("Element", "Client Matrix", "element-desktop"),
    ("Code - OSS", "Editor di codice", "code-oss"),
    ("Git", "Controllo versione", "git"),
    ("Python 3", "Interprete Python", "python3"),
    ("Neovim", "Editor da terminale", "neovim"),
    ("htop", "Monitor di sistema", "htop"),
    ("Fastfetch", "Informazioni di sistema", "fastfetch"),
    ("File manager (Thunar)", "Gestione file", "thunar"),
    ("Archive Manager", "Archivi compressi", "file-roller"),
    ("Screenshot (grim)", "Cattura schermo Wayland", "grim"),
    ("Clipboard (wl-clipboard)", "Copia/incolla Wayland", "wl-clipboard"),
]

PKG_NAME_RE = re.compile(r"^[A-Za-z0-9@._+:-]+$")


def is_protected(pkg):
    # Tutti i kernel (linux6.6, linux6.12, ...) e i loro componenti
    # (linux-firmware, linux-headers) sono vitali, anche quelli futuri.
    return pkg in PROTECTED_PACKAGES or pkg.startswith("linux")


def _run(cmd, timeout=60, env=None):
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def split_pkgver(pkgver):
    """'firefox-160.0_1' -> ('firefox', '160.0_1')."""
    m = re.match(r"^(.+)-([0-9][^-]*(?:_[0-9]+)?)$", pkgver)
    if not m:
        return pkgver, ""
    return m.group(1), m.group(2)


def installed_packages():
    """Dizionario pkgname -> versione per i pacchetti installati."""
    res = _run(["xbps-query", "-l"], timeout=20)
    if res.returncode != 0:
        return {}
    out = {}
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, version = split_pkgver(parts[1])
        out[name] = version
    return out


def search(term, limit=60):
    """Cerca pacchetti. Ritorna [(nome, descrizione, pkgname)]."""
    term = (term or "").strip()
    if not term:
        return []
    res = _run(["xbps-query", "-Rs", term], timeout=20)
    if res.returncode != 0:
        return []
    results = []
    seen = set()
    for line in res.stdout.splitlines():
        m = re.match(r"^\[[^\]]*\]\s+(\S+)\s+(.*)$", line)
        if not m:
            continue
        name, desc = split_pkgver(m.group(1))
        if name in seen or not PKG_NAME_RE.match(name):
            continue
        seen.add(name)
        results.append((name, m.group(2).strip(), name))
        if len(results) >= limit:
            break
    return results


def catalog():
    """Catalogo XBPS: consigliati prima, poi il resto degli installati."""
    installed = installed_packages()
    entries = []
    seen = set()
    for name, desc, pkg in RECOMMENDED:
        if pkg in seen:
            continue
        seen.add(pkg)
        entries.append((name, desc, pkg))
    for pkg in sorted(installed):
        if pkg in seen:
            continue
        seen.add(pkg)
        entries.append((pkg, "Pacchetto installato (XBPS)", pkg))
    return entries


def _can_sync_repos():
    """True se `xbps-install -S` può scrivere la cache dei repository.

    /var/db/xbps appartiene a root: da utente normale `-S` fallirebbe con
    "permission denied". In quel caso il controllo locale usa la cache già
    presente (`-n`) e il verdetto autorevole resta nel helper root, che gira
    elevato e rifà il preflight con `-S`.
    """
    if os.geteuid() == 0:
        return True
    return os.access("/var/db/xbps", os.W_OK)


def preflight(pkg):
    """Controlla se l'installazione singola è sicura.

    Ritorna (ok: bool, messaggio: str, richiede_aggiornamento: bool).
    """
    if not PKG_NAME_RE.match(pkg or ""):
        return False, "Nome pacchetto non valido.", False

    sync = _can_sync_repos()
    res = _run(["xbps-install", "-Sny" if sync else "-ny", pkg], timeout=60)
    output = (res.stdout or "") + (res.stderr or "")
    low = output.lower()

    if "already installed" in low:
        return True, "", False

    if res.returncode != 0:
        if not sync:
            # Senza -S la simulazione dipende dalla cache locale: un errore qui
            # non è un verdetto. Il helper root rifà il controllo aggiornato.
            return True, "", False
        # Spesso significa "database non aggiornato" o repo non raggiungibile.
        needs_update = any(
            k in low
            for k in ("out of date", "must be updated", "update xbps", "repository")
        )
        msg = output.strip() or "Controllo XBPS fallito."
        return False, msg, needs_update

    blocked = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        name, _version = split_pkgver(fields[0])
        action = fields[1]
        is_core = name in CORE_UPDATE_BLOCKERS or name.startswith("linux")
        if action in ("update", "downgrade") and is_core and name != pkg:
            blocked.append(name)

    if blocked:
        return (
            False,
            "Questa installazione richiederebbe di aggiornare componenti di "
            "base (%s). Aggiorna prima il sistema per evitare uno stato "
            "incoerente." % ", ".join(sorted(set(blocked))),
            True,
        )

    return True, "", False


def _priv_prefix():
    if os.geteuid() == 0:
        return []
    if shutil.which("pkexec"):
        return ["pkexec"]
    if shutil.which("doas"):
        return ["doas"]
    if shutil.which("sudo"):
        return ["sudo"]
    return None


def _helper():
    for path in (
        "/usr/libexec/hyleos/hyleos-store-xbps",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "hyleos-store-xbps"),
    ):
        if os.path.isfile(path):
            return path
    return None


def install(pkg):
    """Installa via helper privilegiato. Ritorna (ok, messaggio)."""
    ok, msg, needs_update = preflight(pkg)
    if not ok:
        return False, msg
    helper = _helper()
    if not helper:
        return False, "Helper di installazione non trovato (hyleos-store-xbps)."
    prefix = _priv_prefix()
    if prefix is None:
        return False, "Nessun strumento di elevazione disponibile (pkexec/doas/sudo)."
    res = _run(prefix + [helper, "install", pkg], timeout=1800)
    if res.returncode == 0:
        return True, ""
    if res.returncode == 5:
        return False, "Aggiorna prima il sistema: la transazione richiede aggiornamenti di base."
    return False, ((res.stdout or "") + (res.stderr or "")).strip()


def remove(pkg):
    """Disinstalla via helper privilegiato. Ritorna (ok, messaggio)."""
    if is_protected(pkg):
        return False, "Componente protetto: non può essere rimosso dallo Store."
    helper = _helper()
    if not helper:
        return False, "Helper di installazione non trovato (hyleos-store-xbps)."
    prefix = _priv_prefix()
    if prefix is None:
        return False, "Nessun strumento di elevazione disponibile (pkexec/doas/sudo)."
    res = _run(prefix + [helper, "remove", pkg], timeout=1800)
    if res.returncode == 0:
        return True, ""
    if res.returncode == 3:
        return False, "Componente protetto: non può essere rimosso dallo Store."
    return False, ((res.stdout or "") + (res.stderr or "")).strip()


def run_system_update():
    """Avvia l'aggiornamento completo in un terminale."""
    updater = "/usr/bin/hyleos-user-update"
    if not os.path.isfile(updater):
        return False, "hyleos-user-update non trovato."
    for term in ("kitty", "foot", "alacritty", "wezterm", "xterm"):
        if shutil.which(term):
            subprocess.Popen([term, "-e", updater])
            return True, ""
    return False, "Nessun terminale trovato per avviare l'aggiornamento."
