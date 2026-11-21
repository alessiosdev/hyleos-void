#!/bin/bash
# build-repo.sh — costruisce il repository XBPS di HyleOS (punti 4, 5, 6).
#
# Cosa fa:
#   1. sincronizza il payload dei pacchetti hyleos-* dall'overlay;
#   2. ricompila aquamarine, hyprland, hyprland-qt-support e hyprpolkitagent
#      dai sorgenti contro le librerie attuali di Void (niente più shim
#      libdisplay-info0.2 / libqt6-private-api);
#   3. costruisce hyleos-theme, hyleos-dms, hyleos-session, hyleos-installer,
#      hyleos-store, hyle, hyleos-desktop, hyleos-base;
#   4. indicizza il repository in out/hyleos-repo/x86_64.
#
# Requisiti: una copia di void-packages con xbps-src e i template di Hyprland.
#   git clone --depth=1 https://github.com/void-linux/void-packages ~/void-packages
#   ~/void-packages/xbps-src binary-bootstrap
# Va eseguito come utente normale (gruppo xbuilder), NON come root: xbps-src
# rifiuta root e usa xbps-uchroot (setgid) per i chroot di build.
# Se i template Hyprland non sono in void-packages, passa HYPR_SRCPKGS_DIR
# puntando a un albero che li contiene (es. un fork di void-packages).
#
# Uso:
#   VOID_PACKAGES=~/void-packages ./build/build-repo.sh
#   HYLEOS_REPO_URL=https://esempio/hyleos-repo/x86_64 ./build/build-repo.sh
#
# Variabili:
#   VOID_PACKAGES        percorso di void-packages (default ~/void-packages)
#   HYPR_SRCPKGS_DIR     albero alternativo con i template Hyprland
#   HYLEOS_REPO_DIR      destinazione repo (default out/hyleos-repo/x86_64)
#   HYLEOS_REUSE_STACK=1 riutilizza lo stack Hyprland già compilato in
#                        hostdir/binpkgs (solo per riprendere una build
#                        interrotta: non usarlo per build pulite)
#   HYLEOS_SIGN=1        firma il repository (chiave PEM RSA, default
#                        ~/.ssh/id_rsa dell'utente che lancia la build)
#   HYLEOS_SIGNEDBY      stringa "Nome <email>" per la firma
#   HYLEOS_PRIVKEY       percorso della chiave privata (default ~/.ssh/id_rsa)
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
VOID_PACKAGES="${VOID_PACKAGES:-$HOME/void-packages}"
HYLEOS_REPO_DIR="${HYLEOS_REPO_DIR:-$ROOT_DIR/out/hyleos-repo/x86_64}"
HYPR_SRCPKGS_DIR="${HYPR_SRCPKGS_DIR:-}"
HYLEOS_PRIVKEY="${HYLEOS_PRIVKEY:-$HOME/.ssh/id_rsa}"

# xbps-src NON va eseguito come root: rifiuta esplicitamente root e usa
# xbps-uchroot (setgid, gruppo xbuilder) per entrare nei chroot di build.
# Qui controlliamo i prerequisiti e ci fermiamo con un messaggio chiaro.
if [ "$(id -u)" -eq 0 ]; then
    echo "ERRORE: esegui build-repo.sh come utente normale, non come root." >&2
    echo "xbps-src rifiuta root: usa xbps-uchroot (setgid) e il gruppo xbuilder." >&2
    echo "  ./build/build-repo.sh" >&2
    exit 1
fi

if ! id -nG | tr ' ' '\n' | grep -qx xbuilder; then
    echo "ERRORE: l'utente $(id -un) non è nel gruppo xbuilder." >&2
    echo "  doas usermod -aG xbuilder $(id -un)" >&2
    echo "  (poi esci e rientra nella sessione, oppure usa 'newgrp xbuilder')" >&2
    exit 1
fi

# La chiave serve solo se firmiamo: meglio fallire subito che dopo la build.
if [ "${HYLEOS_SIGN:-0}" = "1" ]; then
    [ -r "$HYLEOS_PRIVKEY" ] || {
        echo "ERRORE: chiave privata non leggibile: $HYLEOS_PRIVKEY" >&2
        echo "Generala con: ssh-keygen -t rsa -b 4096 -m PEM -f ~/.ssh/id_rsa" >&2
        exit 1
    }
    case "$(head -n 1 "$HYLEOS_PRIVKEY")" in
        "-----BEGIN"*"PRIVATE KEY-----") : ;;
        *)
            echo "ERRORE: $HYLEOS_PRIVKEY non è una chiave PEM." >&2
            echo "Rigenerala con -m PEM (ssh-keygen) oppure usa openssl genrsa." >&2
            exit 1
            ;;
    esac
