-- ==============================================================================
-- Hyle OS 1.0 - Default Hyprland Configuration (Lua)
-- Base: Void Linux (glibc, runit, elogind)
-- Desktop Shell: DankMaterialShell (Quickshell / QML)
--
-- Hyprland 0.57 ha rimosso il supporto a hyprland.conf: da questa versione
-- l'unico formato supportato e' hyprland.lua.
-- ==============================================================================

---------------------
---- MONITORS ----
---------------------

hl.monitor({
    output   = "",
    mode     = "preferred",
    position = "auto",
    scale    = 1,
})

---------------
---- INPUT ----
---------------

hl.config({
    input = {
        kb_layout    = "it",
        follow_mouse = 1,
        sensitivity  = 0,

        touchpad = {
            natural_scroll = true,
        },
    },
})

-----------------------
---- LOOK AND FEEL ----
-----------------------

hl.config({
    general = {
        gaps_in     = 6,
        gaps_out    = 14,
        border_size = 2,

        -- Mouse-first: ridimensionamento trascinando i bordi delle finestre
        -- flottanti, con un'area di presa piu' generosa del bordo visibile.
        resize_on_border        = true,
        extend_border_grab_area = 15,
        hover_icon_on_border    = true,

        col = {
            active_border   = { colors = { "rgba(c6b4ffee)", "rgba(8070c0ee)" }, angle = 45 },
            inactive_border = "rgba(3a334faa)",
        },

        layout = "dwindle",
    },

    xwayland = {
        enabled              = true,
        force_zero_scaling   = true,
        use_nearest_neighbor = true,
    },

    decoration = {
        rounding = 14,

        blur = {
            enabled           = true,
            size              = 8,
            passes            = 3,
            new_optimizations = true,
        },

        shadow = {
            enabled      = true,
            range        = 18,
            render_power = 3,
            color        = "rgba(09061155)",
        },
    },

    animations = {
        enabled = true,
    },

    dwindle = {
        preserve_split = true,
    },

    misc = {
        disable_hyprland_logo     = true,
        disable_splash_rendering  = true,
    },
})

-- Curve e animazioni
hl.curve("myBezier", { type = "bezier", points = { { 0.05, 0.9 }, { 0.1, 1.05 } } })

hl.animation({ leaf = "windows",    enabled = true, speed = 5, bezier = "myBezier" })
hl.animation({ leaf = "windowsOut", enabled = true, speed = 5, bezier = "default", style = "popin 80%" })
hl.animation({ leaf = "border",     enabled = true, speed = 8, bezier = "default" })
hl.animation({ leaf = "fade",       enabled = true, speed = 5, bezier = "default" })
hl.animation({ leaf = "workspaces", enabled = true, speed = 5, bezier = "default" })

-------------------------------
---- MODALITA' FINESTRE ----
-------------------------------

-- Preferenza persistente scritta da hyleos-window-mode, DMS e hyle-welcome:
--   "tiling"   = tiling automatico (default)
--   "floating" = finestre libere, spostabili e ridimensionabili col mouse
-- Se il file manca o e' illeggibile restiamo sul tiling: un errore qui non
-- deve mai impedire l'avvio della sessione.
local function hyleos_window_mode()
    local config_home = os.getenv("XDG_CONFIG_HOME")
    if not config_home or config_home == "" then
        config_home = (os.getenv("HOME") or "") .. "/.config"
    end

    local handle = io.open(config_home .. "/hyleos/window-mode.json", "r")
    if not handle then
        return "tiling"
    end

    local content = handle:read("*a")
    handle:close()

    if content:match('"mode"%s*:%s*"floating"') then
        return "floating"
    end
    return "tiling"
end

local hyleosWindowMode = hyleos_window_mode()

------------------------------
---- WINDOW RULES ----
------------------------------

hl.window_rule({
    name  = "hyleos-store",
    match = { class = "^(org\\.hyleos\\.store)$" },
    float = true,
    size  = { 900, 650 },
    center = true,
})

hl.window_rule({
    name  = "hyleos-installer",
    match = { class = "^(org\\.hyleos\\.installer)$" },
    float = true,
    size  = { 880, 620 },
    center = true,
})

