#!/bin/bash
# repo-key.sh — esporta la chiave pubblica di un repository HyleOS firmato.
#
# La chiave pubblica è incorporata in x86_64-repodata da `xbps-rindex -s`,
# quindi non serve la chiave privata. Il file prodotto va in
# /var/db/xbps/keys/<fingerprint>.plist: così i sistemi (e la ISO) si fidano
# del repository senza dover importare la chiave al primo aggiornamento.
#
# Uso:
#   ./build/repo-key.sh [repodir]              stampa fingerprint e destinazione
#   ./build/repo-key.sh --out DIR [repodir]    scrive DIR/<fingerprint>.plist
#
# Variabili:
#   HYLEOS_REPO_DIR   repository (default out/hyleos-repo/x86_64)
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="${HYLEOS_REPO_DIR:-$ROOT_DIR/out/hyleos-repo/x86_64}"
OUT_DIR=""

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)
            [ "$#" -ge 2 ] || { echo "ERRORE: --out richiede una directory" >&2; exit 2; }
            OUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Opzione sconosciuta: $1" >&2
            exit 2
            ;;
        *)
            REPO_DIR="$1"
            shift
            ;;
    esac
done

[ -f "$REPO_DIR/x86_64-repodata" ] || {
    echo "ERRORE: indice mancante in $REPO_DIR" >&2
    exit 1
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

python3 - "$REPO_DIR/x86_64-repodata" "$tmp/pub.pem" <<'PY'
import io
import plistlib
import subprocess
import sys
import tarfile

src, dst = sys.argv[1], sys.argv[2]


def read_members(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
        return {
            m.name: tf.extractfile(m).read()
            for m in tf.getmembers()
            if m.isfile()
        }


raw = open(src, "rb").read()
try:
    files = read_members(raw)
except tarfile.TarError:
    raw = subprocess.run(["zstd", "-dc", src], capture_output=True, check=True).stdout
    files = read_members(raw)

meta_raw = files.get("index-meta.plist")
if not meta_raw:
    sys.exit("ERRORE: repodata non firmato (manca index-meta.plist)")
meta = plistlib.loads(meta_raw)
key = meta.get("public-key")
if not key:
    sys.exit("ERRORE: repodata non firmato (manca public-key)")
open(dst, "wb").write(key)
PY

ssh-keygen -i -m PKCS8 -f "$tmp/pub.pem" > "$tmp/pub.ssh" 2>/dev/null || {
    echo "ERRORE: impossibile convertire la chiave pubblica (serve ssh-keygen)." >&2
    exit 1
}
fp="$(ssh-keygen -E md5 -lf "$tmp/pub.ssh" | awk '{print $2}' | sed 's/^MD5://')"
[ -n "$fp" ] || { echo "ERRORE: fingerprint non calcolato." >&2; exit 1; }

b64="$(base64 -w0 "$tmp/pub.pem")"
plist="$tmp/key.plist"
cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>public-key</key>
	<data>$b64</data>
</dict>
</plist>
EOF

if [ -n "$OUT_DIR" ]; then
    mkdir -p "$OUT_DIR"
    install -m 0644 "$plist" "$OUT_DIR/$fp.plist"
    echo "$OUT_DIR/$fp.plist"
else
    echo "fingerprint: $fp"
    echo "repo:        $REPO_DIR"
    echo "destinazione: /var/db/xbps/keys/$fp.plist"
fi
