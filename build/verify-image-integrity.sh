#!/bin/bash
set -e
set -u
set -o pipefail

log() { printf '[INTEGRITY] %s\n' "$*" >&2; }
die() { log "FAIL $*"; exit 1; }

[ $# -eq 3 ] || die "USO: $0 pre <rootfs> <manifest> | post <iso> <manifest>"

MODE="$1"
TARGET="$2"
MANIFEST="$3"
case "$MODE" in
    pre|post) ;;
    *) die "modo sconosciuto: $MODE" ;;
esac

[ -f "$MANIFEST" ] || [ "$MODE" = pre ] || die "manifest non trovato: $MANIFEST"
[ -d "$(dirname "$MANIFEST")" ] || die "directory del manifest non trovata: $(dirname "$MANIFEST")"

TMPDIR_WORK="$(mktemp -d /tmp/hyleos-image-integrity.XXXXXX)" || die "mktemp fallita"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

SENTINEL_PATHS=()
ICU_MEMBER=""
ICU_HASH_OK=0
VERIFIED_COUNT=0
SKIP_COUNT=0

build_sentinel_list() {
    local root="$1" f rel
    shopt -s nullglob
    for f in "$root"/usr/lib/libicuuc.so.*; do
        [ -f "$f" ] || continue
        SENTINEL_PATHS+=("${f#"$root"/}")
    done
    shopt -u nullglob
    [ "${#SENTINEL_PATHS[@]}" -gt 0 ] || die "nessuna sentinella usr/lib/libicuuc.so.* in $root"

    for rel in usr/bin/Hyprland usr/bin/dms usr/lib/libc.so.6; do
        [ -f "$root/$rel" ] || die "sentinella mancante: $rel"
        SENTINEL_PATHS+=("$rel")
    done
    if [ -f "$root/lib/libc.so.6" ]; then
        SENTINEL_PATHS+=("lib/libc.so.6")
    else
        log "SKIP lib/libc.so.6 assente (usrmerge)"
        SKIP_COUNT=$((SKIP_COUNT + 1))
    fi
}

