# Hyprland 0.57 + DankMaterialShell: cosa e' cambiato

## Sintomo

Dopo il boot Hyprland parte (wallpaper, a volte notifiche), ma la shell DMS non
compare e i tasti non fanno nulla.

## Causa

Hyprland **0.57** (28/10/2026) ha rimosso del tutto il supporto a
`hyprland.conf`. Il repository usato dalla ISO contiene `hyprland-0.57.1`, quindi
il file `~/.config/hypr/hyprland.conf` distribuito dalla ISO veniva **ignorato**:

- niente `exec-once` -> `dms` non veniva mai avviato;
- niente keybind -> nessuna scorciatoia funzionante;
- Hyprland restava con la configurazione di default (o ne generava una).

In piu' DMS 1.6.x e' solo il *backend*: la UI viene lanciata come processo
separato `qs` (symlink a `quickshell`). Se `quickshell` non c'e', `dms run`
resta vivo (notifiche comprese) ma non mostra nessuna shell.

## Cosa e' stato modificato

| File | Modifica |
|------|----------|
| `out/rootfs-overlay/etc/skel/.config/hypr/hyprland.lua` | nuova configurazione Lua (monitor, input, decorazioni, env, autostart, keybind) |
| `out/rootfs-overlay/etc/skel/.config/hypr/hyprland.conf` | rimosso |
| `out/rootfs-overlay/usr/bin/hyleos-dms-start` | launcher DMS con log, controllo `quickshell`/`qs`, watchdog e avvio di emergenza |
| `out/rootfs-overlay/usr/bin/hyleos-session` | migrazione automatica `hyprland.conf` -> `hyprland.lua` |
| `out/rootfs-overlay/usr/share/hyleos/installer.py` | il layout tastiera viene scritto in `hyprland.lua` |
| `build/mkiso.sh` | `quickshell`, `libnotify`, `xsettingsd`; controlli di sanita'; `HYLEOS_BUILD_DIR`/`HYLEOS_OUTPUT_ISO` |

La configurazione Lua e' stata verificata con il vero binario 0.57.1:

```
Hyprland --verify-config  ->  config ok
```

## Build

La ISO richiede circa **8-9 GB liberi** (rootfs + squashfs + ISO). Se la
partizione di sistema non basta, usare un disco esterno ext4:

```sh
export HYLEOS_BUILD_DIR=/mnt/usb/hyleos-build
export HYLEOS_OUTPUT_ISO=/mnt/usb/hyleos-1.0-beta.iso
cd hyleos-void-fixed   # radice del progetto
./build/mkiso.sh
```

Per non riscaricare tutto, copiare prima la cache:

```sh
mkdir -p /mnt/usb/hyleos-build
cp -a /var/tmp/hyleos-build/xbps-cache /mnt/usb/hyleos-build/
cp -a /var/tmp/hyleos-build/void-mklive /mnt/usb/hyleos-build/
```

## Verifica dopo il boot

1. `Super+Return` deve aprire `kitty`.
2. La barra DMS deve comparire in alto.
3. Se non compare, aprire un TTY (`Ctrl+Alt+F2`), login, e controllare:

```sh
cat ~/.local/state/hyleos/dms-start.log
pgrep -a qs
command -v qs quickshell dms
```

## Riparare un'installazione esistente senza reinstallare

Da un TTY, con i file nuovi a portata di mano:

```sh
mkdir -p ~/.config/hypr
cp /etc/skel/.config/hypr/hyprland.lua ~/.config/hypr/hyprland.lua
mv ~/.config/hypr/hyprland.conf ~/.config/hypr/hyprland.conf.bak 2>/dev/null
sudo xbps-install -S quickshell
# poi riavviare la sessione (logout o reboot)
```
