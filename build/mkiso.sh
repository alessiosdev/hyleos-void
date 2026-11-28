#!/bin/bash
# Script per la generazione della ISO di Hyle OS basata su Void Linux + Hyprland + DankMaterialShell
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Controlli pre-build: overlay pulito, Python/shell/JSON/QML validi.
# Bloccano la build prima di spendere ~35 minuti. Per saltarli (sconsigliato):
#   HYLEOS_SKIP_CHECKS=1 ./build/mkiso.sh
if [ "${HYLEOS_SKIP_CHECKS:-0}" != "1" ]; then
    if ! "$SCRIPT_DIR/check-overlay.sh"; then
        echo "ERRORE: controlli pre-build falliti. Build interrotta." >&2
        exit 1
    fi
fi

# Output e directory di build sovrascrivibili: servono per costruire la ISO su
# un disco esterno quando la partizione di sistema ha poco spazio libero.
#   HYLEOS_BUILD_DIR=/mnt/usb/hyleos-build HYLEOS_OUTPUT_ISO=/mnt/usb/hyleos.iso ./build/mkiso.sh
OUTPUT_ISO="${HYLEOS_OUTPUT_ISO:-$ROOT_DIR/out/hyleos-1.0-beta-x86_64.iso}"

# Modalita' build rapida: default a 1 (initramfs zstd, squashfs zstd livello 1,
# rootfs direttamente in squashfs senza immagine ext3 intermedia).
# Per la massima compressione (piu' lenta), esporta HYLEOS_FAST=0.
FAST_BUILD="${HYLEOS_FAST:-1}"
# Livello zstd del squashfs: default 1 (build rapida, invariato). Intero
# ammesso 1..22 (limite superiore zstd). Per una release piu' compatta esporta
# HYLEOS_ZSTD_LEVEL=15. HYLEOS_FAST resta il toggle della modalita' rapida: un
# livello esplicito si applica anche con HYLEOS_FAST=0.
SQUASHFS_ZSTD_LEVEL="${HYLEOS_ZSTD_LEVEL:-1}"
case "$SQUASHFS_ZSTD_LEVEL" in
    ''|*[!0-9]*) echo "ERRORE: HYLEOS_ZSTD_LEVEL deve essere un intero 1..22." >&2; exit 1 ;;
esac
if [ "$SQUASHFS_ZSTD_LEVEL" -lt 1 ] || [ "$SQUASHFS_ZSTD_LEVEL" -gt 22 ]; then
    echo "ERRORE: HYLEOS_ZSTD_LEVEL deve essere un intero 1..22." >&2
    exit 1
fi
# Squashfs diretto (senza rootfs.img ext3). Metti HYLEOS_DIRECT_SQUASHFS=0 per
# tornare al layout classico se servisse una ISO di emergenza.
DIRECT_SQUASHFS="${HYLEOS_DIRECT_SQUASHFS:-1}"
BUILD_START=$(date +%s)

# Repository ufficiali Void
REPO_VOID_MAIN="https://repo-default.voidlinux.org/current"
REPO_VOID_NONFREE="https://repo-default.voidlinux.org/current/nonfree"

# Repository XBPS di HyleOS (punto 4): contiene hyprland/aquamarine/
# hyprland-qt-support/hyprpolkitagent ricompilati contro le librerie attuali di
# Void e i pacchetti hyleos-*. Non puntiamo più al repository esterno
# sofijacom/hyprland-void: era compilato contro libdisplay-info 0.2 e Qt 6.11.1
# e richiedeva due shim sintetici. Se il repository non è ancora stato
# costruito, la ISO usa solo i repository Void ufficiali + l'overlay.
HYLEOS_REPO_DIR="${HYLEOS_REPO_DIR:-$ROOT_DIR/out/hyleos-repo/x86_64}"

BUILD_DIR="${HYLEOS_BUILD_DIR:-/var/tmp/hyleos-build}"
MKLIVE_DIR="$BUILD_DIR/void-mklive"
TEMP_OVERLAY="$BUILD_DIR/rootfs-overlay"
WORK_DIR="$BUILD_DIR/work"
CACHE_DIR="$BUILD_DIR/xbps-cache"

