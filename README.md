# HyleOS

Distribuzione desktop basata su **Void Linux** con **Hyprland** (configurazione in
Lua) e **DankMaterialShell** (DMS) come shell grafica. Include un installer
grafico UEFI, uno store applicazioni basato su XBPS e AppImage e un sistema di
aggiornamenti firmati con repository proprio.

> **Regola di fondo:** `out/rootfs-overlay/` è la fonte canonica. La ISO live e i
> pacchetti installati nascono dagli stessi file: nessuna copia manuale.
> `build/sync-packages.sh` genera il payload dei pacchetti dall'overlay.

## Caratteristiche

- **Void Linux** (x86_64, glibc) con kernel gestito e pulizia automatica delle
  versioni vecchie (mantiene le ultime 2).
- **Hyprland** con configurazione in Lua (`~/.config/hypr/hyprland.lua`), nessun
  `.conf` legacy.
- **DankMaterialShell 1.6.2** inclusa nell'overlay, con dipendenza
  `quickshell=0.3.2_1` bloccata: un aggiornamento upstream non può rompere il
  desktop a metà.
- **Usabilità mouse-first**: controlli finestra nella barra, modalità
  *Tiling* / *Finestre libere*, menu contestuale sul desktop, panoramica
  finestre, dock. Tutte le scorciatoie da tastiera restano attive.
- **Installer grafico** (Python + GTK4/libadwaita): GPT, ESP FAT32, root ext4,
  copia di `/etc/skel` e configurazione della sessione.
- **Hyle Store**: installa da XBPS e da AppImage; il catalogo
  `appimage-catalog.json` richiede un `sha256` valido (senza hash non si
  installa) e per montare le AppImage serve FUSE2. Lo Store fa solo
  Esplora/Cerca/Installa/Disinstalla/Apri: non mostra aggiornamenti, che restano
  in DMS → Impostazioni → Aggiornamenti di sistema; preflight di rete e permessi.
- **Aggiornamenti firmati**: repository XBPS proprio con pacchetti `.sig2` e
  indice firmato. `/usr/bin/hyleos-user-update` aggiorna prima le AppImage come
  utente normale e poi esegue l'aggiornamento XBPS di sistema con
  `hyleos-system-update`, che usa `pkexec` da DMS e `doas`/`sudo` da terminale.

## Struttura del repository

| Percorso | Contenuto |
| --- | --- |
| `out/rootfs-overlay/` | **Fonte canonica** di tutto ciò che finisce nel sistema: `/etc/skel`, `/etc/xdg`, script, installer, store, tema, asset. |
| `packages/` | Template XBPS (`xbps-src`) dei pacchetti `hyleos-*`. |
| `build/` | Script di controllo, sincronizzazione, build repo, ISO, canary, pubblicazione. |
| `tests/` | Test Python (config, kernel, packaging, firma repo, window mode, store). |
| `docs/` | Documentazione tecnica (vedi in fondo). |
| `tools/` | Utility di diagnostica (`diagnosi-dischi.sh`). |
| `kernel/` | Riservato a materiale kernel locale. |
| `test-qemu.sh` | Avvio rapido della ISO in QEMU. |

`packages/*/files/` è **generato**: non va modificato a mano, viene ricreato da
`./build/sync-packages.sh` (e da `build-repo.sh`). Per questo è escluso da Git:
dopo un clone pulito va rigenerato prima di costruire.

Il binario `out/rootfs-overlay/usr/bin/dms` (~73 MB, Go statico) è invece
versionato di proposito, perché il pacchetto `hyleos-dms` lo installa; resta
sotto il limite GitHub di 100 MB per file.

## Requisiti di build

Su una macchina Void Linux:

- `xbps-src` inizializzato in `~/void-packages` (o `VOID_PACKAGES=/percorso`):
  ```sh
  git clone --depth=1 https://github.com/void-linux/void-packages ~/void-packages
  ~/void-packages/xbps-src binary-bootstrap
  ```
- i **template di Hyprland**: Void non pacchettizza Hyprland, quindi servono
  da un fork (es. `sofijacom/hyprland-void`, allineato a Hyprland 0.57.x):
  ```sh
  git clone --depth=1 https://github.com/sofijacom/hyprland-void /tmp/hypr-templates
  ```
  `build-repo.sh` copia in `void-packages` tutti i template mancanti, comprese
  le dipendenze (`hyprlang`, `hyprwire`, `hyprgraphics`, `lua55`, `glaze`, …).
- `git`, `python3`, `xz`, `squashfs-tools`, `qemu` (solo per i test in VM).
- Almeno **~15 GB liberi** per repo + ISO (usare `HYLEOS_BUILD_DIR` su disco
  esterno se serve).