hl.window_rule({
    name  = "hyleos-welcome",
    match = { class = "^(org\\.hyleos\\.welcome)$" },
    float = true,
    size  = { 820, 580 },
    center = true,
})

hl.window_rule({
    name  = "hyleos-calculator",
    match = { class = "^(org\\.gnome\\.Calculator|gnome-calculator|kcalc|qalculate-gtk)$" },
    float = true,
    center = true,
})

-- Selettori file e portali: devono sempre aprirsi sopra l'app, centrati.
hl.window_rule({
    name  = "hyleos-file-dialogs",
    match = { class = "^(xdg-desktop-portal-gtk|xdg-desktop-portal-gnome|xdg-desktop-portal-kde|org\\.gnome\\.Nautilus\\.FileChooser)$" },
    float = true,
    center = true,
})

hl.window_rule({
    name        = "hyleos-border",
    match       = { class = ".*" },
    border_size = 2,
})

-- Regola nominata per la modalita' "Finestre Libere". Deve essere una
-- variabile globale: hyleos-window-mode la cerca con
--   hyprctl eval 'HyleOSFloatingModeRule:set_enabled(true)'
-- per passare da tiling a floating senza riavviare la sessione.
HyleOSFloatingModeRule = hl.window_rule({
    name  = "hyleos-floating-mode",
    match = { class = ".*" },
    float = true,
    center = true,
})

HyleOSFloatingModeRule:set_enabled(hyleosWindowMode == "floating")

-------------------------------
---- ENVIRONMENT VARIABLES ----
-------------------------------

