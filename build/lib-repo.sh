#!/bin/sh
# lib-repo.sh — funzioni condivise per ispezionare un repository XBPS.
#
# Un repository remoto XBPS deve essere firmato, altrimenti i client lo
# rifiutano. <arch>-repodata è un archivio tar (compresso) con index.plist e
# index-meta.plist: la firma dell'indice è nei metadati (signature-by,
# signature-type, public-key), non in un file .sig separato. Ogni pacchetto ha
# invece un file .sig2 accanto.
#
# Controllare solo l'esistenza di *-repodata.sig* darebbe sempre "non
# firmato"; controllare solo i .sig2 non garantisce che l'indice sia firmato.

# repo_packages_signed <repodir>: true se ogni .xbps ha il suo .sig2.
repo_packages_signed() {
    _found=0
    for _pkg in "$1"/*.x86_64.xbps; do
        [ -e "$_pkg" ] || continue
        _found=1
        [ -f "$_pkg.sig2" ] || return 1
    done
    [ "$_found" -eq 1 ]
}

# repo_signature_state <repodir> -> "signed" | "unsigned" | "unknown"
#
# "unknown" solo quando l'indice esiste ma non è leggibile (zstd assente e
# formato compresso): il chiamante deve trattarlo come non firmato.
repo_signature_state() {
    _dir="$1"
    if [ ! -f "$_dir/x86_64-repodata" ]; then
        echo unsigned
        return 0
    fi

    _repo_sig=0
    # GNU tar riconosce da solo la compressione (zstd, xz, gzip, ...).
    if tar -xOf "$_dir/x86_64-repodata" index-meta.plist 2>/dev/null \
         | grep -q '<key>signature-by</key>'; then
        _repo_sig=1
    elif grep -aq '<key>signature-by</key>' "$_dir/x86_64-repodata" 2>/dev/null; then
        # Formato molto vecchio: plist non incapsulato nel tar.
        _repo_sig=1
    fi

    if [ "$_repo_sig" -eq 1 ] && repo_packages_signed "$_dir"; then
        echo signed
    else
        echo unsigned
    fi
}
