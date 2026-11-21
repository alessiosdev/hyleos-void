pragma Singleton
pragma ComponentBehavior: Bound

import QtQuick
import Quickshell

// Stato del menu contestuale del desktop. Il menu vero e proprio vive in
// Modules/DesktopContextMenu.qml come surface overlay per schermo.
Singleton {
    id: root

    property bool menuOpen: false
    property var targetScreen: null
    property real menuX: 0
    property real menuY: 0

    function open(screen, x, y) {
        targetScreen = screen;
        menuX = Math.round(x);
        menuY = Math.round(y);
        menuOpen = true;
    }

    function close() {
        menuOpen = false;
        targetScreen = null;
    }

    function toggle(screen, x, y) {
        if (menuOpen && targetScreen === screen)
            close();
        else
            open(screen, x, y);
    }
}
