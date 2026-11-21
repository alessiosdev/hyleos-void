#!/bin/bash
# publish-repo.sh — pubblica il repository XBPS solo dopo il test canary.
#
# Guardia del punto 12: confronta l'hash di x86_64-repodata con quello
# registrato da ./build/canary.sh mark-ok. Se il repository è cambiato dopo
# l'ultimo test, la pubblicazione viene bloccata.
#
# Uso:
#   ./build/publish-repo.sh                      # mostra cosa farebbe
#   HYLEOS_PUBLISH_DIR=/var/www/hyleos ./build/publish-repo.sh
#   HYLEOS_PUBLISH_RSYNC=utente@vps:/var/www/hyleos ./build/publish-repo.sh
#
# Variabili:
#   HYLEOS_REPO_DIR        repo locale (default out/hyleos-repo/x86_64)
#   HYLEOS_PUBLISH_DIR     destinazione locale (rsync)
#   HYLEOS_PUBLISH_RSYNC   destinazione remota (rsync)
#   HYLEOS_FORCE_PUBLISH=1 salta la guardia canary (sconsigliato)
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="${HYLEOS_REPO_DIR:-$ROOT_DIR/out/hyleos-repo/x86_64}"
OK_MARKER="${HYLEOS_VM_DIR:-$ROOT_DIR/out/vm}/.canary-ok"

[ -d "$REPO_DIR" ] || { echo "ERRORE: repo non trovato: $REPO_DIR" >&2; exit 1; }
[ -f "$REPO_DIR/x86_64-repodata" ] || {
    echo "ERRORE: indice mancante. Esegui prima ./build/build-repo.sh" >&2
    exit 1
}

current_sha="$(sha256sum "$REPO_DIR/x86_64-repodata" | awk '{print $1}')"

if [ "${HYLEOS_FORCE_PUBLISH:-0}" != "1" ]; then
    [ -f "$OK_MARKER" ] || {
        echo "PUBBLICAZIONE BLOCCATA: nessun test canary registrato." >&2
        echo "Esegui ./build/canary.sh update, verifica la VM e poi ./build/canary.sh mark-ok." >&2
        exit 1
    }
    tested_sha="$(sed -n 's/^repodata_sha256=//p' "$OK_MARKER" | tail -1)"
    if [ -z "$tested_sha" ]; then
        echo "PUBBLICAZIONE BLOCCATA: il marker canary non contiene l'hash del repo." >&2
        exit 1
    fi
    if [ "$tested_sha" != "$current_sha" ]; then
        echo "PUBBLICAZIONE BLOCCATA: il repository è cambiato dopo il test canary." >&2
        echo "  testato:  $tested_sha" >&2
        echo "  attuale:  $current_sha" >&2
        echo "Ripeti il test canary prima di pubblicare." >&2
        exit 1
    fi
    echo "[OK] Canary verificata per questo repository."
fi

DEST="${HYLEOS_PUBLISH_DIR:-}"
REMOTE="${HYLEOS_PUBLISH_RSYNC:-}"

if [ -z "$DEST" ] && [ -z "$REMOTE" ]; then
    cat <<EOF
Repository pronto: $REPO_DIR
Pacchetti: $(ls -1 "$REPO_DIR"/*.x86_64.xbps 2>/dev/null | wc -l)

Per pubblicare scegli una destinazione:
  HYLEOS_PUBLISH_DIR=/var/www/hyleos-repo/x86_64 ./build/publish-repo.sh
  HYLEOS_PUBLISH_RSYNC=utente@vps:/var/www/hyleos-repo/x86_64 ./build/publish-repo.sh

Poi su ogni sistema:
  echo 'repository=https://TUO-DOMINIO/hyleos-repo/x86_64' | \\
    doas tee /etc/xbps.d/10-hyleos-repo.conf
  doas xbps-install -Suy
EOF
    exit 0
fi

if [ -n "$DEST" ]; then
    mkdir -p "$DEST"
    rsync -a --delete --info=progress2 "$REPO_DIR"/ "$DEST"/
    echo "[OK] Pubblicato in $DEST"
fi
if [ -n "$REMOTE" ]; then
    # XBPS rifiuta i repository remoti non firmati: pubblicarli sarebbe inutile
    # (i client non li userebbero). I repo locali possono restare non firmati.
    . "$ROOT_DIR/build/lib-repo.sh"
    _repo_sig="$(repo_signature_state "$REPO_DIR")"
    if [ "$_repo_sig" != "signed" ]; then
        echo "PUBBLICAZIONE BLOCCATA: il repository non è firmato ($_repo_sig)." >&2
        echo "Serve l'indice firmato (xbps-rindex -s) e ogni pacchetto con .sig2." >&2
        echo "Ricostruiscilo con HYLEOS_SIGN=1 e una chiave in ~/.ssh/id_rsa," >&2
        echo "oppure pubblica in locale con HYLEOS_PUBLISH_DIR." >&2
        [ "${HYLEOS_FORCE_PUBLISH:-0}" = "1" ] || exit 1
    fi
    rsync -a --delete --info=progress2 "$REPO_DIR"/ "$REMOTE"/
    echo "[OK] Pubblicato in $REMOTE"
fi