- appartenenza al gruppo `xbuilder` (su Void è così per l'utente principale):
  serve a `xbps-src`, che gira come utente normale e usa `xbps-uchroot`
  (setgid). **`build-repo.sh` non va lanciato con `doas`/`sudo`**: `xbps-src`
  rifiuta root.
- `doas`/`sudo` solo per la ISO (`mkiso.sh` monta i chroot di mklive).
- `qmllint` fortemente consigliato (`qt6-declarative-tools`).

## Build passo passo

```sh
# 0. Controlli statici (pochi secondi, evita ~35 minuti di build rotta)
./build/check-overlay.sh

# 1. Genera il payload dei pacchetti dall'overlay
#    (obbligatorio dopo un clone pulito: packages/*/files non è versionato)
./build/sync-packages.sh

# 2. Costruisce il repository XBPS (Hyprland + pacchetti hyleos-*)
VOID_PACKAGES=~/void-packages \
HYPR_SRCPKGS_DIR=/tmp/hypr-templates \
./build/build-repo.sh

# 3. Costruisce la ISO
./build/mkiso.sh
#    output: out/hyleos-1.0-beta-x86_64.iso

# 4. Prova rapida in QEMU
./test-qemu.sh

# 5. Test end-to-end dell'installer (UEFI/OVMF, disco virtuale)
./build/e2e-installer.sh
```

Dopo un'installazione reale, verifica il sistema e poi pubblica:

```sh
./build/verify-installed-system.sh --strict
./build/canary.sh mark-ok
./build/publish-repo.sh
```

## Variabili d'ambiente principali

| Variabile | Default | Uso |
| --- | --- | --- |
| `VOID_PACKAGES` | `~/void-packages` | Albero `xbps-src` da usare. |
| `HYPR_SRCPKGS_DIR` | — | Albero con i template Hyprland mancanti (copiati in `void-packages`). |
| `HYLEOS_REPO_DIR` | `out/hyleos-repo/x86_64` | Destinazione del repository XBPS. |
| `HYLEOS_REPO_URL` | — | Repo remoto da configurare nella ISO. |
| `HYLEOS_SIGN=1` | `0` | Firma pacchetti e indice del repository. |
| `HYLEOS_PRIVKEY` | `~/.ssh/id_rsa` | Chiave PEM per la firma. |
| `HYLEOS_SIGNEDBY` | `Hyle OS Team <team@hyleos.org>` | Identità della firma. |
| `HYLEOS_BUILD_DIR` | `/var/tmp/hyleos-build` | Directory temporanea di build. |
| `HYLEOS_OUTPUT_ISO` | `out/hyleos-1.0-beta-x86_64.iso` | Percorso della ISO. |
| `HYLEOS_FAST=0` | `1` | Compressione massima (build più lenta). |
| `HYLEOS_SKIP_CHECKS=1` | — | Salta i controlli pre-build (sconsigliato). |
| `HYLEOS_VM_DIR` | `out/vm` | Dischi delle VM canary/E2E. |

## Sul sistema installato

| Comando | A cosa serve |
| --- | --- |
| `hyle update` | Aggiornamento completo (AppImage come utente, poi XBPS + sistema). |
| `hyleos-user-update` | Aggiorna le AppImage come utente normale, poi avvia l'aggiornamento XBPS di sistema. |
| `hyleos-system-update` | Aggiornamento XBPS con preflight, log e avviso di riavvio. |
| `hyle clean` | Pulizia cache/orfani XBPS e kernel vecchi (mantiene gli ultimi 2). |
| `hyle doctor` | Diagnostica e auto-riparazione. |
| `hyle repair` | Riparazione dei componenti di sessione. |
| `hyle search`, `hyle info` | Ricerca e dettagli pacchetti XBPS. |
| `kernel-cleanup --list` | Kernel presenti e protetti. |
| `hyleos-window-mode get\|set\|toggle\|apply` | Modalità finestre. |

## Scorciatoie principali

| Tastiera | Azione |
| --- | --- |
| `SUPER + Invio` | Terminale (kitty) |
| `SUPER + B` | Firefox |
| `SUPER + E` | File manager |
| `SUPER + A` | Hyle Store |
| `SUPER + Spazio` | Ricerca applicazioni |
| `SUPER + C` | Centro di controllo |
| `SUPER + Q` / `SUPER + V` / `SUPER + F` | Chiudi / flottante / schermo intero |
| `SUPER + 1…9` | Workspace |
| `SUPER + frecce` | Focus finestra |
| `Stampa`, `Shift + Stampa` | Screenshot |

Mouse:

- trascina la barra del titolo per spostare una finestra libera;
- `SUPER + clic sinistro` sposta, `SUPER + clic destro` ridimensiona;
- trascina i bordi per ridimensionare;
- clic destro sul desktop: menu rapido (file manager, terminale, store, sfondo,
  impostazioni, sessione);
- controlli finestra e pulsante panoramica nella barra;
- rotella sui workspace per cambiarli.

## Configurazione persistente

- Default di sistema in `/etc/skel` e `/etc/xdg`: gli aggiornamenti non toccano
  mai `~/.config`.
- `hyleos-config-migrate` gira a ogni login, crea solo i file mancanti e fa un
  merge "add-missing" dei JSON: le scelte dell'utente non vengono sovrascritte.
- Modalità finestre in `~/.config/hyleos/window-mode.json`:
  ```json
  { "version": 1, "mode": "tiling" }
  ```
  Valori: `tiling` (default) o `floating`.

## Test

```sh
for t in tests/test_*.py; do python3 "$t"; done
```

Coprono migrazione configurazione, pulizia kernel, rilevamento disco live,
coerenza packaging, firma repository, modalità finestre, backend XBPS e AppImage
dello store e flusso di aggiornamento utente/sistema.

## Documentazione

- [`docs/AGGIORNAMENTI.md`](docs/AGGIORNAMENTI.md) — aggiornamenti, repository,
  canary, release, usabilità mouse-first.
- [`docs/hyprland-057-dms.md`](docs/hyprland-057-dms.md) — integrazione Hyprland
  0.57 + DMS.
- [`docs/installer-disk-selection.md`](docs/installer-disk-selection.md) —
  selezione disco dell'installer.

## Stato del progetto

Beta. Le build `build-repo.sh`, `e2e-installer.sh`, `canary.sh` e
`verify-installed-system.sh` richiedono una macchina Void completa con
`xbps-src`, QEMU/OVMF e spazio su disco; non girano in un ambiente minimale.
