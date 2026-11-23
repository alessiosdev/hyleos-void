#!/bin/bash
# Controlli automatici pre-build per HyleOS.
#
# Verifica che l'overlay sia pulito e che il codice Python/shell/QML/JSON sia
# sintatticamente valido. Va eseguito PRIMA di mklive: un errore qui costa
# pochi secondi, un errore dentro la ISO costa ~35 minuti di build e un boot
# rotto.
#
# Uso:
#   ./build/check-overlay.sh            # controlli normali
#   ./build/check-overlay.sh --strict   # qmllint obbligatorio, errori = stop
#   ./build/check-overlay.sh --clean    # rimuove file sporchi e __pycache__
#
# Variabili:
#   HYLEOS_SKIP_QML=1       salta qmllint (solo se il tool non è disponibile)
#   HYLEOS_STRICT_CHECKS=1  equivale a --strict
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
OVERLAY="$ROOT_DIR/out/rootfs-overlay"

STRICT="${HYLEOS_STRICT_CHECKS:-0}"
CLEAN=0
for arg in "$@"; do
    case "$arg" in
        --strict) STRICT=1 ;;
        --clean)  CLEAN=1 ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Opzione sconosciuta: $arg" >&2
            exit 2
            ;;
    esac
done

errors=0
warnings=0

err()  { echo "  [ERRORE] $*" >&2; errors=$((errors + 1)); }
warn() { echo "  [AVVISO] $*" >&2; warnings=$((warnings + 1)); }
ok()   { echo "  [OK] $*"; }

section() { echo; echo "== $* =="; }

if [ ! -d "$OVERLAY" ]; then
    echo "ERRORE: overlay non trovato: $OVERLAY" >&2
    exit 1
fi

# Versioni precedenti di questo script usavano py_compile, che scriveva
# __pycache__ dentro l'overlay. Se una corsa veniva interrotta, i residui
# facevano fallire la corsa successiva con errori "file sporchi" del tutto
# illegittimi. Sono artefatti generati, non sorgenti: rimuovili prima di
# qualsiasi controllo. Gli altri file sporchi (.bak, .orig, .pyc sciolti)
# restano invece errori veri e vengono segnalati qui sotto.
find "$OVERLAY" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------------------
section "File sporchi nell'overlay"

DIRTY_PATTERNS=(
    -name '*.bak' -o -name '*.bak.*' -o -name '*.orig' -o -name '*.rej'
    -o -name '*.newbackup' -o -name '*.backup.*' -o -name '*.pyc'
    -o -name '*~' -o -name '.DS_Store'
)
mapfile -t dirty < <(find "$OVERLAY" \( "${DIRTY_PATTERNS[@]}" \) -print | sort)
mapfile -t pycache < <(find "$OVERLAY" -name '__pycache__' -type d -print | sort)

if [ "${#dirty[@]}" -eq 0 ] && [ "${#pycache[@]}" -eq 0 ]; then
    ok "nessun file di backup, temporaneo o cache Python"
else
    for f in "${dirty[@]}"; do err "file sporco: ${f#$ROOT_DIR/}"; done
    for d in "${pycache[@]}"; do err "cache Python: ${d#$ROOT_DIR/}"; done
    if [ "$CLEAN" -eq 1 ]; then
        find "$OVERLAY" \( "${DIRTY_PATTERNS[@]}" \) -delete
        find "$OVERLAY" -name '__pycache__' -type d -prune -exec rm -rf {} +
        echo "  [*] Pulizia eseguita (--clean)."
        errors=0
    fi
fi

# ---------------------------------------------------------------------------
section "Directory morte / scaffolding"

DEAD_DIRS=(
    "out/rootfs-overlay/usr/share/hyleos/installer/ui"
    "out/rootfs-overlay/usr/share/hyleos/installer/utils"
    "out/rootfs-overlay/usr/share/hyleos/installer/tests"
    "out/rootfs-overlay/usr/share/hyleos/hyle-installer"
    "hyle-installer"
    "installer"
)
for d in "${DEAD_DIRS[@]}"; do
    if [ -e "$ROOT_DIR/$d" ]; then
        err "directory morta presente: $d"
    fi
