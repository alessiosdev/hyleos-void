#!/bin/bash
# Script per avviare la ISO di Hyle OS Void in QEMU per verifica
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO_PATH="$SCRIPT_DIR/out/hyleos-1.0-beta-x86_64.iso"

if [ ! -f "$ISO_PATH" ]; then
    echo "[!] File ISO non trovato in $ISO_PATH. Esegui prima build/mkiso.sh"
    exit 1
fi

echo "[*] Avvio QEMU per Hyle OS (Void Base)..."
qemu-system-x86_64 \
  -enable-kvm \
  -m 2048 \
  -smp 2 \
  -cpu host \
  -vga virtio \
  -display gtk,gl=on \
  -cdrom "$ISO_PATH" \
  -boot d
