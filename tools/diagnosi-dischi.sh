#!/bin/sh
# HyleOS - diagnosi selezione dischi (da eseguire nella live, come utente normale).
# Non modifica nulla: legge solo lo stato dei dischi e usa il DiskManager installato.
set -u

echo "=== 1. Dischi visti dal kernel ==="
lsblk -o NAME,SIZE,TYPE,RM,RO,TRAN,MODEL,MOUNTPOINTS

echo
echo "=== 2. Mount attuali (initramfs, /run/media, ...) ==="
findmnt -rn -o SOURCE,TARGET,FSTYPE | grep -E "initramfs|media|isoscan" || echo "(nessuno)"

echo
echo "=== 3. Cmdline (come e' partita la live) ==="
cat /proc/cmdline

echo
echo "=== 4. Cosa vede il DiskManager dell'installer ==="
python3 - <<'PY'
import sys, traceback
sys.path.insert(0, "/usr/share/hyleos")
try:
    from installer.core.disk_manager import DiskManager
except Exception as exc:
    print("Impossibile importare DiskManager:", exc)
    raise SystemExit(1)

dm = DiskManager()
try:
    print("live_detection():", sorted(dm.live_detection()))
except Exception as exc:
    print("live_detection() ERRORE:", exc)

print("\nlist_disks():")
try:
    disks = dm.list_disks()
except Exception as exc:
    print("  ERRORE:", exc)
    disks = []

if not disks:
    print("  (nessun disco: controlla la sezione 1)")

for d in disks:
    path = d.get("path")
    print(f"  {path}  size={d.get('size')}  rm={d.get('rm')}  ro={d.get('ro')}  "
          f"tran={d.get('tran')}  model={d.get('model')!r}")
    try:
        print("      mounts:", dm.disk_mounts(path))
    except Exception as exc:
        print("      mounts ERRORE:", exc)
PY

echo
echo "=== 5. Log dell'installer (se l'hai gia' aperto) ==="
if [ -f /tmp/hyleos-installer.log ]; then
    grep -E "refresh_disks|list_disks|escludo" /tmp/hyleos-installer.log || echo "(nessuna riga di selezione dischi)"
else
    echo "(log assente: apri l'installer, vai alla pagina dischi, poi rilancia questo script)"
fi

echo
echo "=== Interpretazione ==="
echo "  SD elencata in list_disks ma con mounts non vuoto  -> era nascosta perche' montata (fix: ora appare)"
echo "  SD con ro=1                                        -> switch write-protect del lettore"
echo "  SD assente da list_disks e da lsblk                -> problema di lettore/collegamento"
echo "  SD assente da list_disks ma presente in lsblk      -> verifica il motivo nelle righe sopra"
