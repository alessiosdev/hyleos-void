#!/bin/bash
# canary.sh — VM canary per testare gli aggiornamenti prima di pubblicarli.
#
# Flusso (punto 12):
#   1. ./build/canary.sh init            installa l'ultima release nella VM
#   2. ./build/canary.sh snapshot        salva lo stato pulito ("clean")
#   3. ./build/canary.sh update          ripristina lo snapshot e avvia la VM
#      dentro la VM: "Aggiornamenti di sistema" (o hyleos-system-update)
#      poi riavvia e verifica login, Hyprland, DMS, pkexec, audio, Store
#   4. ./build/canary.sh mark-ok         registra che il test è passato
#   5. ./build/publish-repo.sh           pubblica solo se mark-ok è valido
#
# Uso:
#   ./build/canary.sh init [iso]
#   ./build/canary.sh snapshot | restore | boot | update | status | mark-ok
#
# Variabili:
#   HYLEOS_VM_DIR     directory VM (default out/vm)
#   HYLEOS_CANARY_DISK nome disco (default hyleos-canary.qcow2)
#   HYLEOS_REPO_DIR   repository da validare (default out/hyleos-repo/x86_64)
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
VM_DIR="${HYLEOS_VM_DIR:-$ROOT_DIR/out/vm}"
DISK="$VM_DIR/${HYLEOS_CANARY_DISK:-hyleos-canary.qcow2}"
REPO_DIR="${HYLEOS_REPO_DIR:-$ROOT_DIR/out/hyleos-repo/x86_64}"
READY_MARKER="$VM_DIR/.canary-ready"
OK_MARKER="$VM_DIR/.canary-ok"
SNAP_NAME="${HYLEOS_CANARY_SNAPSHOT:-clean}"

RAM="${HYLEOS_E2E_RAM:-4096}"
SMP="${HYLEOS_E2E_SMP:-4}"

die() { echo "ERRORE: $*" >&2; exit 1; }

need_qemu() {
    command -v qemu-system-x86_64 >/dev/null 2>&1 || die "qemu-system-x86_64 non installato"
    command -v qemu-img >/dev/null 2>&1 || die "qemu-img non installato"
}

find_ovmf() {
    for f in \
        /usr/share/edk2/x64/OVMF_CODE.4m.fd \
        /usr/share/edk2/x64/OVMF_CODE.fd \
        /usr/share/edk2-ovmf/x64/OVMF_CODE.fd \
        /usr/share/OVMF/OVMF_CODE.fd \
        /usr/share/ovmf/OVMF_CODE.fd; do
        [ -f "$f" ] && { echo "$f"; return 0; }
    done
    return 1
}
find_ovmf_vars() {
    for f in \
        /usr/share/edk2/x64/OVMF_VARS.4m.fd \
        /usr/share/edk2/x64/OVMF_VARS.fd \
        /usr/share/edk2-ovmf/x64/OVMF_VARS.fd \
        /usr/share/OVMF/OVMF_VARS.fd \
        /usr/share/ovmf/OVMF_VARS.fd; do
        [ -f "$f" ] && { echo "$f"; return 0; }
    done
    return 1
}

prepare_ovmf() {
    mkdir -p "$VM_DIR"
    OVMF_CODE="$(find_ovmf || true)"
    OVMF_VARS_SRC="$(find_ovmf_vars || true)"
    [ -n "$OVMF_CODE" ] && [ -n "$OVMF_VARS_SRC" ] || die "firmware OVMF non trovato (edk2-ovmf)"
    OVMF_VARS="$VM_DIR/OVMF_VARS.fd"
    [ -f "$OVMF_VARS" ] || cp "$OVMF_VARS_SRC" "$OVMF_VARS"
    if [ -w /dev/kvm ]; then
        ACCEL="kvm"
        CPU="host"
    else
        echo "[!] /dev/kvm non disponibile: uso TCG (molto più lento)."
        ACCEL="tcg"
        CPU="max"
    fi
}

run_qemu() {
    # $1 = "installer" per avviare dalla ISO, "disk" per avviare il disco.
    mode="$1"
    iso="${2:-}"
    prepare_ovmf
    if [ "$mode" = "installer" ]; then
        [ -n "$iso" ] && [ -f "$iso" ] || die "ISO non trovata: $iso"
        exec qemu-system-x86_64 \
            -name "HyleOS Canary (installer)" -machine "q35,accel=$ACCEL" -cpu "$CPU" \
            -smp "$SMP" -m "$RAM" \
            -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
            -drive if=pflash,format=raw,file="$OVMF_VARS" \
            -drive file="$DISK",if=virtio,format=qcow2 \
            -cdrom "$iso" -boot order=d \
            -device virtio-vga -device qemu-xhci -device usb-tablet \
            -nic user,model=virtio-net-pci -display gtk
    else
        exec qemu-system-x86_64 \
            -name "HyleOS Canary" -machine "q35,accel=$ACCEL" -cpu "$CPU" \
            -smp "$SMP" -m "$RAM" \
            -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
            -drive if=pflash,format=raw,file="$OVMF_VARS" \
            -drive file="$DISK",if=virtio,format=qcow2 \
            -boot order=c \
            -device virtio-vga -device qemu-xhci -device usb-tablet \
            -nic user,model=virtio-net-pci -display gtk
    fi
}