echo "[*] Avvio generazione ISO Hyle OS su base Void Linux (Hyprland + DankMaterialShell)..."

# Rilevamento comando di elevazione privilegi (sudo, doas o nessuno se già root)
PRIV_CMD=""
if [ "$EUID" -ne 0 ]; then
    if command -v doas &> /dev/null; then
        PRIV_CMD="doas"
    elif command -v sudo &> /dev/null; then
        PRIV_CMD="sudo"
    else
        echo "ERRORE: Questo script deve essere eseguito come root, o avere 'doas' o 'sudo' installato."
        exit 1
    fi
fi

# Installazione delle sole dipendenze mancanti sull'host.
# Non chiedere a XBPS di reinstallare pacchetti già presenti: restituisce un
# errore e blocca lo script, pur non essendo un problema di build.
missing_packages=()
command -v mksquashfs &> /dev/null || missing_packages+=(squashfs-tools)
command -v xorriso &> /dev/null || missing_packages+=(xorriso)
command -v git &> /dev/null || missing_packages+=(git)
command -v qemu-x86_64 &> /dev/null || missing_packages+=(qemu-user-static)
# Servono per creare l'ISO e, se richiesto, per firmare il repository locale.
command -v curl &> /dev/null || missing_packages+=(curl)
command -v zstd &> /dev/null || missing_packages+=(zstd)

if [ "${#missing_packages[@]}" -gt 0 ]; then
    echo "[*] Installazione pacchetti di build necessari sull'host..."
    if command -v xbps-install &> /dev/null; then
        $PRIV_CMD xbps-install -Sy "${missing_packages[@]}"
    elif command -v dnf &> /dev/null; then
        $PRIV_CMD dnf install -y xbps qemu-user-static squashfs-tools xorriso git curl binutils xz
    elif command -v apt-get &> /dev/null; then
        $PRIV_CMD apt-get update && $PRIV_CMD apt-get install -y xbps qemu-user-static squashfs-tools xorriso git curl binutils xz
    elif command -v pacman &> /dev/null; then
        $PRIV_CMD pacman -Sy --needed --noconfirm xbps qemu-user-static squashfs-tools xorriso git curl binutils xz
    fi
fi

# Assicura che l'intera cartella di build e i suoi file siano di proprieta' dell'utente corrente
if [ -d "$BUILD_DIR" ]; then
    echo "[*] Correzione permessi della directory di build..."
    $PRIV_CMD chown -R "$(id -u):$(id -g)" "$BUILD_DIR"
fi

