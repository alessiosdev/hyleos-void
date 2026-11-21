# HyleOS: aggiornamenti sicuri nel tempo

Questo documento descrive come HyleOS viene costruita, aggiornata e pubblicata
per poter restare manutenibile per anni. Corrisponde ai punti **2–12** della
checklist interna (il punto 1, Git, è escluso per scelta).

Regola di fondo: **l'overlay è la fonte canonica**. La ISO live e i pacchetti
installati nascono dagli stessi file in `out/rootfs-overlay`. Nessuna copia
manuale: `build/sync-packages.sh` genera il payload dei pacchetti dall'overlay.

---

## 1. Mappa dei comandi

| Comando | A cosa serve |
| --- | --- |
| `./build/check-overlay.sh` | Valida l'overlay prima di costruire (file sporchi, sintassi, JSON, QML). |
| `./build/sync-packages.sh` | Copia l'overlay nei payload dei pacchetti XBPS. |
| `./build/build-repo.sh` | Costruisce il repository XBPS HyleOS (stack Hyprland + pacchetti `hyleos-*`). |
| `./build/mkiso.sh` | Costruisce la ISO usando il repository HyleOS, se presente. |
| `./build/e2e-installer.sh` | Avvia la ISO in QEMU/OVMF per il test end-to-end dell'installer. |
| `./build/verify-installed-system.sh` | Verifica un sistema installato dopo il primo riavvio. |
| `./build/canary.sh` | Gestisce la VM canary (snapshot, update, mark-ok). |
| `./build/publish-repo.sh` | Pubblica il repository solo dopo il test canary. |

Sul sistema installato:

| Comando | A cosa serve |
| --- | --- |
| `hyle update` | Aggiornamento completo (delega a `/usr/bin/hyleos-user-update`). |
| `hyleos-user-update` | Aggiorna le AppImage come utente normale, poi passa a `hyleos-system-update` per XBPS. |
| `hyleos-system-update` | Aggiornamento XBPS con preflight, log e avviso di riavvio. |
| `hyle clean` | Pulizia cache/orfani XBPS + kernel vecchi (mantiene gli ultimi 2). |
| `kernel-cleanup --list` | Mostra i kernel presenti e quelli protetti. |

---

## 2. Pulizia e controllo prima della build (punto 2)

`build/check-overlay.sh` blocca la build se nell'overlay compaiono:

- `*.bak`, `*.bak.*`, `*.orig`, `*.newbackup`, `*.rej`;
- directory `__pycache__`, `*.pyc`;
- directory morte o scaffolding (`installer/ui`, `installer/utils`,
  `installer/tests`, `hyle-installer`, ...).

Controlla inoltre:

- presenza dei file obbligatori (script di sessione, installer, Store, DMS,
  policy polkit);
- permessi eseguibili sugli script che devono esserlo;
- `python3 -m py_compile` su tutti i `.py`;
- `sh -n` su tutti gli script shell;
- validità di tutti i `.json` (tranne i template `matugen/*`, che sono JSONC
  con placeholder);
- `qmllint` sui QML modificati elencati in `build/qml-lint-targets.txt`.

`mkiso.sh` chiama `check-overlay.sh` e si ferma se ci sono errori. Se `qmllint`
non è installato, la build prosegue con un avviso: il controllo QML è
fortemente raccomandato sulla macchina di build.

---

## 3. Configurazione: default di sistema e migrazione (punto 3)

I default vivono **solo** in:

- `/etc/skel` — scheletro della home per i nuovi utenti;
- `/etc/xdg` — configurazione condivisa (DMS, xsettingsd, ...);
- `/etc/gtk-3.0`, `/etc/gtk-4.0` — tema GTK.

Nessun aggiornamento scrive in `~/.config`. La migrazione è idempotente e la
esegue `hyleos-session` a ogni login tramite `hyleos-config-migrate`:

