pragma Singleton
pragma ComponentBehavior: Bound

import QtCore
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Services

// Preferenze HyleOS condivise tra shell e strumenti esterni.
//
// La modalita' finestre vive in ~/.config/hyleos/window-mode.json ed e'
// scritta da /usr/bin/hyleos-window-mode: lo stesso file viene letto da
// hyprland.lua all'avvio. Qui lo osserviamo per riflettere subito i cambi
// fatti da DMS, da hyle-welcome o da terminale.
Singleton {
    id: root
    readonly property var log: Log.scoped("HyleOSService")

    readonly property string configDir: Paths.strip(StandardPaths.writableLocation(StandardPaths.ConfigLocation)) + "/hyleos"
    readonly property string windowModePath: configDir + "/window-mode.json"
    readonly property string windowModeHelper: "/usr/bin/hyleos-window-mode"

    readonly property string modeTiling: "tiling"
    readonly property string modeFloating: "floating"

    property string windowMode: modeTiling
    readonly property bool floatingMode: windowMode === modeFloating

    // Non usare `windowModeChanged`: la property `windowMode` genera gia'
    // un segnale con lo stesso nome e QML rifiuta la dichiarazione duplicata.
    signal modeChanged(string mode)

    function parseWindowMode(text) {
        try {
            const data = JSON.parse(text || "{}");
            return data?.mode === modeFloating ? modeFloating : modeTiling;
        } catch (e) {
            // File corrotto o assente: il default sicuro e' il tiling.
            return modeTiling;
        }
    }

    function setWindowMode(mode) {
        if (mode !== modeTiling && mode !== modeFloating)
            return;

        if (windowMode !== mode) {
            windowMode = mode;
            modeChanged(mode);
        }

        Proc.runCommand("hyleos-window-mode-set", [windowModeHelper, "set", mode], (output, exitCode) => {
            if (exitCode !== 0)
                root.log.warn("Impossibile applicare la modalita' finestre:", output);
            windowModeFile.reload();
        });
    }

    function toggleWindowMode() {
        setWindowMode(floatingMode ? modeTiling : modeFloating);
    }

    function reload() {
        windowModeFile.reload();
    }

    Component.onCompleted: reload()

    FileView {
        id: windowModeFile

        path: root.windowModePath
        blockLoading: true
        blockWrites: true
        atomicWrites: true
        watchChanges: true

        onLoaded: {
            const mode = root.parseWindowMode(windowModeFile.text());
            if (mode !== root.windowMode) {
                root.windowMode = mode;
                root.modeChanged(mode);
            }
        }

        onFileChanged: windowModeFile.reload()

        onLoadFailed: error => {
            // File non ancora creato: resta la modalita' tiling.
            root.log.debug("window-mode.json non disponibile, uso tiling");
        }
    }
}