cmd="${1:-status}"
case "$cmd" in
    init)
        need_qemu
        iso="${2:-$(ls -1t "$ROOT_DIR"/out/*.iso 2>/dev/null | head -1 || true)}"
        [ -n "$iso" ] || die "nessuna ISO in out/: passala come argomento"
        mkdir -p "$VM_DIR"
        if [ ! -f "$DISK" ]; then
            echo "[*] Creo disco canary: $DISK (40G)"
            qemu-img create -f qcow2 "$DISK" 40G >/dev/null
        fi
        rm -f "$READY_MARKER" "$OK_MARKER"
        echo "[*] Avvio l'installer. Al termine installa e riavvia, poi esegui:"
        echo "    ./build/canary.sh snapshot"
        run_qemu installer "$iso"
        ;;
    snapshot)
        need_qemu
        [ -f "$DISK" ] || die "disco canary assente: esegui init"
        qemu-img snapshot -c "$SNAP_NAME" "$DISK"
        touch "$READY_MARKER"
        echo "[OK] Snapshot '$SNAP_NAME' creato. Ora puoi testare gli aggiornamenti:"
        echo "     ./build/canary.sh update"
        ;;
    restore)
        need_qemu
        [ -f "$DISK" ] || die "disco canary assente"
        qemu-img snapshot -a "$SNAP_NAME" "$DISK"
        rm -f "$OK_MARKER"
        echo "[OK] Snapshot '$SNAP_NAME' ripristinato."
        ;;
    boot)
        need_qemu
        [ -f "$DISK" ] || die "disco canary assente"
        run_qemu disk
        ;;
    update)
        need_qemu
        [ -f "$DISK" ] || die "disco canary assente"
        [ -f "$READY_MARKER" ] || die "nessuno snapshot: esegui prima ./build/canary.sh snapshot"
        qemu-img snapshot -a "$SNAP_NAME" "$DISK"
        rm -f "$OK_MARKER"
        cat <<EOF

=== Test canary aggiornamenti ===
Nella VM:
  1. apri DMS -> Aggiornamenti di sistema (o esegui hyleos-system-update);
  2. attendi il completamento e riavvia;
  3. verifica: login, Hyprland, DMS, pkexec (apri lo Store), audio, rete;
  4. esegui: ./build/verify-installed-system.sh --strict
Se tutto funziona, chiudi la VM e lancia:
  ./build/canary.sh mark-ok
EOF
        run_qemu disk
        ;;
    mark-ok)
        [ -f "$READY_MARKER" ] || die "nessuno snapshot: esegui prima snapshot"
        [ -f "$DISK" ] || die "disco canary assente"
        qemu-img check "$DISK" >/dev/null 2>&1 || echo "[!] qemu-img check ha segnalato problemi (non bloccante)."
        mkdir -p "$VM_DIR"
        {
            echo "data=$(date -Is)"
            echo "snapshot=$SNAP_NAME"
            echo "disco=$DISK"
            if [ -f "$REPO_DIR/x86_64-repodata" ]; then
                echo "repodata_sha256=$(sha256sum "$REPO_DIR/x86_64-repodata" | awk '{print $1}')"
            fi
        } > "$OK_MARKER"
        echo "[OK] Canary marcata come verificata: $OK_MARKER"
        echo "     Ora puoi pubblicare: ./build/publish-repo.sh"
        ;;
    status)
        echo "VM:      $DISK"
        if [ -f "$DISK" ]; then
            echo "Snapshot:"
            qemu-img snapshot -l "$DISK" 2>/dev/null || true
        else
            echo "Disco:   assente (esegui init)"
        fi
        [ -f "$READY_MARKER" ] && echo "Ready:   sì" || echo "Ready:   no"
        [ -f "$OK_MARKER" ] && { echo "Canary OK:"; sed 's/^/  /' "$OK_MARKER"; } || echo "Canary OK: no"
        ;;
    -h|--help)
        sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
        ;;
    *)
        die "comando sconosciuto: $cmd (init|snapshot|restore|boot|update|status|mark-ok)"
        ;;
esac
