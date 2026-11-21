# Installer: selezione del disco di destinazione

## Sintomo

Nella pagina "Seleziona il Disco di Destinazione" compariva solo il disco
interno del PC; il disco USB/SD su cui si voleva installare non era elencato.

## Causa

`refresh_disks()` in `usr/share/hyleos/installer.py` escludeva un disco per
tre motivi, **senza mostrare nulla all'utente**:

1. il disco era (o sembrava) il supporto di avvio della live;
2. il disco aveva **almeno una partizione montata**;
3. `inspect_disk()`/`lsblk` non riusciva a leggerlo.

Il caso 2 è il più frequente: `udisks2` (o Nautilus/DMS) auto-monta le
partizioni delle chiavette appena inserite, quindi il disco di destinazione
spariva dall'elenco. Il caso 1 era troppo aggressivo: `live_detection()`
marcava come "live" **qualsiasi** disco contenente un filesystem `iso9660` /
`squashfs` / `overlay`, anche se non era quello di avvio (es. una vecchia
partizione live rimasta su una chiavetta dati).

## Cosa è stato modificato

### `installer/core/disk_manager.py`

- Nuovo `live_scan()` che restituisce tre insiemi:
  - `confirmed`: dischi **certamente** di avvio della live (montati su
    `/run/initramfs/live`, `/run/initramfs/isoscan`, `/run/archiso/bootmnt`,
    oppure contenenti il file ISO usato in loopback);
  - `suspect`: dischi che contengono solo un filesystem "simile a live";
  - `reasons`: motivazione leggibile per ogni disco.
- `live_detection()` ora restituisce **solo** i `confirmed`: si nasconde solo
  ciò che è davvero il supporto di avvio.
- Il disco che ospita la root in esecuzione (`/`) è sempre `confirmed`, così
  l'installer non può proporre di formattare il sistema da cui è lanciato
  (protezione necessaria ora che non si escludono più i dischi montati).
- `unmount_disk(disk)`: smonta tutte le partizioni montate del disco scelto
  (dalla più profonda alla più superficiale) e restituisce gli errori.
- `list_disks()` registra nel log ogni disco saltato (read-only, virtuale,
  nodo assente): niente più esclusioni mute.

### `installer.py`

- `refresh_disks()`:
  - esclude solo i dischi `confirmed`;
  - mostra i dischi con partizioni montate, con avviso
    `⚠ partizioni montate (...), verranno smontate`;
  - mostra i dischi `suspect` con avviso, invece di nasconderli;
  - nuovo pannello **"Dischi esclusi"** con il motivo dell'esclusione;
  - se non resta nessun disco, il messaggio di errore elenca i dischi esclusi
    e le cause.
- `proceed_to_confirm()`: un disco con partizioni montate non blocca più la
  conferma (viene solo annotato nel riepilogo).
- `run_install_worker()`: prima di `wipefs`/`sgdisk` chiama `unmount_disk()`;
  se una partizione resta occupata, l'installazione si ferma con un errore
  esplicito **prima** di modificare il disco.

## Comportamento atteso

| Situazione | Prima | Ora |
|---|---|---|
| USB di destinazione auto-montata | nascosta | elencata, smontata all'installazione |
| Chiavetta con vecchia partizione `iso9660` | nascosta | elencata con avviso |
| Chiavetta da cui è partita la live | nascosta | esclusa e motivata nel pannello "Dischi esclusi" |
| Scheda SD con switch write-protect | nascosta | elencata nel pannello "Dischi esclusi" con il motivo |
| Disco write-protected / illeggibile | nascosto | escluso con motivo nel log e nel pannello |

## Diagnosi rapida nella live

```bash
cat /tmp/hyleos-installer.log | grep refresh_disks
lsblk -o NAME,SIZE,RM,RO,TRAN,MOUNTPOINTS
findmnt -rn -o SOURCE,TARGET,FSTYPE | grep -E 'initramfs|media'
cat /proc/cmdline
```

Se il disco di destinazione è **lo stesso** da cui è partita la live, non può
essere formattato mentre è in uso: avviare la live da un'altra chiavetta.

## Test

`/tmp/opencode/test_live_scan.py` (fuori dal progetto) copre gli scenari:
avvio via `dd`, avvio in loopback, chiavetta con residuo `iso9660`, live non
identificabile (blocco), sistema installato (nessun live).