done
[ "$errors" -eq 0 ] && ok "nessuna directory morta"

# ---------------------------------------------------------------------------
section "File obbligatori"

REQUIRED=(
    "etc/skel/.config/hypr/hyprland.lua"
    "etc/skel/.config/hyleos/window-mode.json"
    "usr/bin/hyleos-session"
    "usr/bin/hyleos-dms-start"
    "usr/bin/hyleos-config-migrate"
    "usr/bin/hyleos-installer"
    "usr/bin/hyle"
    "usr/bin/hyleos-system-update"
    "usr/bin/hyleos-appimage-update"
    "usr/bin/hyleos-user-update"
    "usr/bin/hyleos-window-mode"
    "usr/libexec/hyleos/kernel-cleanup"
    "usr/libexec/hyleos/hyleos-store-xbps"
    "usr/share/hyleos/installer.py"
    "usr/share/hyleos/appstore.py"
    "usr/share/hyleos/xbps_backend.py"
    "usr/share/hyleos/appimage_backend.py"
    "usr/share/hyleos/appimage-catalog.json"
    "usr/share/hyleos/appimage-catalog.example.json"
    "usr/share/hyleos/installer/core/disk_manager.py"
    "usr/share/hyleos/installer/core/validation.py"
    "usr/share/hyleos/installer/preflight.py"
    "usr/share/hyleos/installer/disks.py"
    "usr/share/hyleos/installer/install_worker.py"
    "usr/share/hyleos/installer/ui.py"
    "usr/share/polkit-1/actions/org.hyleos.installer.policy"
    "usr/share/polkit-1/actions/org.hyleos.store.xbps.policy"
    "usr/share/polkit-1/actions/org.hyleos.update.system.policy"
    "usr/share/polkit-1/rules.d/50-hyleos-installer-live.rules"
    "etc/xdg/quickshell/hyleos-dms/shell.qml"
    "etc/xdg/quickshell/hyleos-dms/Common/SettingsData.qml"
)
for f in "${REQUIRED[@]}"; do
    [ -e "$OVERLAY/$f" ] || err "manca: $f"
done

for f in usr/bin/hyleos-session usr/bin/hyleos-dms-start usr/bin/hyleos-config-migrate usr/bin/hyleos-installer usr/bin/hyle usr/bin/hyleos-system-update usr/bin/hyleos-appimage-update usr/bin/hyleos-user-update usr/bin/hyleos-window-mode usr/libexec/hyleos/kernel-cleanup usr/libexec/hyleos/hyleos-store-xbps; do
    [ -x "$OVERLAY/$f" ] || err "non eseguibile: $f"
done
[ "$errors" -eq 0 ] && ok "file obbligatori presenti"

# ---------------------------------------------------------------------------
section "Regole Polkit"

POLKIT_BAD_METHODS='subject[[:space:]]*\.[[:space:]]*(user|active|local|isActive|isLocal|get[A-Za-z0-9_]*)[[:space:]]*\('
LIVE_RULE="$OVERLAY/usr/share/polkit-1/rules.d/50-hyleos-installer-live.rules"
mapfile -t rules < <(find "$OVERLAY" -name '*.rules' -type f | sort)
for f in "${rules[@]}"; do
    if bad=$(grep -nE "$POLKIT_BAD_METHODS" "$f"); then
        err "metodo Polkit inesistente in: ${f#$ROOT_DIR/}"
        echo "$bad" | sed 's/^/      /' >&2
    fi
done
if grep -qE "$POLKIT_BAD_METHODS" "$LIVE_RULE"; then
    err "regola live contiene un metodo Polkit inesistente"
fi
if grep -qF 'subject.user == "anon"' "$LIVE_RULE"; then
    ok "regola live usa subject.user come proprieta'"
