#!/bin/bash
# e2e-installer.sh — avvia la ISO HyleOS in QEMU con firmware UEFI (OVMF).
#
# Serve per il test end-to-end dell'installer (punto 8): la VM parte in UEFI,
# vede un disco vuoto e permette di provare le 5 pagine dell'installer come su
# un SSD reale. Dopo l'installazione, riavvia senza la ISO e lancia
# build/verify-installed-system.sh dentro il sistema installato.
#
# Uso:
#   ./build/e2e-installer.sh                 # ISO più recente in out/
#   ./build/e2e-installer.sh /percorso.iso
#   HYLEOS_E2E_DISK_SIZE=40G ./build/e2e-installer.sh
#
# Variabili:
#   HYLEOS_VM_DIR       directory della VM (default out/vm)
#   HYLEOS_E2E_RAM      RAM in MB (default 4096)
#   HYLEOS_E2E_SMP      CPU (default 4)
#   HYLEOS_E2E_DISK     nome del disco (default hyleos-e2e.qcow2)
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
VM_DIR="${HYLEOS_VM_DIR:-$ROOT_DIR/out/vm}"
DISK="$VM_DIR/${HYLEOS_E2E_DISK:-hyleos-e2e.qcow2}"
DISK_SIZE="${HYLEOS_E2E_DISK_SIZE:-40G}"
RAM="${HYLEOS_E2E_RAM:-4096}"
SMP="${HYLEOS_E2E_SMP:-4}"

ISO="${1:-}"
if [ -z "$ISO" ]; then
    ISO="$(ls -1t "$ROOT_DIR"/out/*.iso 2>/dev/null | head -1 || true)"
fi
[ -n "$ISO" ] && [ -f "$ISO" ] || { echo "ERRORE: ISO non trovata. Passala come argomento." >&2; exit 1; }

for tool in qemu-system-x86_64 qemu-img; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "ERRORE: $tool non installato." >&2
        echo "Void: doas xbps-install -Sy qemu-system-amd64 qemu-img edk2-ovmf" >&2
        exit 1
    }
done

# Firmware OVMF: cerca i percorsi più comuni nelle distro.
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
OVMF_CODE="$(find_ovmf || true)"
OVMF_VARS_SRC="$(find_ovmf_vars || true)"
[ -n "$OVMF_CODE" ] && [ -n "$OVMF_VARS_SRC" ] || {
    echo "ERRORE: firmware OVMF non trovato (pacchetto edk2-ovmf)." >&2
    exit 1
}

mkdir -p "$VM_DIR"
OVMF_VARS="$VM_DIR/OVMF_VARS.fd"
[ -f "$OVMF_VARS" ] || cp "$OVMF_VARS_SRC" "$OVMF_VARS"

# Con KVM si passa la CPU dell'host; senza KVM (TCG) "-cpu host" non è valido.
if [ -w /dev/kvm ]; then
    ACCEL="kvm"
    CPU="host"
else
    echo "[!] /dev/kvm non disponibile: uso TCG (molto più lento)."
    ACCEL="tcg"
    CPU="max"
fi

if [ ! -f "$DISK" ]; then
    echo "[*] Creo il disco di test: $DISK ($DISK_SIZE)"
    qemu-img create -f qcow2 "$DISK" "$DISK_SIZE" >/dev/null
else
    echo "[*] Uso il disco esistente: $DISK"
    echo "    Per ripartire da zero: rm -f '$DISK'"
fi

cat <<EOF

=== Test E2E installer HyleOS (UEFI/OVMF) ===
ISO:   $ISO
Disco: $DISK
RAM:   ${RAM} MB, CPU: $SMP

Nella VM:
  1. avvia "Installa Hyle OS";
  2. verifica che il disco vuoto sia selezionabile e che il disco live/root
     sia nascosto;
  3. completa le 5 pagine e installa;
  4. al termine riavvia (la ISO resta montata: scegli il disco come boot).

Al riavvio, dal sistema installato esegui:
  ./build/verify-installed-system.sh --strict

Se la VM non parte, controlla /dev/kvm: senza KVM è lentissima ma funziona.
EOF

exec qemu-system-x86_64 \
    -name "HyleOS E2E" \
    -machine "q35,accel=$ACCEL" \
    -cpu "$CPU" \
    -smp "$SMP" \
    -m "$RAM" \
    -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
    -drive if=pflash,format=raw,file="$OVMF_VARS" \
    -drive file="$DISK",if=virtio,format=qcow2 \
    -cdrom "$ISO" \
    -boot order=d \
    -device virtio-vga \
    -device qemu-xhci \
    -device usb-tablet \
    -nic user,model=virtio-net-pci \
    -display gtk
