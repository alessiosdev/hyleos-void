# HyleOS: crea le cartelle utente standard al primo accesso (Documenti, Download, ecc.)
if [ -z "${XDG_CONFIG_HOME:-}" ]; then
    export XDG_CONFIG_HOME="$HOME/.config"
fi
if [ -n "${HOME:-}" ] && [ ! -e "$HOME/.config/user-dirs.dirs" ] && command -v xdg-user-dirs-update >/dev/null 2>&1; then
    xdg-user-dirs-update >/dev/null 2>&1 || true
fi