else
    err 'regola live senza subject.user == "anon"'
fi
[ "$errors" -eq 0 ] && ok "${#rules[@]} file .rules Polkit verificati"

# ---------------------------------------------------------------------------
section "Own overlay (A10)"

MKISO="$ROOT_DIR/build/mkiso.sh"
if [ -f "$MKISO" ]; then
    mkiso_code=$(grep -vE '^[[:space:]]*#' "$MKISO")
    if printf '%s\n' "$mkiso_code" | grep -qE '[$]PRIV_CMD[[:space:]]+chown[[:space:]]+-R[[:space:]]+0:0[[:space:]]+"?[$]TEMP_OVERLAY"?'; then
        ok 'mkiso.sh: normalizzazione $PRIV_CMD chown -R 0:0 presente'
    else
        err 'mkiso.sh: manca $PRIV_CMD chown -R 0:0 "$TEMP_OVERLAY" (A10, ADR-0026/0027)'
    fi
    if printf '%s\n' "$mkiso_code" | grep -qE 'for[[:space:]]+_sys_dir[[:space:]]+in[[:space:]]+home[[:space:]]+etc[[:space:]]+usr[[:space:]]+etc/runit/runsvdir[[:space:]]+var/service[[:space:]]*;[[:space:]]*do'; then
        ok "mkiso.sh: loop directory di sistema presente"
    else
        err "mkiso.sh: manca il loop home etc usr etc/runit/runsvdir var/service (A10, ADR-0026/0027)"
    fi
    if printf '%s\n' "$mkiso_code" | grep -qE '[$]_sys_owner"[[:space:]]*!=[[:space:]]*"0:0"'; then
        ok "mkiso.sh: verifica _sys_owner != 0:0 presente"
    else
        err 'mkiso.sh: manca la verifica _sys_owner != "0:0" (A10, ADR-0026/0027)'
    fi
    if printf '%s\n' "$mkiso_code" | grep -qE 'find[[:space:]]+"?[$]TEMP_OVERLAY"?.*![[:space:]]*-uid[[:space:]]+0.*![[:space:]]*-gid[[:space:]]+0'; then
        ok "mkiso.sh: controllo find ! -uid 0 -o ! -gid 0 presente"
    else
        err "mkiso.sh: manca il controllo find ! -uid 0 -o ! -gid 0 (A10, ADR-0026/0027)"
    fi
else
    err "mkiso.sh non trovato: $MKISO (A10, ADR-0026/0027)"
fi

# ---------------------------------------------------------------------------
section "Sintassi Python"

mapfile -t py_files < <(find "$OVERLAY" -name '*.py' -type f | sort)

# Controllo di sintassi senza effetti collaterali: py_compile scriveva
# __pycache__ nell'overlay, rendendo il pre-volo dipendente da corse precedenti.
check_py_syntax() {
    python3 - "$1" >/dev/null 2>/tmp/hyleos-pycompile.err <<'PY'
import sys
import tokenize

with tokenize.open(sys.argv[1]) as source:
    compile(source.read(), sys.argv[1], "exec")
PY
}

if [ "${#py_files[@]}" -eq 0 ]; then
    warn "nessun file Python trovato"
else
    for f in "${py_files[@]}"; do
        if ! check_py_syntax "$f"; then
            err "Python non valido: ${f#$ROOT_DIR/}"
            sed 's/^/      /' /tmp/hyleos-pycompile.err >&2
        fi
    done
    [ "$errors" -eq 0 ] && ok "${#py_files[@]} file Python compilati"
fi

# ---------------------------------------------------------------------------
section "Lint Python (ruff)"

if [ "${#py_files[@]}" -eq 0 ]; then
    :
elif command -v ruff >/dev/null 2>&1; then
    # Solo errori reali (sintassi, nomi non definiti, import rotti): lo stile
    # non deve bloccare una build.
    if ruff check --quiet --select E9,F63,F7,F82 "${py_files[@]}" 2>/tmp/hyleos-ruff.err; then
        ok "ruff: nessun errore bloccante"
    else
        err "ruff ha trovato errori bloccanti"
        sed 's/^/      /' /tmp/hyleos-ruff.err >&2
    fi
