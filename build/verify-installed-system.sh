#!/bin/bash
# verify-installed-system.sh — verifica un'installazione HyleOS su disco.
#
# Va eseguito DENTRO il sistema installato (non nella live), dopo il primo
# riavvio. Controlla i punti critici del post-install:
#   * /etc/fstab con UUID reali e root montata correttamente;
#   * bootloader UEFI e voce EFI registrata;
#   * initramfs rigenerato senza il modulo live (vmklive);
#   * /etc/machine-id valido;
#   * utente anon/autologin rimossi;
#   * servizi runit attivi.
#
# Uso: ./verify-installed-system.sh [--strict]
# Exit: 0 tutto ok, 1 almeno un errore.
set -u

STRICT=0
[ "${1:-}" = "--strict" ] && STRICT=1

errors=0
warnings=0
err()  { printf '  [ERRORE] %s\n' "$*" >&2; errors=$((errors + 1)); }
warn() { printf '  [AVVISO] %s\n' "$*" >&2; warnings=$((warnings + 1)); }
ok()   { printf '  [OK] %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }

section "Identità del sistema"
if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" = "hyleos" ]; then
        ok "os-release: ${PRETTY_NAME:-HyleOS}"
    else
        err "os-release non è HyleOS (ID=${ID:-assente})"
    fi
else
    err "/etc/os-release mancante"
fi

if [ -d /sys/firmware/efi ]; then
    ok "avvio UEFI"
else
    err "sistema avviato in modalità BIOS: HyleOS supporta solo UEFI x86_64"
fi

section "/etc/fstab"
if [ ! -s /etc/fstab ]; then
    err "/etc/fstab mancante o vuoto"
else
    ok "/etc/fstab presente"
    if grep -qE '^[^#].*\b(live|overlay|squashfs)\b' /etc/fstab; then
        err "/etc/fstab contiene ancora voci della live"
    fi
    while read -r uuid; do
        [ -n "$uuid" ] || continue
        if command -v blkid >/dev/null 2>&1 && ! blkid -U "$uuid" >/dev/null 2>&1; then
            err "UUID $uuid in fstab non esiste su nessun dispositivo"
        fi
    done < <(sed -n 's/.*UUID=\([^ \t]*\).*/\1/p' /etc/fstab | sort -u)
    root_src="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
    if [ -n "$root_src" ]; then
        ok "root montata da $root_src"
    else
        warn "impossibile determinare la root con findmnt"
    fi
fi

section "Bootloader UEFI"
if command -v efibootmgr >/dev/null 2>&1; then
    if efibootmgr -v 2>/dev/null | grep -qiE 'hyle|void|grub'; then
        ok "voce EFI registrata"
    else
        err "nessuna voce EFI HyleOS/Void/GRUB trovata"
    fi
else
    warn "efibootmgr non installato: impossibile verificare la voce EFI"
fi
if findmnt -n /boot/efi >/dev/null 2>&1 || findmnt -n /efi >/dev/null 2>&1; then
    ok "partizione EFI montata"
else
    err "partizione EFI non montata (/boot/efi o /efi)"
fi

section "initramfs"
kver="$(uname -r)"
if [ -f "/boot/initramfs-$kver.img" ] || [ -f "/boot/initramfs-$kver" ]; then
    ok "initramfs per il kernel in uso presente"
else
    err "initramfs per $kver assente"
fi
if command -v lsinitrd >/dev/null 2>&1; then
    if lsinitrd "/boot/initramfs-$kver.img" 2>/dev/null | grep -q 'vmklive'; then
        err "l'initramfs contiene ancora il modulo live vmklive"
    else
        ok "initramfs senza modulo vmklive"
    fi
else
    warn "lsinitrd non disponibile: controllo vmklive saltato"
fi

section "Identità macchina e utenti"
mid="$(tr -d '[:space:]' < /etc/machine-id 2>/dev/null || true)"
if [ "${#mid}" -eq 32 ]; then
    ok "/etc/machine-id valido"
else
    err "/etc/machine-id assente o non valido (lunghezza ${#mid})"
fi
if id anon >/dev/null 2>&1; then
    err "l'utente anon della live esiste ancora"
else
    ok "utente anon assente"
fi
if grep -rqs -- '--autologin' /etc/sv/agetty-tty1 /etc/inittab 2>/dev/null; then
    err "autologin agetty ancora configurato"
else
    ok "nessun autologin agetty"
fi

section "Servizi runit"
for svc in dbus elogind NetworkManager zramen; do
    if [ -d "/etc/sv/$svc" ]; then
        if [ -L "/var/service/$svc" ] || [ -L "/etc/runit/runsvdir/default/$svc" ]; then
            ok "$svc abilitato"
        else
            err "$svc non abilitato"
        fi
    else
        warn "servizio $svc non presente"
    fi
done

section "Aggiornamenti"
if [ -f /var/lib/hyleos/reboot-required ]; then
    warn "flag di riavvio presente: riavvia e ripeti la verifica"
fi
if command -v hyleos-system-update >/dev/null 2>&1; then
    ok "hyleos-system-update presente"
else
    err "hyleos-system-update mancante"
fi
if command -v kernel-cleanup >/dev/null 2>&1 || [ -x /usr/libexec/hyleos/kernel-cleanup ]; then
    ok "kernel-cleanup presente"
else
    err "kernel-cleanup mancante"
fi

printf '\n'
if [ "$errors" -gt 0 ]; then
    printf 'VERIFICA FALLITA: %d errori, %d avvisi.\n' "$errors" "$warnings" >&2
    exit 1
fi
if [ "$STRICT" -eq 1 ] && [ "$warnings" -gt 0 ]; then
    printf 'VERIFICA FALLITA (strict): %d avvisi.\n' "$warnings" >&2
    exit 1
fi
printf 'VERIFICA SUPERATA: 0 errori, %d avvisi.\n' "$warnings"