fi

[ -x "$VOID_PACKAGES/xbps-src" ] || {
    echo "ERRORE: xbps-src non trovato in $VOID_PACKAGES" >&2
    echo "Clona void-packages e inizializza il chroot:" >&2
    echo "  git clone --depth=1 https://github.com/void-linux/void-packages ~/void-packages" >&2
    echo "  cd ~/void-packages && ./xbps-src binary-bootstrap" >&2
    exit 1
}

# Senza masterdir ogni build ripartirebbe da zero (o fallirebbe a metà).
# xbps-src usa masterdir-<machine> (es. masterdir-x86_64), non solo "masterdir".
if ! ls "$VOID_PACKAGES"/masterdir*/.xbps_chroot_init >/dev/null 2>&1; then
    echo "ERRORE: il chroot di xbps-src non è inizializzato in $VOID_PACKAGES." >&2
    echo "  cd $VOID_PACKAGES && ./xbps-src binary-bootstrap" >&2
    exit 1
fi

# Il repository deve restare scrivibile dall'utente: se esiste già ed è di
# root (build precedenti), fermati invece di produrre file non aggiornabili.
if [ -d "$HYLEOS_REPO_DIR" ] && [ ! -w "$HYLEOS_REPO_DIR" ]; then
    echo "ERRORE: $HYLEOS_REPO_DIR non è scrivibile da $(id -un)." >&2
    echo "  doas chown -R $(id -u):$(id -g) $HYLEOS_REPO_DIR" >&2
    exit 1
fi

echo "[*] Sincronizzazione payload dall'overlay..."
"$SCRIPT_DIR/sync-packages.sh"

# Template Hyprland. Void non pacchettizza Hyprland e le versioni che ha delle
# dipendenze sono troppo vecchie: Hyprland 0.57.1 richiede hyprutils >= 0.14.3,
# Void ne ha 0.11.0. Servono quindi i template del fork, che vanno anche a
# sostituire quelli troppo vecchi già presenti in void-packages.
#
# L'ordine conta: hyprutils per primo, poi hyprwayland-scanner (host tool) e
# aquamarine, che vanno ricompilati contro la nuova libhyprutils.
# hyprland-guiutils va incluso esplicitamente: la ISO lo installa e non è una
# dipendenza di hyprland, quindi senza questa riga il template viene copiato ma
# il pacchetto non finisce mai nel repository ("not found in repository pool").
HYPR_PKGS="hyprutils hyprwayland-scanner aquamarine hyprland hyprland-qt-support hyprpolkitagent hyprland-guiutils"