else
    warn "ruff non installato: lint Python saltato (opzionale)"
fi

# ---------------------------------------------------------------------------
section "Sintassi shell"

mapfile -t sh_files < <(
    find "$OVERLAY" -type f \( -name '*.sh' -o -path '*/bin/*' -o -path '*/sbin/*' \) \
        -exec sh -c 'head -c 64 "$1" | grep -qE "^#!.*(sh|bash)"' _ {} \; -print | sort
)
for f in "${sh_files[@]}"; do
    if head -1 "$f" | grep -q 'bash'; then
        bash -n "$f" 2>/tmp/hyleos-sh.err || { err "bash non valido: ${f#$ROOT_DIR/}"; sed 's/^/      /' /tmp/hyleos-sh.err >&2; }
    else
        sh -n "$f" 2>/tmp/hyleos-sh.err || { err "sh non valido: ${f#$ROOT_DIR/}"; sed 's/^/      /' /tmp/hyleos-sh.err >&2; }
    fi
done
[ "$errors" -eq 0 ] && ok "${#sh_files[@]} script shell verificati"

# ---------------------------------------------------------------------------
section "JSON"

mapfile -t json_files < <(
    find "$OVERLAY" -name '*.json' -type f \
        -not -path '*/matugen/*' | sort
)
for f in "${json_files[@]}"; do
    python3 -m json.tool "$f" >/dev/null 2>/tmp/hyleos-json.err \
        || { err "JSON non valido: ${f#$ROOT_DIR/}"; sed 's/^/      /' /tmp/hyleos-json.err >&2; }
done
[ "$errors" -eq 0 ] && ok "${#json_files[@]} file JSON validi"

# ---------------------------------------------------------------------------
section "QML (qmllint)"

QML_TARGETS_FILE="$SCRIPT_DIR/qml-lint-targets.txt"
if [ "${HYLEOS_SKIP_QML:-0}" = "1" ]; then
    warn "qmllint saltato (HYLEOS_SKIP_QML=1)"
elif ! command -v qmllint >/dev/null 2>&1; then
    if [ "$STRICT" = "1" ]; then
        err "qmllint non installato (pacchetto: qt6-declarative-tools)"
    else
        warn "qmllint non installato: controllo QML saltato (installa qt6-declarative-tools)"
    fi
elif [ ! -f "$QML_TARGETS_FILE" ]; then
    warn "lista target QML assente: $QML_TARGETS_FILE"
else
    qml_errors=0
    qml_count=0
    while IFS= read -r rel; do
        case "$rel" in ''|'#'*) continue ;; esac
        f="$OVERLAY/$rel"
        if [ ! -f "$f" ]; then
            err "target QML inesistente: $rel"
            continue
        fi
        qml_count=$((qml_count + 1))
        # qmllint emette warning per gli import qs.* non risolvibili fuori da
        # quickshell; gli errori di sintassi però sono fatali (exit != 0).
        if ! qmllint "$f" >/tmp/hyleos-qmllint.out 2>&1; then
            err "QML non valido: $rel"
            sed 's/^/      /' /tmp/hyleos-qmllint.out >&2
            qml_errors=$((qml_errors + 1))
        fi
    done < "$QML_TARGETS_FILE"
    if [ "$qml_errors" -eq 0 ]; then
        ok "$qml_count file QML verificati"
    fi
fi

# ---------------------------------------------------------------------------
echo
if [ "$errors" -gt 0 ]; then
    echo "CONTROLLI FALLITI: $errors errori, $warnings avvisi." >&2
    echo "La build è bloccata. Correggi gli errori sopra e riprova." >&2
    exit 1
fi

echo "CONTROLLI SUPERATI: 0 errori, $warnings avvisi."
exit 0