relr_in_writable_segment() {
    local addr="$1" vaddr_hex filesz_hex vaddr filesz
    while read -r vaddr_hex filesz_hex; do
        [ -n "$vaddr_hex" ] || continue
        vaddr=$((16#${vaddr_hex#0x}))
        filesz=$((16#${filesz_hex#0x}))
        if [ "$addr" -ge "$vaddr" ] && [ "$addr" -lt "$((vaddr + filesz))" ]; then
            return 0
        fi
    done < "$RELR_WRITABLE_SEGS"
    return 1
}

check_relr() {
    local file="$1" dyn="$2" relr relrsz relrent relr_dec off="" line
    local p_off_hex p_vaddr_hex p_filesz_hex p_off p_vaddr p_filesz
    local sec_off sec_size raw w high low a base=0 last=-1 count=0 have_base=0 j bit any count_words
    local -a words=()
    relr="$(printf '%s\n' "$dyn" | awk '/\(RELR\)/ { v=$NF; sub(/^0x/, "", v); print v; exit }')"
    relrsz="$(printf '%s\n' "$dyn" | awk '/\(RELRSZ\)/ { for (i=NF; i>0; i--) if ($i ~ /^[0-9]+$/) { print $i; exit } }')"
    relrent="$(printf '%s\n' "$dyn" | awk '/\(RELRENT\)/ { for (i=NF; i>0; i--) if ($i ~ /^[0-9]+$/) { print $i; exit } }')"
    [ -n "$relr" ] && [[ "$relr" =~ ^[0-9a-fA-F]+$ ]] || die "DT_RELR assente o non valido su sentinella ELF: $file"
    [ -n "$relrsz" ] && [[ "$relrsz" =~ ^[0-9]+$ ]] || die "DT_RELRSZ assente o non valido su sentinella ELF: $file"
    [ -n "$relrent" ] || relrent=8
    [[ "$relrent" =~ ^[0-9]+$ ]] && [ "$relrent" -eq 8 ] || die "DT_RELRENT diverso da 8 su sentinella ELF: $file"
    [ "$relrsz" -gt 0 ] || die "DT_RELRSZ nullo su sentinella ELF: $file"
    [ $((relrsz % relrent)) -eq 0 ] || die "DT_RELRSZ=$relrsz non multiplo di DT_RELRENT=$relrent su $file"
    relr_dec=$((16#$relr))
    [ "$relr_dec" -gt 0 ] && [ $((relr_dec % 8)) -eq 0 ] || die "DT_RELR non allineato a 8 su sentinella ELF: $file"

    RELR_WRITABLE_SEGS="$TMPDIR_WORK/relr-writable-segs"
    readelf -lW "$file" 2>/dev/null | awk '$1 == "LOAD" && $7 ~ /W/ { print $3, $5 }' > "$RELR_WRITABLE_SEGS" \
        || die "readelf -lW fallito: $file"
    while read -r p_off_hex p_vaddr_hex p_filesz_hex; do
        [ -n "$p_off_hex" ] || continue
        p_off=$((16#${p_off_hex#0x}))
        p_vaddr=$((16#${p_vaddr_hex#0x}))
        p_filesz=$((16#${p_filesz_hex#0x}))
        if [ "$relr_dec" -ge "$p_vaddr" ] && [ "$relr_dec" -lt "$((p_vaddr + p_filesz))" ]; then
            off=$((p_off + relr_dec - p_vaddr))
            [ $((off + relrsz)) -le $((p_off + p_filesz)) ] || die "tabella RELR fuori dal segmento LOAD: $file"
            break
        fi
    done < <(readelf -lW "$file" 2>/dev/null | awk '$1 == "LOAD" { print $2, $3, $5 }')
    [ -n "$off" ] || die "DT_RELR non mappato in un segmento LOAD: $file"

    line="$(readelf -SW "$file" 2>/dev/null | awk '$2 == ".relr.dyn" { print $5, $6 }')"
    if [ -n "$line" ]; then
        read -r sec_off sec_size <<< "$line"
        [ $((16#${sec_off#0x})) -eq "$off" ] && [ $((16#${sec_size#0x})) -eq "$relrsz" ] \
            || die ".relr.dyn non coerente con DT_RELR/DT_RELRSZ su $file"
    fi

    raw="$(od -An -tx8 -v -j "$off" -N "$relrsz" -- "$file")" || die "lettura tabella RELR fallita: $file"
    words=($raw)
    count_words=$((relrsz / relrent))
    [ "${#words[@]}" -eq "$count_words" ] || die "tabella RELR troncata su $file"
    for w in "${words[@]}"; do
        [[ "$w" =~ ^[0-9a-fA-F]{16}$ ]] || die "voce RELR non decodificabile su $file: $w"
        high=$((16#${w:0:8}))
        low=$((16#${w:8:8}))
        if [ $((low & 1)) -eq 0 ]; then
            [ "$high" -lt 2147483648 ] || die "valore RELR spurio su $file: $w"
            a=$((high * 4294967296 + low))
            [ $((a % 8)) -eq 0 ] || die "indirizzo RELR non allineato a 8 su $file: $w"
            relr_in_writable_segment "$a" || die "indirizzo RELR fuori segmento scrivibile su $file: $w"
            [ "$a" -gt "$last" ] || die "indirizzi RELR non crescenti su $file: $w"
            base=$((a + 8))
            have_base=1
            last=$a
            count=$((count + 1))
        else
            [ "$have_base" -eq 1 ] || die "bitmap RELR senza indirizzo base su $file: $w"
            any=0
            for ((j = 0; j < 63; j++)); do
                if [ "$j" -lt 31 ]; then
                    bit=$(((low >> (j + 1)) & 1))
                else
                    bit=$(((high >> (j - 31)) & 1))
                fi
                if [ "$bit" -eq 1 ]; then
                    a=$((base + j * 8))
                    relr_in_writable_segment "$a" || die "indirizzo RELR fuori segmento scrivibile su $file: $w"
                    [ "$a" -gt "$last" ] || die "indirizzi RELR non crescenti su $file: $w"
                    last=$a
                    count=$((count + 1))
                    any=1
                fi
            done
            [ "$any" -eq 1 ] || die "bitmap RELR vuota su $file: $w"
            base=$((base + 504))
        fi
    done
    [ "$count" -gt 0 ] || die "nessuna rilocazione RELR decodificata su $file"
    log "OK ELF $file (DT_RELR, $count entries)"
}

check_elf_relocations() {
    local file="$1" dyn rel relacount
    if ! readelf -h "$file" >/dev/null 2>&1; then
        die "sentinella ELF attesa non valida: $file"
    fi
    dyn="$(readelf -d "$file" 2>/dev/null)" || die "readelf -d fallito: $file"
    relacount="$(printf '%s\n' "$dyn" | awk '/\(RELACOUNT\)/ { v=$NF; gsub(/[^0-9]/, "", v); print v; exit }')"
    if [ -z "$relacount" ] || ! [[ "$relacount" =~ ^[0-9]+$ ]] || [ "$relacount" -eq 0 ]; then
        check_relr "$file" "$dyn"
        return 0
    fi
    rel="$(readelf -Wr "$file" 2>/dev/null)" || die "readelf -Wr fallito: $file"
    printf '%s\n' "$rel" | awk -v want="$relacount" -v file="$file" '
        BEGIN { inside = 0; total = 0; leading = 0; bad = -1 }
        /^Relocation section/ {
            inside = (index($0, ".rela.dyn") > 0)
            next
        }
        inside && /^[[:space:]]*Offset[[:space:]]+Info/ { next }
        inside && $1 ~ /^[[:xdigit:]]+$/ && $2 ~ /^[[:xdigit:]]+$/ {
            total++
            if (total <= want && $3 != "R_X86_64_RELATIVE" && bad < 0) bad = total
            if ($3 == "R_X86_64_RELATIVE" && leading == total - 1) leading++
            next
        }
        END {
            if (bad > 0) {
                printf "[INTEGRITY] FAIL %s: rilocazione non-RELATIVE in posizione %d (DT_RELACOUNT=%d)\n", file, bad, want > "/dev/stderr"
                exit 1
            }
            if (total == 0) {
                printf "[INTEGRITY] FAIL %s: .rela.dyn assente o vuota con DT_RELACOUNT=%d\n", file, want > "/dev/stderr"
                exit 1
            }
            if (total < want) {
                printf "[INTEGRITY] FAIL %s: .rela.dyn ha %d voci ma DT_RELACOUNT=%d\n", file, total, want > "/dev/stderr"
                exit 1
            }
            if (leading != want) {
                printf "[INTEGRITY] FAIL %s: DT_RELACOUNT=%d ma %d R_X86_64_RELATIVE consecutivi all inizio\n", file, want, leading > "/dev/stderr"
                exit 1
            }
        }
    ' || return 1
    log "OK ELF $file (DT_RELACOUNT=$relacount)"
}

check_sentinel_elf() {
    local rel="$1" file="$2"
    if [ "$rel" = "usr/bin/dms" ]; then
        log "SKIP ELF: usr/bin/dms e' uno script dichiarato"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        return 0
    fi
    check_elf_relocations "$file"
    VERIFIED_COUNT=$((VERIFIED_COUNT + 1))
}

mode_pre() {
    local rootfs="${2%/}" rel rc
    VERIFIED_COUNT=0
    SKIP_COUNT=0
    [ -d "$rootfs" ] || die "rootfs non trovata: $rootfs"
    build_sentinel_list "$rootfs"

    local tmp_manifest="$TMPDIR_WORK/manifest"
    : > "$tmp_manifest"
    for rel in "${SENTINEL_PATHS[@]}"; do
        ( cd "$rootfs" && sha256sum -- "$rel" ) >> "$tmp_manifest" || die "sha256 fallito: $rel"
        check_sentinel_elf "$rel" "$rootfs/$rel"
    done
    cp "$tmp_manifest" "$MANIFEST"
    log "RIEPILOGO pre: $VERIFIED_COUNT sentinelle verificate, $SKIP_COUNT SKIPPATE"
    log "OK manifest scritto: $MANIFEST (${#SENTINEL_PATHS[@]} sentinelle)"

    if ! command -v xbps-pkgdb >/dev/null 2>&1; then
        log "SKIP xbps-pkgdb (non disponibile)"
        return 0
    fi
    if timeout 300 xbps-pkgdb -a -r "$rootfs" > "$TMPDIR_WORK/pkgdb.log" 2>&1; then
        log "OK xbps-pkgdb -a -r"
    else
        rc=$?
        log "SKIP xbps-pkgdb (exit $rc): $(head -n 1 "$TMPDIR_WORK/pkgdb.log" 2>/dev/null)"
    fi
}

extract_squashfs() {
    local iso="$1" out="$2" found=""
    if command -v xorriso >/dev/null 2>&1; then
        if xorriso -osirrox on -indev "$iso" -extract /LiveOS/squashfs.img "$out" \
            > "$TMPDIR_WORK/xorriso.log" 2>&1 && [ -s "$out" ]; then
            return 0
        fi
        log "xorriso non ha estratto squashfs.img, tentativo con 7z"
    fi
    command -v 7z >/dev/null 2>&1 || die "ne' xorriso ne' 7z disponibili per estrarre squashfs.img"
    local out7="$TMPDIR_WORK/iso7z"
    mkdir -p "$out7"
    7z e -y -o"$out7" "$iso" 'LiveOS/squashfs.img' > "$TMPDIR_WORK/7z.log" 2>&1 || die "estrazione 7z fallita"
    found="$(find "$out7" -type f -name squashfs.img -print -quit)"
    [ -n "$found" ] || die "squashfs.img non trovato nell'ISO"
    cp "$found" "$out"
}

icu_unverified() {
    if [ "${HYLEOS_ALLOW_UNVERIFIED_ICU:-0}" = "1" ]; then
        log "WARNING $1 (unverified: override HYLEOS_ALLOW_UNVERIFIED_ICU=1)"
        return 0
    fi
    die "$1"
}

verify_icu_from_xbps() {
    local dirs="${HYLEOS_XBPS_CACHE_DIRS:-}" d pkg found="" dec ok=0 member expected actual
    [ -n "$ICU_MEMBER" ] || { icu_unverified "nessuna sentinella ICU nel manifest"; return 0; }
    if [ -z "$dirs" ]; then
        icu_unverified "hash ICU obbligatorio non verificabile: nessuna directory cache/repo configurata"
        return 0
    fi
    local -a dirs_arr=()
    IFS=: read -r -a dirs_arr <<< "$dirs"
    for d in "${dirs_arr[@]}"; do
        [ -d "$d" ] || continue
        for pkg in "$d"/libicu78-*.x86_64.xbps "$d"/libicu78-*.xbps "$d"/libicu*-*.x86_64.xbps; do
            [ -f "$pkg" ] || continue
            found="$pkg"
            break 2
        done
    done
    if [ -z "$found" ]; then
        icu_unverified "hash ICU obbligatorio non verificabile: pacchetto libicu non trovato in cache/repo"
        return 0
    fi

    local tarfile="$TMPDIR_WORK/icu.tar"
    for dec in zstd gzip xz; do
        command -v "$dec" >/dev/null 2>&1 || continue
        if "$dec" -dc "$found" > "$tarfile" 2>/dev/null && tar -tf "$tarfile" >/dev/null 2>&1; then
            ok=1
            break
        fi
    done
    if [ "$ok" -ne 1 ]; then
        icu_unverified "hash ICU obbligatorio non verificabile: decompressione fallita ($found)"
        return 0
    fi
    member="$(tar -tf "$tarfile" 2>/dev/null | awk -v want="$ICU_MEMBER" '{ n=$0; sub(/^\.\//, "", n); if (n == want) { print $0; exit } }')"
    if [ -z "$member" ]; then
        icu_unverified "hash ICU obbligatorio non verificabile: membro $ICU_MEMBER assente in $found"
        return 0
    fi
    expected="$(tar -xOf "$tarfile" "$member" 2>/dev/null | sha256sum | awk '{print $1}')"
    actual="$(sha256sum "$EXTRACT_DIR/$ICU_MEMBER" 2>/dev/null | awk '{print $1}')"
    if [ -z "$expected" ] || [ -z "$actual" ]; then
        icu_unverified "hash ICU obbligatorio non verificabile: hash non calcolabile"
        return 0
    fi
    [ "$expected" = "$actual" ] || die "hash ICU da .xbps ($expected) diverso da quello nella ISO ($actual)"
    ICU_HASH_OK=1
    log "OK hash ICU derivato da $found ($ICU_MEMBER)"
}

load_test() {
    local loader="" l libpath=""
    for l in lib/ld-linux-x86-64.so.2 lib64/ld-linux-x86-64.so.2 usr/lib/ld-linux-x86-64.so.2; do
        if [ -f "$EXTRACT_DIR/$l" ]; then
            loader="$l"
            break
        fi
    done
    if [ -z "$loader" ] || [ ! -f "$EXTRACT_DIR/usr/bin/Hyprland" ]; then
        log "SKIP test caricamento (loader o Hyprland non estratti)"
        return 0
    fi
    libpath="$EXTRACT_DIR/lib:$EXTRACT_DIR/usr/lib:$EXTRACT_DIR/lib64"
    if LD_LIBRARY_PATH="$libpath" timeout 20 "$EXTRACT_DIR/$loader" \
        --library-path "$libpath" "$EXTRACT_DIR/usr/bin/Hyprland" --version \
        > /dev/null 2>&1; then
        log "OK test caricamento Hyprland --version con loader estratto"
    else
        log "SKIP test caricamento Hyprland --version (dipendenze incomplete nell'estrazione parziale)"
    fi
}

mode_post() {
    local iso="$2" sqfs="$TMPDIR_WORK/squashfs.img" rel
    local expected_iso="${HYLEOS_EXPECTED_ISO:-}" expected_sha="${HYLEOS_EXPECTED_ISO_SHA256:-}" actual_sha
    VERIFIED_COUNT=0
    SKIP_COUNT=0
    ICU_HASH_OK=0
    ICU_MEMBER=""
    [ -s "$iso" ] || die "ISO non trovata o vuota: $iso"
    if [ -n "$expected_iso" ]; then
        [ "$iso" = "$expected_iso" ] || die "ISO $iso non e' quella generata in questa run ($expected_iso)"
    else
        log "WARNING HYLEOS_EXPECTED_ISO non fornito: impossibile legare l'ISO alla run corrente"
    fi
    if [ -n "$expected_sha" ]; then
        actual_sha="$(sha256sum "$iso" | awk '{print $1}')"
        [ "$actual_sha" = "$expected_sha" ] || die "sha256 ISO $actual_sha diverso da quello generato in questa run ($expected_sha)"
        log "OK ISO legata alla run corrente (sha256 $actual_sha)"
    fi
    [ -s "$MANIFEST" ] || die "manifest vuoto o non trovato: $MANIFEST"

    while read -r _hash rel; do
        case "$rel" in
            usr/lib/libicuuc.so.*)
                if [ ${#rel} -gt ${#ICU_MEMBER} ]; then ICU_MEMBER="$rel"; fi
                ;;
        esac
    done < "$MANIFEST"
    [ -n "$ICU_MEMBER" ] || die "nessuna sentinella ICU nel manifest"
    log "Sentinella ICU: $ICU_MEMBER"

    EXTRACT_DIR="$TMPDIR_WORK/extract"
    mkdir -p "$EXTRACT_DIR"
    extract_squashfs "$iso" "$sqfs"

    local -a extracted=()
    while read -r _hash rel; do
        [ -n "$rel" ] || continue
        mkdir -p "$EXTRACT_DIR/$(dirname "$rel")"
        unsquashfs -cat "$sqfs" "$rel" > "$EXTRACT_DIR/$rel" 2> "$TMPDIR_WORK/unsquashfs.log" \
            || die "estrazione sentinella fallita: $rel"
        extracted+=("$rel")
        check_sentinel_elf "$rel" "$EXTRACT_DIR/$rel"
    done < "$MANIFEST"

    if ! ( cd "$EXTRACT_DIR" && sha256sum -c -- "$MANIFEST" ) > "$TMPDIR_WORK/sha.log" 2>&1; then
        sed 's/^/[INTEGRITY]   /' "$TMPDIR_WORK/sha.log" >&2 || true
        die "confronto sha256 pre/post fallito"
    fi
    log "OK sha256 pre/post su ${#extracted[@]} sentinelle"

    verify_icu_from_xbps
    if [ "$ICU_HASH_OK" -ne 1 ] && [ "${HYLEOS_ALLOW_UNVERIFIED_ICU:-0}" != "1" ]; then
        die "sentinella ICU non verificata da .xbps"
    fi
    log "RIEPILOGO post: $VERIFIED_COUNT sentinelle verificate, $SKIP_COUNT SKIPPATE, ICU hash_ok=$ICU_HASH_OK"
    load_test
}

case "$MODE" in
    pre) mode_pre "$@" ;;
    post) mode_post "$@" ;;
esac
