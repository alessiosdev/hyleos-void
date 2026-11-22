#!/bin/bash
# sync-packages.sh — genera il payload dei pacchetti XBPS dall'overlay.
#
# L'overlay resta la fonte canonica (così la ISO live e i pacchetti installati
# hanno sempre lo stesso contenuto). Questo script copia i file nelle
# directory files/ dei template, che xbps-src userà per costruire i pacchetti.
#
# Uso: ./build/sync-packages.sh
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
OVERLAY="$ROOT_DIR/out/rootfs-overlay"
PKG_DIR="$ROOT_DIR/packages"

[ -d "$OVERLAY" ] || { echo "ERRORE: overlay non trovato: $OVERLAY" >&2; exit 1; }

copy() {
    # copy <src-relativa-all'overlay> <dest-relativa-a-files/>
    src="$OVERLAY/$1"
    dst="$2"
    if [ ! -e "$src" ]; then
        echo "ERRORE: manca $1 nell'overlay" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
}

reset_files() {
    rm -rf "$PKG_DIR/$1/files"
    mkdir -p "$PKG_DIR/$1/files"
}

echo "[*] Sincronizzazione payload pacchetti dall'overlay..."

# --- hyleos-session ---------------------------------------------------------
reset_files hyleos-session
F="$PKG_DIR/hyleos-session/files"
mkdir -p "$F/bin" "$F/libexec/hyleos" "$F/profile.d" "$F/polkit"
copy usr/bin/hyleos-session        "$F/bin/hyleos-session"
copy usr/bin/hyleos-config-migrate "$F/bin/hyleos-config-migrate"
copy usr/bin/hyleos-dms-start      "$F/bin/hyleos-dms-start"
copy usr/bin/hyleos-system-update        "$F/bin/hyleos-system-update"
copy usr/bin/hyleos-window-mode          "$F/bin/hyleos-window-mode"
copy usr/libexec/hyleos/kernel-cleanup   "$F/libexec/hyleos/kernel-cleanup"
copy etc/profile.d/hyleos-session.sh     "$F/profile.d/hyleos-session.sh"
copy usr/share/polkit-1/actions/org.hyleos.update.system.policy \
     "$F/polkit/org.hyleos.update.system.policy"

# --- hyleos-installer -------------------------------------------------------
reset_files hyleos-installer
F="$PKG_DIR/hyleos-installer/files"
mkdir -p "$F/bin" "$F/share/hyleos" "$F/applications" "$F/polkit"
copy usr/bin/hyleos-installer "$F/bin/hyleos-installer"
copy usr/share/hyleos/installer.py "$F/share/hyleos/installer.py"
copy usr/share/hyleos/installer "$F/share/hyleos/installer"
copy usr/share/applications/org.hyleos.installer.desktop "$F/applications/org.hyleos.installer.desktop"
copy usr/share/polkit-1/actions/org.hyleos.installer.policy \
     "$F/polkit/org.hyleos.installer.policy"
copy usr/share/polkit-1/rules.d/50-hyleos-installer-live.rules \
     "$F/polkit/50-hyleos-installer-live.rules"

# --- hyleos-dms -------------------------------------------------------------
reset_files hyleos-dms
F="$PKG_DIR/hyleos-dms/files"
mkdir -p "$F/bin" "$F/quickshell"
copy usr/bin/dms "$F/bin/dms"
copy etc/xdg/quickshell/hyleos-dms "$F/quickshell/hyleos-dms"

# --- hyleos-store -----------------------------------------------------------
reset_files hyleos-store
F="$PKG_DIR/hyleos-store/files"
mkdir -p "$F/bin" "$F/share/hyleos" "$F/libexec/hyleos" "$F/polkit" "$F/applications"
copy usr/bin/hyleos-appimage-update "$F/bin/hyleos-appimage-update"
copy usr/bin/hyleos-user-update "$F/bin/hyleos-user-update"
copy usr/share/hyleos/appstore.py "$F/share/hyleos/appstore.py"
copy usr/share/hyleos/xbps_backend.py "$F/share/hyleos/xbps_backend.py"
copy usr/share/hyleos/appimage_backend.py "$F/share/hyleos/appimage_backend.py"
copy usr/share/hyleos/appimage-catalog.json "$F/share/hyleos/appimage-catalog.json"
copy usr/share/hyleos/appimage-catalog.example.json "$F/share/hyleos/appimage-catalog.example.json"
copy usr/libexec/hyleos/hyleos-store-xbps "$F/libexec/hyleos/hyleos-store-xbps"
copy usr/share/polkit-1/actions/org.hyleos.store.xbps.policy "$F/polkit/org.hyleos.store.xbps.policy"
copy usr/share/applications/hyleos-store.desktop "$F/applications/hyleos-store.desktop"
copy usr/share/applications/org.hyleos.store.desktop "$F/applications/org.hyleos.store.desktop"

# --- hyle -------------------------------------------------------------------
reset_files hyle
F="$PKG_DIR/hyle/files"
mkdir -p "$F/bin" "$F/share/hyleos" "$F/applications"
copy usr/bin/hyle "$F/bin/hyle"
copy usr/bin/hyle-welcome "$F/bin/hyle-welcome"
copy usr/share/hyleos/welcome.py "$F/share/hyleos/welcome.py"
copy usr/share/applications/org.hyleos.welcome.desktop "$F/applications/org.hyleos.welcome.desktop"

# --- hyleos-theme -----------------------------------------------------------
reset_files hyleos-theme
F="$PKG_DIR/hyleos-theme/files"
mkdir -p "$F/hyleos" "$F/backgrounds"
copy usr/share/hyleos/assets "$F/hyleos/assets"
copy usr/share/hyleos/themes "$F/hyleos/themes"
copy usr/share/backgrounds/hyleos "$F/backgrounds/hyleos"

# --- hyleos-desktop (default di sistema, mai in ~/.config) ------------------
reset_files hyleos-desktop
F="$PKG_DIR/hyleos-desktop/files"
mkdir -p "$F/skel" "$F/xdg" "$F/profile.d" "$F/gtk-3.0" "$F/gtk-4.0"
copy etc/skel/. "$F/skel/"
copy etc/xdg/xsettingsd "$F/xdg/xsettingsd"
copy etc/profile.d/xdg-user-dirs.sh "$F/profile.d/xdg-user-dirs.sh"
copy etc/gtk-3.0/settings.ini "$F/gtk-3.0/settings.ini"
copy etc/gtk-4.0/settings.ini "$F/gtk-4.0/settings.ini"

# Niente cache Python o backup nei pacchetti.
find "$PKG_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PKG_DIR" \( -name '*.pyc' -o -name '*.bak*' -o -name '*.orig' \) -delete

echo "[OK] Payload pacchetti sincronizzato."
du -sh "$PKG_DIR"/hyleos-*/files "$PKG_DIR"/hyle/files 2>/dev/null || true