if [ -n "$HYPR_SRCPKGS_DIR" ]; then
    if [ ! -d "$HYPR_SRCPKGS_DIR/srcpkgs" ]; then
        echo "ERRORE: $HYPR_SRCPKGS_DIR non contiene srcpkgs/." >&2
        exit 1
    fi
    for p in $HYPR_PKGS; do
        [ -d "$HYPR_SRCPKGS_DIR/srcpkgs/$p" ] || {
            echo "ERRORE: $p non trovato in $HYPR_SRCPKGS_DIR/srcpkgs" >&2
            exit 1
        }
    done

    copied=0
    upgraded=0
    for src in "$HYPR_SRCPKGS_DIR"/srcpkgs/*; do
        p="$(basename "$src")"
        case "$p" in
            # hyprland-repo punta al repository binario esterno: non ci serve.
            # hyprland-devel è un duplicato del template hyprland.
            # sdbus-cpp esiste in Void come "sdbus-c++" (stessa versione 2.3.1):
            # compilarlo qui creerebbe un conflitto di file con quello di Void.
            hyprland-repo|hyprland-devel|sdbus-cpp) continue ;;
        esac
        dst="$VOID_PACKAGES/srcpkgs/$p"
        if [ -d "$dst" ]; then
            vnew="$(sed -n 's/^version=//p' "$src/template" 2>/dev/null | head -1)"
            vold="$(sed -n 's/^version=//p' "$dst/template" 2>/dev/null | head -1)"
            if [ -n "$vold" ] && [ -n "$vnew" ]; then
                rc=0
                xbps-uhelper cmpver "$vold" "$vnew" >/dev/null 2>&1 || rc=$?
                # 0 = uguali, 1 = quella in void-packages è più nuova (255 = più vecchia)
                if [ "$rc" -ne 255 ]; then
                    continue
                fi
            fi
            rm -rf "$dst"
            cp -a "$src" "$dst"
            upgraded=$((upgraded + 1))
        else
            cp -a "$src" "$dst"
            copied=$((copied + 1))
        fi
    done
    echo "[*] Template Hyprland: $copied nuovi, $upgraded aggiornati da $HYPR_SRCPKGS_DIR"

    # Il fork chiama la libreria D-Bus "sdbus-cpp"; Void la fornisce come
    # "sdbus-c++" (con -devel per gli header). Usiamo quella di Void.
    rm -rf "$VOID_PACKAGES/srcpkgs/sdbus-cpp"
    if [ -f "$VOID_PACKAGES/srcpkgs/xdg-desktop-portal-hyprland/template" ]; then
        sed -i 's/\bsdbus-cpp\b/sdbus-c++-devel/g' \
            "$VOID_PACKAGES/srcpkgs/xdg-desktop-portal-hyprland/template"
    fi

    # Il fork rimuove le mappature dei SONAME vecchi (es. libhyprutils.so.10).
    # Se restassero, il controllo "SONAME bump detected" di xbps-src fermerebbe
    # la build chiedendo il revbump di tutte le reverse dependency.
    if [ -f "$HYPR_SRCPKGS_DIR/common/shlibs_remove" ]; then
        while read -r soname _rest; do
            [ -n "$soname" ] || continue
            awk -v s="$soname" '$1 != s' "$VOID_PACKAGES/common/shlibs" \
                > "$VOID_PACKAGES/common/shlibs.tmp" &&
                mv "$VOID_PACKAGES/common/shlibs.tmp" "$VOID_PACKAGES/common/shlibs"
        done < "$HYPR_SRCPKGS_DIR/common/shlibs_remove"
    fi

    # xbps-src deve conoscere il pacchetto che fornisce ogni SONAME, altrimenti
    # si ferma con "cannot guess required shlibs" (libhyprlang, libhyprutils...).
    if [ -f "$HYPR_SRCPKGS_DIR/common/shlibs" ]; then
        while read -r soname pkg; do
            [ -n "$soname" ] || continue
            grep -q "^${soname} " "$VOID_PACKAGES/common/shlibs" || \
                echo "$soname $pkg" >> "$VOID_PACKAGES/common/shlibs"
        done < "$HYPR_SRCPKGS_DIR/common/shlibs"
    fi
else
    for p in $HYPR_PKGS; do
        [ -d "$VOID_PACKAGES/srcpkgs/$p" ] || {
            cat >&2 <<'EOF'
ERRORE: mancano i template di Hyprland in void-packages.

Void non pacchettizza Hyprland e le sue dipendenze sono troppo vecchie: servono
i template aggiornati. Clona un albero di template e passalo a HYPR_SRCPKGS_DIR:

  git clone --depth=1 https://github.com/sofijacom/hyprland-void /tmp/hypr-templates
  HYPR_SRCPKGS_DIR=/tmp/hypr-templates ./build/build-repo.sh
EOF
            echo "Template mancante: $p" >&2
            exit 1
        }
    done
fi

# I nostri pacchetti.
for p in hyleos-theme hyleos-dms hyleos-session hyleos-installer hyleos-store hyle hyleos-desktop hyleos-base; do
    rm -rf "$VOID_PACKAGES/srcpkgs/$p"
    cp -a "$ROOT_DIR/packages/$p" "$VOID_PACKAGES/srcpkgs/$p"
done

cd "$VOID_PACKAGES"

# Pacchetti compilati in una sessione precedente possono essere legati a
# versioni vecchie delle dipendenze (es. aquamarine contro hyprutils 0.11):
# vanno ricostruiti, altrimenti finiscono nel repository con ABI sbagliata.
# L'unica eccezione è una ripresa esplicita con HYLEOS_REUSE_STACK=1, quando
# lo stack è stato compilato pochi minuti fa nella stessa sessione di build.
if [ "${HYLEOS_REUSE_STACK:-0}" = "1" ]; then
    echo "[*] Riutilizzo dello stack Hyprland già presente in hostdir/binpkgs..."
    ls "$VOID_PACKAGES"/hostdir/binpkgs/*.x86_64.xbps >/dev/null 2>&1 || {
        echo "ERRORE: HYLEOS_REUSE_STACK=1 ma hostdir/binpkgs è vuoto." >&2
        exit 1
    }
    # Una ripresa può essere successiva a un cambio di HYPR_PKGS: costruisci
    # solo i pacchetti espliciti che mancano, senza ricompilare tutto lo stack.
    missing_stack=()
    for p in $HYPR_PKGS; do
        if ! find "$VOID_PACKAGES/hostdir/binpkgs" -maxdepth 1 \
                -name "${p}-[0-9]*.x86_64.xbps" | grep -q .; then
            missing_stack+=("$p")
        fi
    done
    if [ "${#missing_stack[@]}" -gt 0 ]; then
        echo "[*] Pacchetti stack mancanti, li costruisco: ${missing_stack[*]}"
        for p in "${missing_stack[@]}"; do
            echo "    -> $p"
            ./xbps-src pkg "$p"
        done
    fi
else
    rm -rf "$VOID_PACKAGES/hostdir/binpkgs"

    echo "[*] Ricompilazione stack Hyprland contro le librerie attuali di Void..."
    for p in $HYPR_PKGS; do
        echo "    -> $p"
        ./xbps-src pkg "$p"
    done
fi

echo "[*] Costruzione pacchetti HyleOS..."
for p in hyleos-theme hyleos-dms hyleos-session hyleos-installer hyleos-store hyle hyleos-desktop hyleos-base; do
    echo "    -> $p"
    ./xbps-src pkg "$p"
done

echo "[*] Raccolta pacchetti in $HYLEOS_REPO_DIR..."
mkdir -p "$HYLEOS_REPO_DIR"
# In una build pulita hostdir/binpkgs viene svuotato all'inizio: tutto quello
# che contiene appartiene a questa esecuzione. Con HYLEOS_REUSE_STACK=1 invece
# lo stack Hyprland è stato compilato prima: il filtro -newer lo escluderebbe.
find "$VOID_PACKAGES/hostdir/binpkgs" -maxdepth 2 -name '*.x86_64.xbps' \
    -exec cp -f {} "$HYLEOS_REPO_DIR/" \;

# Indice e firme vecchi vanno rimossi prima di indicizzare: altrimenti si
# pubblicano .sig2 orfani o un repodata che descrive pacchetti non più presenti.
rm -f "$HYLEOS_REPO_DIR"/*-repodata* "$HYLEOS_REPO_DIR"/*.sig2 2>/dev/null || true
xbps-rindex -a "$HYLEOS_REPO_DIR"/*.x86_64.xbps

if [ "${HYLEOS_SIGN:-0}" = "1" ]; then
    echo "[*] Firma dei pacchetti..."
    for f in "$HYLEOS_REPO_DIR"/*.x86_64.xbps; do
        xbps-rindex --privkey "$HYLEOS_PRIVKEY" -S "$f"
    done
    echo "[*] Firma dell'indice..."
    xbps-rindex --privkey "$HYLEOS_PRIVKEY" \
        --signedby "${HYLEOS_SIGNEDBY:-Hyle OS Team <team@hyleos.org>}" \
        -s "$HYLEOS_REPO_DIR"
    # Autoverifica: meglio scoprire ora una firma incompleta che dopo la
    # pubblicazione, quando i client rifiuterebbero il repository.
    . "$SCRIPT_DIR/lib-repo.sh"
    _sig_state="$(repo_signature_state "$HYLEOS_REPO_DIR")"
    [ "$_sig_state" = "signed" ] || {
        echo "ERRORE: firma del repository incompleta ($_sig_state)." >&2
        exit 1
    }
    echo "[OK] Repository firmato e verificato."
fi

echo
echo "[OK] Repository pronto: $HYLEOS_REPO_DIR"
echo "     Pacchetti: $(ls -1 "$HYLEOS_REPO_DIR"/*.x86_64.xbps | wc -l)"
echo
echo "Prossimi passi:"
echo "  1. Pubblica la directory su GitHub Pages/Releases o un VPS."
echo "  2. Aggiungi su ogni sistema /etc/xbps.d/10-hyleos-repo.conf:"
echo "       repository=https://TUO-DOMINIO/hyleos-repo/x86_64"
echo "  3. Aggiorna la ISO con: HYLEOS_REPO_DIR=$HYLEOS_REPO_DIR ./build/mkiso.sh"
echo "  4. Prima di pubblicare, prova l'aggiornamento nella VM canary (punto 12)."