# Pulizia preventiva della cache pacchetti dell'host per recuperare spazio su disco (~1.2 GB)
if [ -d "/var/cache/xbps" ]; then
    echo "[*] Pulizia /var/cache/xbps per massimizzare lo spazio su disco..."
    $PRIV_CMD rm -rf /var/cache/xbps/* 2>/dev/null || true
fi

mkdir -p "$BUILD_DIR" "$CACHE_DIR"
if [ ! -d "$MKLIVE_DIR" ]; then
    echo "[*] Clonazione di void-mklive in $MKLIVE_DIR..."
    git clone https://github.com/void-linux/void-mklive.git "$MKLIVE_DIR"
fi

# Reset mklive.sh allo stato originale prima di applicare le patch (evita duplicati su run successive)
cd "$MKLIVE_DIR"
git checkout mklive.sh grub/grub_void.cfg.pre 2>/dev/null || true
cd - > /dev/null

# Patch 1: assicurarci che /tmp e /var/tmp esistano nel chroot di Void (necessario per grub-mkstandalone)
sed -i 's/xbps-uchroot "$VOIDTARGETDIR" grub-mkstandalone/mkdir -p "$VOIDTARGETDIR\/tmp" "$VOIDTARGETDIR\/var\/tmp" \&\& xbps-uchroot "$VOIDTARGETDIR" grub-mkstandalone/' "$MKLIVE_DIR/mklive.sh"

# Patch 2: riduci timeout GRUB da 15s a 3s e usa countdown invece di menu
# Cosi' l'utente non finisce accidentalmente nella shell grub> premendo un tasto
sed -i 's/^timeout=15$/timeout=3/' "$MKLIVE_DIR/grub/grub_void.cfg.pre"
sed -i 's/^timeout_style=menu$/timeout_style=countdown/' "$MKLIVE_DIR/grub/grub_void.cfg.pre"
sed -i '/^play /d' "$MKLIVE_DIR/grub/grub_void.cfg.pre"

# Patch 3: assicurati che dracut includa il modulo plymouth e i driver KMS nell'initramfs
sed -i 's/--add-drivers "ahci" --force-add "vmklive autoinstaller"/--add-drivers "ahci simpledrm i915 amdgpu nouveau" --force-add "vmklive autoinstaller" --add "plymouth"/' "$MKLIVE_DIR/mklive.sh"

# Patch 3b (solo fast): squashfs direttamente dal rootfs, senza immagine ext3.
# dmsquash-live (dracut) usa un squashfs contenente /usr come root inferiore in
# overlayfs. Evitiamo cosi' l'immagine ext3 da 2x rootfs e il doppio I/O che
# rendeva la fase di squashfs la piu' lenta della build.
# Il livello resta 1 di default; HYLEOS_ZSTD_LEVEL lo rende configurabile senza
# modificare HYLEOS_FAST. Con HYLEOS_FAST=0 e nessun livello esplicito il
# comportamento precedente resta invariato.
if [ "$FAST_BUILD" = "1" ] || [ -n "${HYLEOS_ZSTD_LEVEL+x}" ]; then
    sed -i "s|-comp \"\${SQUASHFS_COMPRESSION}\"|-comp \"\${SQUASHFS_COMPRESSION}\" -Xcompression-level $SQUASHFS_ZSTD_LEVEL|" "$MKLIVE_DIR/mklive.sh"
    if ! grep -q -- "-Xcompression-level $SQUASHFS_ZSTD_LEVEL" "$MKLIVE_DIR/mklive.sh"; then
        echo "ERRORE: patch compressione squashfs non applicata." >&2
        exit 1
    fi
fi
if [ "$FAST_BUILD" = "1" ] && [ "$DIRECT_SQUASHFS" = "1" ]; then
    sed -i '/^    # Find out required size for the rootfs/,/^    umount -f "\$BUILDDIR\/tmp-rootfs"$/d' "$MKLIVE_DIR/mklive.sh"
    sed -i '/mksquashfs/ s|"\$BUILDDIR/tmp"|"\$ROOTFS"|' "$MKLIVE_DIR/mklive.sh"
    sed -i 's|rm -rf "\$ROOTFS" "\$BUILDDIR/tmp-rootfs" "\$BUILDDIR/tmp"|rm -rf "$ROOTFS"|' "$MKLIVE_DIR/mklive.sh"
    if ! grep -q 'mksquashfs "$ROOTFS" "$IMAGEDIR/LiveOS/squashfs.img"' "$MKLIVE_DIR/mklive.sh"; then
        echo "ERRORE: patch squashfs diretto non applicata." >&2
        exit 1
    fi
fi

# Patch 3c: timestamp di ogni fase, per individuare i colli di bottiglia.
sed -i 's|info_msg "\[${CURRENT_STEP}/${STEP_COUNT}\] \$\*"|info_msg "[${CURRENT_STEP}/${STEP_COUNT}] $(date +%H:%M:%S) $*"|' "$MKLIVE_DIR/mklive.sh"

# Patch 3d: gate anti-corruzione (ADR-0012). Iniettiamo il controllo 'pre'
# immediatamente prima di mksquashfs, passando come rootfs esattamente cio' che
# viene compresso: "$ROOTFS" nel layout diretto (default), "$BUILDDIR/tmp" nel
# layout classico. Scelta deliberata: patch mirata e idempotente di mklive.sh
# invece di un controllo su $TEMP_OVERLAY + residui perche' solo in questo punto
# e' disponibile il rootfs finale dopo l'installazione dei pacchetti.
INTEGRITY_HELPER="$SCRIPT_DIR/verify-image-integrity.sh"
INTEGRITY_MANIFEST="$BUILD_DIR/hyleos-image-integrity.manifest"
if [ ! -x "$INTEGRITY_HELPER" ]; then
    echo "ERRORE: manca l'helper eseguibile $INTEGRITY_HELPER." >&2
    exit 1
fi
# L'anchor deve seguire il layout effettivamente prodotto: la patch del
# squashfs diretto (patch 3b) scatta solo con FAST_BUILD=1. Con HYLEOS_FAST=0
# mklive.sh conserva mksquashfs su "$BUILDDIR/tmp" anche se
# HYLEOS_DIRECT_SQUASHFS=1: cercare l'anchor "$ROOTFS" fallirebbe senza che la
# patch diretta sia stata applicata. Il default (FAST=1, DIRECT=1) resta invariato.
if [ "$FAST_BUILD" = "1" ] && [ "$DIRECT_SQUASHFS" = "1" ]; then
    _sq_src='$ROOTFS'
    _sq_anchor='mksquashfs "$ROOTFS" "$IMAGEDIR/LiveOS/squashfs.img"'
else
    _sq_src='$BUILDDIR/tmp'
    _sq_anchor='mksquashfs "$BUILDDIR/tmp" "$IMAGEDIR/LiveOS/squashfs.img"'
fi
_pre_line="    \"$INTEGRITY_HELPER\" pre \"$_sq_src\" \"$INTEGRITY_MANIFEST\" || exit 1"
if grep -q 'verify-image-integrity\.sh' "$MKLIVE_DIR/mklive.sh"; then
    [ "$(grep -Fc -- "$_pre_line" "$MKLIVE_DIR/mklive.sh")" -eq 1 ] || {
        echo "ERRORE: gate pre gia' presente ma non coerente con questa build in mklive.sh." >&2
        exit 1
    }
else
    _patch_tmp="$(mktemp /tmp/hyleos-mklive-integrity.XXXXXX)"
    if ! INTEGRITY_PRE_LINE="$_pre_line" INTEGRITY_ANCHOR="$_sq_anchor" awk '
        BEGIN { line = ENVIRON["INTEGRITY_PRE_LINE"]; anchor = ENVIRON["INTEGRITY_ANCHOR"] }
        !done && index($0, anchor) > 0 { print line; done = 1 }
        { print }
        END { if (!done) exit 3 }
    ' "$MKLIVE_DIR/mklive.sh" > "$_patch_tmp"; then
        rm -f "$_patch_tmp"
        echo "ERRORE: impossibile iniettare il gate pre in mklive.sh (ancora mksquashfs non trovata)." >&2
        exit 1
    fi
    cp "$_patch_tmp" "$MKLIVE_DIR/mklive.sh"
    rm -f "$_patch_tmp"
fi

# Patch 4: sostituisci lo splash generico con il nostro splash ufficiale Hyle OS
if [ -f "$ROOT_DIR/out/rootfs-overlay/boot/isolinux/splash.png" ]; then
    cp -f "$ROOT_DIR/out/rootfs-overlay/boot/isolinux/splash.png" "$MKLIVE_DIR/data/splash.png" 2>/dev/null || true
fi

# Smontiamo eventuali vecchi punti di mount rimasti aperti nelle cartelle temporanee
for mnt in $(mount | grep "$BUILD_DIR" | awk '{print $3}' | sort -r); do
    $PRIV_CMD umount -l "$mnt" 2>/dev/null || true
done

# Copiamo l'overlay in /var/tmp/hyleos-build per supportare symlink Linux
echo "[*] Preparazione overlay temporaneo..."
rm -rf "$TEMP_OVERLAY"
mkdir -p "$TEMP_OVERLAY"
cp -a "$ROOT_DIR/out/rootfs-overlay/." "$TEMP_OVERLAY/"

# Assicuriamoci che i binari siano eseguibili
chmod 755 "$TEMP_OVERLAY/usr/bin/dms" 2>/dev/null || true
chmod 755 "$TEMP_OVERLAY/usr/bin/hyleos-installer" 2>/dev/null || true
chmod 755 "$TEMP_OVERLAY/usr/bin/hyle" 2>/dev/null || true
chmod 755 "$TEMP_OVERLAY/usr/bin/hyle-welcome" 2>/dev/null || true
chmod 755 "$TEMP_OVERLAY/usr/bin/hyleos-session" 2>/dev/null || true
chmod 755 "$TEMP_OVERLAY/usr/bin/hyleos-dms-start" 2>/dev/null || true
chmod 755 "$TEMP_OVERLAY/usr/bin/hyleos-config-migrate" 2>/dev/null || true
chmod 755 "$TEMP_OVERLAY/usr/bin/hyleos-system-update" 2>/dev/null || true
chmod 755 "$TEMP_OVERLAY/usr/libexec/hyleos/kernel-cleanup" 2>/dev/null || true
chmod 755 "$TEMP_OVERLAY/usr/libexec/hyleos/hyleos-store-xbps" 2>/dev/null || true

# Repository HyleOS per i sistemi installati: viene scritto solo se l'URL è
# noto al momento della build. Senza URL la ISO resta sui repo Void ufficiali.
if [ -n "${HYLEOS_REPO_URL:-}" ]; then
    # XBPS accetta repository remoti solo se firmati. Un repo locale (path
    # assoluto) può restare non firmato; uno http(s) no: i sistemi installati
    # lo rifiuterebbero e gli aggiornamenti HyleOS non arriverebbero mai.
    _repo_sig=""
    if [ -f "$HYLEOS_REPO_DIR/x86_64-repodata" ]; then
        . "$ROOT_DIR/build/lib-repo.sh"
        _repo_sig="$(repo_signature_state "$HYLEOS_REPO_DIR")"
    fi
    case "$HYLEOS_REPO_URL" in
        http://*|https://*)
            # La verifica è possibile solo se il repository è presente in
            # locale: un URL remoto senza copia locale non è ispezionabile qui.
            if [ -f "$HYLEOS_REPO_DIR/x86_64-repodata" ] && [ "$_repo_sig" != "signed" ]; then
                echo "ERRORE: $HYLEOS_REPO_URL è remoto ma il repository non è firmato." >&2
                echo "        Stato: $_repo_sig (serve l'indice firmato e ogni pacchetto con .sig2)." >&2
                echo "        Ricostruisci con HYLEOS_SIGN=1, oppure usa un repository locale." >&2
                [ "${HYLEOS_ALLOW_UNSIGNED_REPO:-0}" = "1" ] || exit 1
            fi
            ;;
    esac
    # Con la chiave già nella ISO il primo aggiornamento non deve importarla
    # al volo: la fiducia è verificata in fase di build. Disattivabile con
    # HYLEOS_EMBED_REPO_KEY=0.
    if [ "$_repo_sig" = "signed" ] && [ "${HYLEOS_EMBED_REPO_KEY:-1}" = "1" ]; then
        if "$ROOT_DIR/build/repo-key.sh" --out "$TEMP_OVERLAY/var/db/xbps/keys" \
             "$HYLEOS_REPO_DIR" >/dev/null; then
            echo "[*] Chiave del repository inclusa nella ISO."
        else
            echo "[AVVISO] Chiave non inclusa: verrà importata al primo aggiornamento." >&2
        fi
    fi
    mkdir -p "$TEMP_OVERLAY/etc/xbps.d"
    printf 'repository=%s\n' "$HYLEOS_REPO_URL" > "$TEMP_OVERLAY/etc/xbps.d/10-hyleos-repo.conf"
    echo "[*] Repo HyleOS configurato: $HYLEOS_REPO_URL"
fi

# Runit gestisce solo servizi di sistema. La sessione grafica deve appartenere
# all'utente live creato da void-mklive.
mkdir -p "$TEMP_OVERLAY/etc/runit/runsvdir/default"
for service in dbus elogind polkitd NetworkManager bluetoothd zramen; do
    ln -sf "/etc/sv/$service" "$TEMP_OVERLAY/etc/runit/runsvdir/default/$service"
done

# Pulizia sicura delle cartelle temporanee
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
rm -rf "$MKLIVE_DIR"/mklive-build.* 2>/dev/null || true

# Repository XBPS di HyleOS (punto 4). Se è stato costruito con
# build/build-repo.sh lo usiamo al posto del vecchio repository esterno
# sofijacom/hyprland-void. Contiene Hyprland/aquamarine/hyprland-qt-support/
# hyprpolkitagent ricompilati contro le librerie attuali di Void, quindi i due
# shim sintetici (libdisplay-info0.2 e libqt6-private-api) non servono più.
HYLEOS_REPO_OPTS=()
HYLEOS_REPO_PKGS=""
if [ -e "$HYLEOS_REPO_DIR/x86_64-repodata" ]; then
    echo "[*] Uso il repository XBPS di HyleOS: $HYLEOS_REPO_DIR"
    HYLEOS_REPO_OPTS=(-r "$HYLEOS_REPO_DIR")
    # Tutti i pacchetti HyleOS vanno installati nella live: l'installer copia
    # il rootfs con rsync, quindi se i file esistessero solo nell'overlay non
    # sarebbero registrati in XBPS e gli aggiornamenti futuri non li
    # toccherebbero (conflitti "file already exists" al primo update).
    HYLEOS_REPO_PKGS="hyleos-base hyleos-theme hyleos-dms hyleos-session hyleos-installer hyleos-store hyle hyleos-desktop"
else
    echo "[!] Repository HyleOS non trovato in $HYLEOS_REPO_DIR"
    echo "    La ISO userà overlay + repository Void. Per rimuovere gli shim:"
    echo "    ./build/build-repo.sh (richiede void-packages + xbps-src)."
fi

# A10 / ADR-0026: il checkout ha le directory dell'overlay con owner 1000:1003.
# Il `cp -a` qui sopra preserva gli owner e mklive li ripropaga nel rootfs con
# `cp -rfpPv` (il flag -p conserva owner/group). Normalizziamo quindi l'intero
# overlay temporaneo a 0:0 prima di passarlo a mklive.
# La normalizzazione e' qui e non subito dopo il `cp -a` perche' i blocchi
# precedenti (chiave del repo, etc/xbps.d, runsvdir) scrivono in TEMP_OVERLAY
# come utente corrente: con owner 0:0 e mode 755 quelle scritture fallirebbero
# con EPERM e, con `set -e`, interromperebbero la build. Da qui a mklive non
# viene scritto nulla nell'overlay.
echo "[*] Normalizzazione owner/group dell'overlay temporaneo a 0:0..."
$PRIV_CMD chown -R 0:0 "$TEMP_OVERLAY"

# Verifica read-only delle directory di sistema prima del passaggio a mklive.
for _sys_dir in home etc usr etc/runit/runsvdir var/service; do
    if [ -e "$TEMP_OVERLAY/$_sys_dir" ] || [ -L "$TEMP_OVERLAY/$_sys_dir" ]; then
        _sys_owner="$(stat -c '%u:%g' "$TEMP_OVERLAY/$_sys_dir")"
        if [ "$_sys_owner" != "0:0" ]; then
            echo "ERRORE: $_sys_dir in TEMP_OVERLAY e' $_sys_owner (atteso 0:0)." >&2
            exit 1
        fi
        echo "[OK] $_sys_dir = 0:0 nell'overlay temporaneo"
    fi
done
_bad_sys_dir="$(find "$TEMP_OVERLAY" -xdev -type d \( ! -uid 0 -o ! -gid 0 \) -print -quit)"
if [ -n "$_bad_sys_dir" ]; then
    echo "ERRORE: directory dell'overlay temporaneo non 0:0: $_bad_sys_dir" >&2
    exit 1
fi
echo "[OK] tutte le directory dell'overlay temporaneo risultano 0:0"

cd "$MKLIVE_DIR"

# gptfdisk (sgdisk) e psmisc (fuser) sono richiesti dal preflight
# dell'installer: vanno inclusi esplicitamente perché non fanno parte di
# base-system e senza di loro l'installer si blocca all'avvio.
# Il set sistema (ADR-0045) e' dichiarato anche nei template hyleos-base e
# hyleos-desktop, cosi' la ISO resta completa anche senza repository HyleOS.
# ADR-0051: greetd, ReGreet, cage e cups sono in PKG_LIST per l'installazione
# offline del target; il servizio hyleos-greetd NON e' abilitato nella live,
# che resta con live.autologin e l'utente anon.
PKG_LIST="hyprland hyprland-guiutils xdg-desktop-portal-hyprland xdg-desktop-portal-gtk quickshell polkit-gnome kitty grim slurp wl-clipboard gtk+3 gtk4 libadwaita adwaita-icon-theme NetworkManager wpa_supplicant pipewire alsa-pipewire wireplumber dbus elogind seatd fuse polkit python3 python3-gobject python3-requests mesa-dri mesa-vaapi intel-media-driver vulkan-loader mesa-vulkan-intel mesa-vulkan-radeon linux-firmware linux-firmware-amd linux-firmware-intel sof-firmware intel-ucode sudo accountsservice power-profiles-daemon cups greetd ReGreet cage cups-pk-helper matugen cava plymouth plymouth-data efibootmgr dosfstools exfatprogs ntfs-3g parted gptfdisk psmisc rsync grub-x86_64-efi libdisplay-info brightnessctl dejavu-fonts-ttf noto-fonts-ttf noto-fonts-emoji font-material-design-icons-ttf nerd-fonts-symbols-ttf swaybg wlr-randr nautilus firefox gvfs gvfs-mtp gvfs-smb udisks2 upower bluez fastfetch inxi pciutils usbutils dmidecode ethtool pavucontrol xdg-user-dirs zramen libnotify xsettingsd void-repo-nonfree"
[ -n "$HYLEOS_REPO_PKGS" ] && PKG_LIST="$PKG_LIST $HYLEOS_REPO_PKGS"

MKLIVE_EXTRA_OPTS=()
BOOT_EXTRA=""
if [ "$FAST_BUILD" = "1" ]; then
    if [ "$DIRECT_SQUASHFS" = "1" ]; then
        echo "[*] Modalita' FAST attiva: initramfs zstd, squashfs diretto zstd livello $SQUASHFS_ZSTD_LEVEL."
        BOOT_EXTRA="rd.overlay=1 rd.live.overlay.overlayfs=1"
    else
        echo "[*] Modalita' FAST attiva con rootfs.img ext3: initramfs zstd, squashfs zstd livello $SQUASHFS_ZSTD_LEVEL."
    fi
    PKG_LIST="$PKG_LIST zstd"
    MKLIVE_EXTRA_OPTS=(-i zstd)
fi

# --- Controlli di sanita' della configurazione della sessione -----------------
# Hyprland 0.57 ha rimosso hyprland.conf: se per errore restasse nell'overlay,
# la sessione partirebbe senza keybind e senza autostart (shell DMS assente).
if [ -f "$ROOT_DIR/out/rootfs-overlay/etc/skel/.config/hypr/hyprland.conf" ]; then
    echo "ERRORE: hyprland.conf presente nell'overlay ma non e' piu' supportato da Hyprland >= 0.57." >&2
    echo "        Rimuovilo: rm out/rootfs-overlay/etc/skel/.config/hypr/hyprland.conf" >&2
    exit 1
fi
if [ ! -f "$ROOT_DIR/out/rootfs-overlay/etc/skel/.config/hypr/hyprland.lua" ]; then
    echo "ERRORE: manca out/rootfs-overlay/etc/skel/.config/hypr/hyprland.lua." >&2
    exit 1
fi
if [ ! -x "$ROOT_DIR/out/rootfs-overlay/usr/bin/hyleos-dms-start" ]; then
    echo "ERRORE: manca o non e' eseguibile usr/bin/hyleos-dms-start." >&2
    exit 1
fi
case " $PKG_LIST " in
    *" quickshell "*) ;;
    *)
        echo "ERRORE: quickshell non e' in PKG_LIST: la shell DMS non avrebbe la UI." >&2
        exit 1
        ;;
esac

# Verifica che la configurazione Lua sia valida con il binario Hyprland che
# verra' installato nell'ISO (se disponibile nell'host).
if command -v Hyprland >/dev/null 2>&1 && command -v hyprctl >/dev/null 2>&1; then
    HYPR_VERIFY_HOME="$(mktemp -d)"
    mkdir -p "$HYPR_VERIFY_HOME/.config/hypr"
    cp "$ROOT_DIR/out/rootfs-overlay/etc/skel/.config/hypr/hyprland.lua" \
        "$HYPR_VERIFY_HOME/.config/hypr/hyprland.lua"
    if HOME="$HYPR_VERIFY_HOME" XDG_CONFIG_HOME="$HYPR_VERIFY_HOME/.config" \
        Hyprland --verify-config >/dev/null 2>&1; then
        echo "[OK] hyprland.lua verificato con Hyprland --verify-config"
    else
        echo "[!] Attenzione: 'Hyprland --verify-config' non e' disponibile o ha segnalato errori." >&2
        echo "    Verifica manualmente hyprland.lua prima di distribuire la ISO." >&2
    fi
    rm -rf "$HYPR_VERIFY_HOME"
fi
# ------------------------------------------------------------------------------

# Libera subito lo spazio della ISO precedente: su questo host la directory di
# build e l'output condividono gli stessi ~7 GB liberi.
mkdir -p "$(dirname "$OUTPUT_ISO")"
if [ -f "$OUTPUT_ISO" ]; then
    echo "[*] Rimozione della ISO precedente per liberare spazio..."
    rm -f "$OUTPUT_ISO"
fi

# Generazione della ISO via mklive.sh
$PRIV_CMD ./mklive.sh \
  -a x86_64 \
  -o "$OUTPUT_ISO" \
  -c "$CACHE_DIR" \
  -s zstd \
  "${MKLIVE_EXTRA_OPTS[@]}" \
  -r "$REPO_VOID_MAIN" \
  -r "$REPO_VOID_NONFREE" \
  "${HYLEOS_REPO_OPTS[@]}" \
  -p "$PKG_LIST" \
  -I "$TEMP_OVERLAY" \
  -C "live.autologin live.shell=/bin/bash quiet splash loglevel=3 vt.global_cursor_default=0 udev.log_level=3 $BOOT_EXTRA" \
  -T "Hyle OS 1.0 Beta (Hyprland + DMS Edition)"

[ -s "$OUTPUT_ISO" ] || {
    echo "ERRORE: ISO generata assente o vuota: $OUTPUT_ISO" >&2
    exit 1
}
ISO_SHA256="$(sha256sum "$OUTPUT_ISO" | awk '{print $1}')"
[ -n "$ISO_SHA256" ] || {
    echo "ERRORE: impossibile calcolare lo sha256 della ISO generata." >&2
    exit 1
}
HYLEOS_EXPECTED_ISO="$OUTPUT_ISO" HYLEOS_EXPECTED_ISO_SHA256="$ISO_SHA256" \
    HYLEOS_XBPS_CACHE_DIRS="$CACHE_DIR${HYLEOS_REPO_DIR:+:$HYLEOS_REPO_DIR}" \
    "$INTEGRITY_HELPER" post "$OUTPUT_ISO" "$INTEGRITY_MANIFEST" || {
    echo "ERRORE: gate di integrita' immagine fallito: ISO non distribuibile." >&2
    exit 1
}
echo "[OK] gate integrita' immagine superato (manifest $INTEGRITY_MANIFEST)"

echo "[OK] ISO generata con successo in $OUTPUT_ISO"
echo "[OK] Tempo totale: $(( $(date +%s) - BUILD_START ))s"
