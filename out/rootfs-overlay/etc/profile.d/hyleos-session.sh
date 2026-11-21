if [ -z "${WAYLAND_DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ] && [ -x /usr/bin/hyleos-session ]; then
    /usr/bin/hyleos-session
fi
