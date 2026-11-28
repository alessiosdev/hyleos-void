# Sul sistema installato il grafico parte da greetd+ReGreet: l'installer crea
# /etc/greetd/enabled e questo hook diventa inerte, evitando il doppio avvio.
# Nella live il marker non esiste e l'autologin tty1 resta invariato.
if [ -f /etc/greetd/enabled ]; then
    return 0 2>/dev/null || exit 0
fi
# Guard di idempotenza: non avviare una seconda sessione grafica se Wayland o
# una sessione grafica sono gia' attivi (XDG_SESSION_TYPE puo' essere "tty").
if [ -n "${WAYLAND_DISPLAY:-}" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || [ "${XDG_SESSION_TYPE:-}" = "x11" ]; then
    return 0 2>/dev/null || exit 0
fi
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ] && [ -x /usr/bin/hyleos-session ]; then
    /usr/bin/hyleos-session
fi