hl.env("XDG_CURRENT_DESKTOP", "Hyprland")
hl.env("XDG_SESSION_TYPE", "wayland")
hl.env("XDG_SESSION_DESKTOP", "Hyprland")
hl.env("GDK_BACKEND", "wayland,x11,*")
hl.env("QT_QPA_PLATFORM", "wayland;xcb")
hl.env("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
hl.env("CLUTTER_BACKEND", "wayland")
hl.env("TERMINAL", "kitty")
hl.env("XCURSOR_THEME", "Adwaita")
hl.env("XCURSOR_SIZE", "24")
hl.env("HYPRCURSOR_THEME", "Adwaita")
hl.env("HYPRCURSOR_SIZE", "24")
hl.env("GTK_CSD", "1")
hl.env("GDK_DECORATION_LAYOUT", "menu:minimize,maximize,close")
hl.env("GTK_THEME", "Adwaita:dark")

-------------------
---- AUTOSTART ----
-------------------

hl.on("hyprland.start", function()
    -- Audio, agent e servizi della sessione utente
    hl.exec_cmd("dbus-update-activation-environment --systemd --all 2>/dev/null || dbus-update-activation-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP")
    hl.exec_cmd("pipewire")
    hl.exec_cmd("wireplumber")
    hl.exec_cmd("pipewire-pulse")
    hl.exec_cmd("/usr/libexec/polkit-gnome-authentication-agent-1 2>/dev/null || true")
    hl.exec_cmd("xsettingsd 2>/dev/null || true")

    -- Wallpaper di fallback (DMS gestisce lo sfondo quando parte)
    hl.exec_cmd("swaybg -i /usr/share/backgrounds/hyleos/wallpaper.png -m fill 2>/dev/null || swaybg -c '#0c0a11'")

    -- Avvia la shell HyleOS; log in ~/.local/state/hyleos/dms-start.log
    hl.exec_cmd("/usr/bin/hyleos-dms-start")

    -- Welcome una tantum
    hl.exec_cmd("[ -f ~/.config/autostart/org.hyleos.welcome.desktop ] && /usr/bin/hyle-welcome")
end)

---------------------
---- KEYBINDINGS ----
---------------------

local mainMod = "SUPER"

-- Applicazioni
hl.bind(mainMod .. " + RETURN", hl.dsp.exec_cmd("kitty"))
hl.bind(mainMod .. " + E",      hl.dsp.exec_cmd("nautilus --new-window"))
hl.bind(mainMod .. " + B",      hl.dsp.exec_cmd("firefox"))
hl.bind(mainMod .. " + A",      hl.dsp.exec_cmd("python3 /usr/share/hyleos/appstore.py"))
hl.bind(mainMod .. " + I",      hl.dsp.exec_cmd("/usr/bin/hyleos-installer"))
hl.bind(mainMod .. " + W",      hl.dsp.exec_cmd("/usr/bin/hyle-welcome"))

-- Shell DMS
hl.bind(mainMod .. " + SPACE",  hl.dsp.exec_cmd("dms ipc call spotlight toggle"))
hl.bind(mainMod .. " + N",      hl.dsp.exec_cmd("dms ipc call notifications toggle"))
hl.bind(mainMod .. " + C",      hl.dsp.exec_cmd("dms ipc call control-center toggle"))
hl.bind(mainMod .. " + L",      hl.dsp.exec_cmd("dms ipc call lock lock"))
hl.bind(mainMod .. " + ESCAPE", hl.dsp.exec_cmd("dms ipc call powermenu toggle"))

-- Finestre
hl.bind(mainMod .. " + Q", hl.dsp.window.close())
hl.bind(mainMod .. " + V", hl.dsp.window.float({ action = "toggle" }))
hl.bind(mainMod .. " + F", hl.dsp.window.fullscreen({ action = "toggle", mode = "fullscreen" }))

-- Coperchio portatile
hl.bind("switch:on:Lid Switch",  hl.dsp.exec_cmd("dms ipc call lock lock && hyprctl dispatch dpms off"), { locked = true })
hl.bind("switch:off:Lid Switch", hl.dsp.exec_cmd("hyprctl dispatch dpms on"), { locked = true })

-- Focus
hl.bind(mainMod .. " + left",  hl.dsp.focus({ direction = "left" }))
hl.bind(mainMod .. " + right", hl.dsp.focus({ direction = "right" }))
hl.bind(mainMod .. " + up",    hl.dsp.focus({ direction = "up" }))
hl.bind(mainMod .. " + down",  hl.dsp.focus({ direction = "down" }))
hl.bind(mainMod .. " + h",     hl.dsp.focus({ direction = "left" }))
hl.bind(mainMod .. " + l",     hl.dsp.focus({ direction = "right" }))
hl.bind(mainMod .. " + k",     hl.dsp.focus({ direction = "up" }))
hl.bind(mainMod .. " + j",     hl.dsp.focus({ direction = "down" }))

-- Workspace
for i = 1, 9 do
    hl.bind(mainMod .. " + " .. i, hl.dsp.focus({ workspace = i }))
    hl.bind(mainMod .. " + SHIFT + " .. i, hl.dsp.window.move({ workspace = i }))
end

-- Mouse
hl.bind(mainMod .. " + mouse:272", hl.dsp.window.drag(),   { mouse = true })
hl.bind(mainMod .. " + mouse:273", hl.dsp.window.resize(), { mouse = true })

-- Multimedia (PipeWire / WirePlumber)
hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("wpctl set-volume -l 1.5 @DEFAULT_AUDIO_SINK@ 5%+"), { locked = true, repeating = true })
hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-"),        { locked = true, repeating = true })
hl.bind("XF86AudioMute",        hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"),       { locked = true })
hl.bind("XF86AudioMicMute",     hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"),     { locked = true })

-- Luminosita'
hl.bind("XF86MonBrightnessUp",   hl.dsp.exec_cmd("brightnessctl set 5%+"), { locked = true, repeating = true })
hl.bind("XF86MonBrightnessDown", hl.dsp.exec_cmd("brightnessctl set 5%-"), { locked = true, repeating = true })

-- Screenshot
hl.bind("Print",       hl.dsp.exec_cmd("grim - | wl-copy"))
hl.bind("SHIFT + Print", hl.dsp.exec_cmd("grim -g \"$(slurp)\" - | wl-copy"))
hl.bind(mainMod .. " + SHIFT + S", hl.dsp.exec_cmd("grim -g \"$(slurp)\" - | wl-copy"))