- legge la versione in `~/.config/hyleos/config-version`;
- se manca, crea solo i file assenti copiandoli dai default;
- per i file JSON fa un merge delle sole chiavi mancanti (i valori
  dell'utente non vengono mai sovrascritti);
- `hyprland.conf` viene convertito in `hyprland.lua` con backup del vecchio
  file;
- supporta `--dry-run` e scrive un log in `~/.local/state/hyleos/`.

Per aggiungere un default: modificalo in `out/rootfs-overlay/etc/skel` o
`/etc/xdg`, incrementa `CONFIG_VERSION` in `hyleos-config-migrate`, aggiungi il
caso di migrazione e un test in `tests/test_config_migrate.py`.

---

## 4. Repository XBPS proprio (punti 4, 5, 6)

### Perché

Il vecchio repository esterno `sofijacom/hyprland-void` era compilato contro
`libdisplay-info.so.2` e Qt 6.11.1. Void ha aggiornato entrambe le librerie,
quindi la ISO dipendeva da due pacchetti-shim:

- `libdisplay-info0.2` (libreria reale presa da Debian);
- `libqt6-private-api-6.11.1` (pacchetto virtuale, solo metadato).

Gli shim sono stati **rimossi**. Al loro posto HyleOS costruisce e ospita i
propri pacchetti, ricompilando lo stack Hyprland contro le librerie attuali.

### Cosa contiene il repository

Ricompilati dai sorgenti:

- `aquamarine`
- `hyprland`
- `hyprland-qt-support`
- `hyprpolkitagent`

Pacchetti HyleOS:

| Pacchetto | Contenuto |
| --- | --- |
| `hyleos-base` | Meta-pacchetto del sistema base (kernel, driver, rete, audio). |
| `hyleos-desktop` | Meta-pacchetto desktop + default in `/etc/skel`, `/etc/xdg`, GTK. |
| `hyleos-theme` | Asset, sfondi, logo, tema. |
| `hyleos-dms` | Binario `dms` + albero QML in `/etc/xdg/quickshell/hyleos-dms`. |
| `hyleos-session` | `hyleos-session`, `hyleos-dms-start`, migrazione config, updater, kernel-cleanup. |
| `hyleos-installer` | Installer GTK4 e launcher. |
| `hyleos-store` | Store GTK4, backend XBPS, helper root, policy polkit. |
| `hyle` | CLI `hyle`/`hyle-welcome` e benvenuto grafico. |

### Versioni bloccate

`hyleos-dms` dipende da `quickshell=0.3.2_1` (versione **esatta**). Se Void
aggiorna Quickshell, il pacchetto non si installa finché i QML non sono stati
testati e la versione non viene aggiornata insieme a
`build/qml-lint-targets.txt`. È intenzionale: meglio un aggiornamento che si
ferma, di un desktop che si rompe a metà transazione.

La versione di `hyleos-dms` segue `VERSION` in
`out/rootfs-overlay/etc/xdg/quickshell/hyleos-dms/VERSION` (attualmente
`1.6.2`).

### Costruire il repository

Requisiti: una copia di `void-packages` con `xbps-src` inizializzato e un
albero con i template di Hyprland. Serve spazio disco (almeno 10–15 GB liberi)
e una macchina con accesso alla rete.

**Void non pacchettizza Hyprland**, quindi i template vanno presi da un fork
(per esempio `sofijacom/hyprland-void`, che tiene i template allineati a
Hyprland 0.57.x). `build-repo.sh` copia automaticamente in `void-packages`
tutti i template mancanti, comprese le dipendenze (`hyprlang`, `hyprwire`,
`hyprgraphics`, `lua55`, `glaze`, `sdbus-cpp`, ...):

```sh
git clone --depth=1 https://github.com/void-linux/void-packages ~/void-packages
cd ~/void-packages && ./xbps-src binary-bootstrap
git clone --depth=1 https://github.com/sofijacom/hyprland-void /tmp/hypr-templates

cd /percorso/hyleos-void-fixed
VOID_PACKAGES=~/void-packages \
HYPR_SRCPKGS_DIR=/tmp/hypr-templates \
./build/build-repo.sh
```

Se `void-packages` contiene già i template Hyprland, `HYPR_SRCPKGS_DIR` può
essere omesso.

**Va eseguito come utente normale, mai con `doas`/`sudo`**: `xbps-src` rifiuta
root e usa `xbps-uchroot` (setgid) per i chroot, quindi l'utente deve essere nel
gruppo `xbuilder` (su Void lo è già l'utente principale). `build-repo.sh`
controlla entrambe le cose e si ferma con un messaggio chiaro se mancano.

Output: `out/hyleos-repo/x86_64/` con i `.xbps` e `x86_64-repodata`.

Firma: **obbligatoria se pubblichi via HTTP/HTTPS**, opzionale per un
repository locale (path assoluto). XBPS rifiuta i repository remoti non
firmati, quindi senza firma i sistemi installati non riceverebbero mai gli
aggiornamenti HyleOS.

La chiave privata predefinita è `~/.ssh/id_rsa` dell'utente che lancia la
build (PEM RSA); si può cambiare con `HYLEOS_PRIVKEY`:

```sh
HYLEOS_SIGN=1 HYLEOS_SIGNEDBY="Hyle OS Team <team@hyleos.org>" \
HYLEOS_PRIVKEY=$HOME/.ssh/id_rsa \
VOID_PACKAGES=~/void-packages ./build/build-repo.sh
```

Lo script, nell'ordine: rimuove indice e `.sig2` vecchi, indicizza i pacchetti
(`xbps-rindex -a`), firma ogni pacchetto (`-S`, crea `<pkg>.xbps.sig2`) e
infine firma l'indice (`-s`). La firma dell'indice è **incorporata** in
`x86_64-repodata` (chiavi `public-key`, `signature-by`, `signature-type`):
non esiste un file `*-repodata.sig` separato. Per questo `mkiso.sh` e
`publish-repo.sh` leggono l'indice e verificano anche che ogni pacchetto abbia
il suo `.sig2` (`build/lib-repo.sh`), invece di cercare un file che non c'è.
Al termine della firma `build-repo.sh` riverifica lo stato e fallisce se
l'indice o anche un solo pacchetto non risultano firmati.

### Usare il repository nella ISO

`mkiso.sh` rileva automaticamente `out/hyleos-repo/x86_64/x86_64-repodata`:

- se c'è, usa il repository HyleOS e include i pacchetti `hyleos-*`;
- se manca, usa overlay + repository Void ufficiali e stampa un avviso.

Per configurare il repository sui sistemi **installati**, passa l'URL alla
build:

```sh
HYLEOS_REPO_URL=https://tuo-dominio/hyleos-repo/x86_64 ./build/mkiso.sh
```

Lo script scrive `/etc/xbps.d/10-hyleos-repo.conf` nell'overlay. Senza URL la
ISO resta sui soli repository Void ufficiali.

Se il repository locale è firmato, `mkiso.sh` include anche la chiave pubblica
in `/var/db/xbps/keys/<fingerprint>.plist`: la ISO (e quindi il sistema
installato) si fida del repository senza importazioni al primo avvio. La
chiave si estrae dall'indice, non serve quella privata:

```sh
./build/repo-key.sh                                  # mostra fingerprint
./build/repo-key.sh --out /tmp/keys                  # scrive il .plist
```

Disattivabile con `HYLEOS_EMBED_REPO_KEY=0`; su un sistema già installato il
`.plist` si copia a mano in `/var/db/xbps/keys/`.

### Installazioni beta esistenti

Le ISO precedenti copiavano i file di HyleOS dall'overlay senza registrarli in
XBPS. Su quei sistemi gli aggiornamenti futuri non toccherebbero i componenti
HyleOS. Per adottarli una volta sola, dopo aver configurato il repository:

```sh
doas xbps-install -S
doas xbps-install -Sy -I hyleos-base hyleos-theme hyleos-dms \
    hyleos-session hyleos-installer hyleos-store hyle hyleos-desktop
```

`-I` ignora i conflitti di file: il contenuto è identico perché pacchetti e
overlay nascono dagli stessi file. Dalle ISO successive i pacchetti sono già
installati e registrati, quindi l'adozione non serve più.

### Pubblicare il repository

Non pubblicare mai direttamente: vedi la sezione canary (punto 12).

```sh
./build/publish-repo.sh                                  # mostra cosa farebbe
HYLEOS_PUBLISH_DIR=/var/www/hyleos-repo/x86_64 ./build/publish-repo.sh
HYLEOS_PUBLISH_RSYNC=utente@vps:/var/www/hyleos-repo/x86_64 ./build/publish-repo.sh
```

Al primo `xbps-install -S` i sistemi già installati chiedono di importare la
chiave pubblica del repository: è normale, va accettata dopo aver verificato
il fingerprint. Gli aggiornamenti lanciati da HyleOS usano `-y`, quindi
importano la chiave senza bloccare; il prompt interattivo si vede solo se si
esegue `xbps-install -S` a mano. Se la chiave è già in
`/var/db/xbps/keys/` (ISO nuove o copia manuale del `.plist` prodotto da
`repo-key.sh`) il prompt non compare affatto.

---

## 5. Controlli pre-build (punto 7)

Oltre a `check-overlay.sh`:

- **QML**: `build/qml-lint-targets.txt` elenca i file QML modificati rispetto
  a DMS upstream. Con `qmllint` installato (`qt6-declarative-tools`) il
  controllo è automatico. Dopo un aggiornamento di DMS/Quickshell, aggiungi i
  nuovi file modificati e rilancia il controllo.
- **Python**: `py_compile` su tutti i file; se disponibile, `ruff check`.
- **Shell**: `sh -n`/`bash -n` su tutti gli script.

La build non deve mai partire con un errore in questi controlli.

---

## 6. Test end-to-end dell'installer (punto 8)

L'installer è stato diviso in mixin mantenendo il comportamento:

- `installer/preflight.py` — controlli root/UEFI/strumenti;
- `installer/disks.py` — rilevamento e selezione dischi;
- `installer/install_worker.py` — esecuzione dell'installazione;
- `installer/ui.py` — pagine GTK;
- `installer.py` — entrypoint e composizione della finestra.

Requisiti: `qemu-system-x86_64`, `qemu-img`, firmware `edk2-ovmf`, `/dev/kvm`
consigliato.

```sh
./build/e2e-installer.sh                 # ISO più recente in out/
./build/e2e-installer.sh out/hyleos-1.0-beta-x86_64.iso
HYLEOS_E2E_DISK_SIZE=60G ./build/e2e-installer.sh
```

Nella VM:

1. avvia l'installer;
2. verifica che il disco vuoto sia selezionabile e che il disco live/root sia
   nascosto (solo quello confermato);
3. completa le 5 pagine e installa;
4. riavvia dal disco.

Dopo il primo avvio, **dentro il sistema installato**:

```sh
./build/verify-installed-system.sh --strict
```

Controlla `os-release`, UEFI, `/etc/fstab` con UUID reali, voce EFI, initramfs
senza modulo `vmklive`, `machine-id`, assenza di utente `anon`/autologin,
servizi runit, presenza di updater e kernel-cleanup.

Lo stesso test va ripetuto su un SSD reale prima di una release: la VM non
copre firmware, controller e dischi fisici.

---

## 7. Politica kernel (punto 9)

`/usr/libexec/hyleos/kernel-cleanup` mantiene sempre:

- gli ultimi **2** kernel (configurabile con `--keep N`);
- il kernel **in uso**, anche se è vecchio.

Usa `vkpurge list` per sapere quali kernel non sono più posseduti da un
pacchetto `linux*`: non indovina e non cancella kernel ancora tracciati da
XBPS. Se `vkpurge` manca, non rimuove nulla.

```sh
kernel-cleanup --list          # cosa c'è e cosa è protetto
kernel-cleanup --dry-run       # cosa rimuoverebbe
doas kernel-cleanup            # rimuove solo i kernel oltre i 2 più recenti
doas kernel-cleanup --keep 3
```

`hyle clean` chiama automaticamente la pulizia kernel. Log:
`/var/log/hyleos/kernel-cleanup.log`.

---

## 8. Aggiornamento unico (punto 10)

`/usr/bin/hyleos-user-update` è l'ingresso unico agli aggiornamenti lanciati da
`hyle update`: aggiorna prima le AppImage come utente normale (best-effort,
nessuna elevazione) e poi passa a `hyleos-system-update` per la parte XBPS di
sistema. Lo Store non mostra aggiornamenti: fa solo
Esplora/Cerca/Installa/Disinstalla/Apri; gli aggiornamenti restano in DMS →
Impostazioni → Aggiornamenti di sistema.

`hyleos-system-update` resta il flusso di aggiornamento di sistema usato da:

- DMS → “Aggiornamenti di sistema” (`updaterUseCustomCommand: true`);
- `hyle update`, tramite `hyleos-user-update`.

Sequenza:

1. AppImage: `hyleos-appimage-update` come utente normale, senza `pkexec`,
   `doas` o `sudo` (catalogo `appimage-catalog.json`, hash SHA256 obbligatorio);
2. verifica connettività;
3. verifica almeno **3 GB** liberi su `/`;
4. `xbps-install -uy xbps` (aggiorna prima il gestore pacchetti);
5. `xbps-install -Suy` (sistema);
6. log completo in `/var/log/hyleos/update.log`;
7. se sono cambiati kernel o componenti core, crea
   `/var/lib/hyleos/reboot-required` e avvisa l'utente.

L'aggiornamento di sistema chiede l'elevazione da solo. In un terminale usa
`doas`/`sudo` (la password si vede); da DMS, dove non c'è una TTY, usa `pkexec` e
la finestra di autenticazione dell'agente polkit del desktop. Se nessuno dei due
è disponibile, esce con un errore esplicito invece di restare appeso in
background. Non richiede di essere lanciato come root.

L'azione polkit è `org.hyleos.update.system`
(`/usr/share/polkit-1/actions/org.hyleos.update.system.policy`), così il
prompt dice cosa sta per succedere invece di usare l'azione generica di
pkexec.

In caso di errore il log resta disponibile e l'utente può eseguire
`hyle repair`.

---

## 9. Store: XBPS e AppImage (punto 11)

Lo Store fa solo **Esplora/Cerca/Installa/Disinstalla/Apri**: non mostra
aggiornamenti, che restano in DMS → Impostazioni → Aggiornamenti di sistema.
Usa XBPS come backend predefinito per le applicazioni di sistema e il catalogo
`appimage-catalog.json` per le AppImage. Ogni voce del catalogo deve avere
`sha256` valido: senza hash non si installa. Per montare ed eseguire le AppImage
serve **FUSE2**.

Prima di ogni installazione XBPS viene eseguita una simulazione XBPS:

- da root (o se `/var/db/xbps` è scrivibile) si usa `xbps-install -Sny <pkg>`,
  che aggiorna anche l'indice dei repository;
- da utente normale `-S` fallirebbe per permessi, quindi il client usa
  `xbps-install -ny <pkg>` sulla cache esistente e, se questa non basta,
  lascia il verdetto all'helper root;
- l'helper `/usr/libexec/hyleos/hyleos-store-xbps` rifà sempre il controllo
  con `-Sny` come root: è lui la fonte autorevole, il client serve solo a
  dare un messaggio immediato.

Se la transazione richiede di aggiornare `xbps`, `glibc`, `base-system`,
`hyprland`, `hyleos-*`, `quickshell`, `mesa`, `wayland`, `elogind` o `dbus`,
l'installazione viene **bloccata** e lo Store invita ad aggiornare prima il
sistema da DMS → Impostazioni → Aggiornamenti di sistema.

Componenti protetti (non rimovibili dallo Store, sia nel client sia
nell'helper root):

`base-system`, `hyprland`, `hyleos-dms`, `hyleos-desktop`, `hyleos-store`,
`hyleos-session`, `quickshell`, `NetworkManager`, `pipewire`,
`pipewire-pulse`, `wireplumber`, `elogind`, `dbus`, `xbps`, `glibc`, `linux*`.

L'helper `/usr/libexec/hyleos/hyleos-store-xbps` è l'unico punto che esegue
XBPS come root; la policy polkit è
`/usr/share/polkit-1/actions/org.hyleos.store.xbps.policy`.

Per aggiungere un'app al catalogo XBPS: aggiungila a `RECOMMENDED` in
`usr/share/hyleos/xbps_backend.py` e verifica che il nome del pacchetto esista
in Void (`xbps-query -R <pkg>`). Per aggiungere un'AppImage: aggiungi la voce a
`/usr/share/hyleos/appimage-catalog.json` con `download_url` e `sha256` reali
(il catalogo di esempio non è installabile).

---

## 10. Canary e pubblicazione (punto 12)

Nessun aggiornamento del repository viene pubblicato senza essere stato
provato in una VM canary.

```sh
./build/canary.sh init          # installa l'ultima ISO nella VM
./build/canary.sh snapshot      # salva lo stato pulito ("clean")
./build/canary.sh update        # ripristina lo snapshot e avvia la VM
```

Dentro la VM:

1. esegui “Aggiornamenti di sistema” (o `hyleos-system-update`);
2. riavvia;
3. verifica login, Hyprland, DMS, pkexec (apri lo Store), audio, rete;
4. esegui `./build/verify-installed-system.sh --strict`.

Se tutto funziona:

```sh
./build/canary.sh mark-ok
./build/publish-repo.sh
```

`mark-ok` registra l'hash SHA-256 di `x86_64-repodata`.
`publish-repo.sh` confronta quell'hash con il repository attuale: se il
repository è cambiato dopo il test, la pubblicazione viene **bloccata**. Per
forzare (sconsigliato): `HYLEOS_FORCE_PUBLISH=1`.

Stato della canary:

```sh
./build/canary.sh status
```

---

## 11. Usabilità mouse-first (Parte 2)

Tutte le funzioni restano raggiungibili da tastiera: questa parte aggiunge
scorciatoie mouse, non le sostituisce.

### Modalità finestre: tiling o finestre libere

La scelta è persistente in `~/.config/hyleos/window-mode.json`:

```json
{ "version": 1, "mode": "tiling" }
```

- `tiling` (default): le finestre vengono affiancate automaticamente.
- `floating`: finestre libere, spostabili con `SUPER + clic sinistro` e
  ridimensionabili con `SUPER + clic destro` o trascinando i bordi.

Il comando unico è `/usr/bin/hyleos-window-mode`:

```sh
hyleos-window-mode get
hyleos-window-mode set floating
hyleos-window-mode toggle
hyleos-window-mode apply
```

`hyprland.lua` legge lo stesso file all'avvio e abilita/disabilita la regola
nominata `hyleos-floating-mode`; il comando la aggiorna a caldo via
`hyprctl eval`, quindi non serve riavviare la sessione. Dialoghi di sistema,
store, installer, welcome, calcolatrice e selettori file restano sempre
flottanti e centrati anche in modalità tiling.

La modalità si cambia da:

- Impostazioni DMS → Workspaces → “Finestre e mouse”;
- Centro di Controllo → pulsante in alto a sinistra nella testata;
- `hyle-welcome` al primo avvio;
- terminale: `hyleos-window-mode set floating`.

IPC DMS: `dms ipc call windowmode get|set|toggle|reload`.

### Barra e desktop

- Controlli finestra nella barra (solo con finestra attiva): flottante,
  massimizza/ripristina, chiudi.
- Pulsante “Panoramica finestre” accanto al titolo: apre la panoramica DMS.
- Dock abilitato di default; app e preferiti si attivano con un clic.
- Clic destro sul desktop: File Manager, Terminale, Hyle Store, sfondo,
  aspetto, impostazioni di sistema, blocco/riavvio/spegnimento.
- I widget del desktop mantengono il loro clic destro: il menu è sul layer
  dello sfondo, sotto di essi.

### Verifiche

- `tests/test_window_mode.py`: persistenza, toggle, file corrotto e
  protezione dei dialoghi.
- `tests/test_config_migrate.py`: creazione del nuovo file di default senza
  sovrascrivere una scelta già fatta.
- `./build/check-overlay.sh` e tutti i test passano; la verifica grafica dal
  vivo (clic, hover, trascinamento) va fatta in una sessione Hyprland reale.

---

## 12. Flusso di release

1. Modifica l'overlay (fonte canonica).
2. Se serve, incrementa `CONFIG_VERSION` e aggiungi la migrazione.
3. Aggiorna `build/qml-lint-targets.txt` se hai toccato QML.
4. `./build/check-overlay.sh` → zero errori.
5. Esegui i test: `tests/test_config_migrate.py`,
   `tests/test_kernel_cleanup.py`, `tests/test_window_mode.py`,
   `tests/test_xbps_backend.py`, `tests/test_appimage_backend.py`,
   `tests/test_user_update.py`.
6. `./build/sync-packages.sh` se hai toccato file impacchettati.
7. `VOID_PACKAGES=... ./build/build-repo.sh`.
8. `./build/canary.sh update` + verifica + `mark-ok`.
9. `./build/publish-repo.sh`.
10. `HYLEOS_REPO_URL=... ./build/mkiso.sh`.
11. `./build/e2e-installer.sh` + `./build/verify-installed-system.sh`.
12. Test su SSD reale, poi release.

---

## 13. Cosa manca in questo ambiente di sviluppo

Alcune verifiche non possono essere eseguite qui e vanno fatte sulla macchina
di build:

- `xbps-src` non installato → `build-repo.sh` non è stato eseguito;
- spazio su `/` insufficiente (~1.3 GB liberi) → la compilazione richiede
  un'altra macchina o un disco esterno;
- `qemu-system-x86_64`/OVMF assenti → `e2e-installer.sh` e `canary.sh` non
  eseguiti;
- `qmllint` assente → controllo QML saltato con avviso;
- `ruff`/`shellcheck` assenti → controlli opzionali saltati.

I test unitari e `check-overlay.sh` passano in questo ambiente.
