import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Common
import qs.Services
import qs.Widgets

// Menu contestuale del desktop (clic destro sullo sfondo). Una surface
// overlay per schermo, visibile solo quando DesktopMenuService.menuOpen.
Variants {
    id: menuVariants
    model: Quickshell.screens

    readonly property var menuItems: [
        {
            "label": I18n.tr("File Manager"),
            "icon": "folder",
            "action": "fileManager"
        },
        {
            "label": I18n.tr("Terminale"),
            "icon": "terminal",
            "action": "terminal"
        },
        {
            "label": I18n.tr("Hyle Store"),
            "icon": "store",
            "action": "store"
        },
        {
            "separator": true
        },
        {
            "label": I18n.tr("Cambia sfondo"),
            "icon": "wallpaper",
            "action": "wallpaper"
        },
        {
            "label": I18n.tr("Aspetto"),
            "icon": "palette",
            "action": "appearance"
        },
        {
            "label": I18n.tr("Impostazioni di sistema"),
            "icon": "settings",
            "action": "settings"
        },
        {
            "separator": true
        },
        {
            "header": I18n.tr("Sessione")
        },
        {
            "label": I18n.tr("Blocca schermo"),
            "icon": "lock",
            "action": "lock"
        },
        {
            "label": I18n.tr("Riavvia"),
            "icon": "restart_alt",
            "action": "reboot"
        },
        {
            "label": I18n.tr("Spegni"),
            "icon": "power_settings_new",
            "action": "poweroff"
        }
    ]

    function runAction(action) {
        DesktopMenuService.close();

        switch (action) {
        case "fileManager":
            Quickshell.execDetached(["nautilus", "--new-window"]);
            break;
        case "terminal":
            Quickshell.execDetached(["kitty"]);
            break;
        case "store":
            Quickshell.execDetached(["python3", "/usr/share/hyleos/appstore.py"]);
            break;
        case "wallpaper":
            PopoutService.openSettingsWithTab("wallpaper");
            break;
        case "appearance":
            PopoutService.openSettingsWithTab("theme");
            break;
        case "settings":
            PopoutService.openSettings();
            break;
        case "lock":
            IdleService.lockRequested();
            break;
        case "reboot":
            SessionService.reboot();
            break;
        case "poweroff":
            SessionService.poweroff();
            break;
        }
    }

    PanelWindow {
        id: menuWindow
        required property var modelData

        readonly property bool menuActive: DesktopMenuService.menuOpen && DesktopMenuService.targetScreen?.name === modelData.name

        screen: modelData
        visible: menuActive
        color: "transparent"

        WlrLayershell.namespace: "hyleos:desktop-menu"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.exclusiveZone: -1
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand

        anchors {
            top: true
            left: true
            right: true
            bottom: true
        }

        // Cattura i clic fuori dal menu e lo chiude.
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onClicked: DesktopMenuService.close()
        }

        Rectangle {
            id: menuRect

            readonly property int margin: Theme.spacingS
            readonly property int menuWidth: 260

            x: Math.max(margin, Math.min(menuWindow.width - width - margin, DesktopMenuService.menuX))
            y: Math.max(margin, Math.min(menuWindow.height - height - margin, DesktopMenuService.menuY))
            width: menuWidth
            height: menuColumn.implicitHeight + Theme.spacingS * 2
            radius: Theme.cornerRadius
            color: Theme.withAlpha(Theme.surfaceContainerHigh, Theme.popupTransparency)
            border.width: BlurService.borderWidth
            border.color: BlurService.borderColor
            focus: true

            Keys.onEscapePressed: event => {
                DesktopMenuService.close();
                event.accepted = true;
            }

            Component.onCompleted: if (menuWindow.menuActive)
                forceActiveFocus()

            Connections {
                target: menuWindow
                function onMenuActiveChanged() {
                    if (menuWindow.menuActive)
                        menuRect.forceActiveFocus();
                }
            }

            Column {
                id: menuColumn
                anchors.fill: parent
                anchors.margins: Theme.spacingS
                spacing: 2

                Repeater {
                    model: menuVariants.menuItems

                    delegate: Item {
                        width: menuColumn.width
                        height: modelData.separator ? 9 : (modelData.header ? 26 : 34)
                        visible: true

                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width
                            height: 1
                            color: Theme.outlineMedium
                            visible: !!modelData.separator
                        }

                        StyledText {
                            anchors.left: parent.left
                            anchors.leftMargin: Theme.spacingS
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData.header || ""
                            font.pixelSize: Theme.fontSizeSmall
                            font.weight: Font.Medium
                            color: Theme.surfaceVariantText
                            visible: !!modelData.header
                        }

                        Rectangle {
                            id: itemBackground
                            anchors.fill: parent
                            radius: Math.max(0, Theme.cornerRadius - 2)
                            color: itemMouse.containsMouse ? Theme.withAlpha(Theme.primary, 0.15) : "transparent"
                            visible: !modelData.separator && !modelData.header

                            Row {
                                anchors.fill: parent
                                anchors.leftMargin: Theme.spacingS
                                anchors.rightMargin: Theme.spacingS
                                spacing: Theme.spacingS

                                DankIcon {
                                    anchors.verticalCenter: parent.verticalCenter
                                    name: modelData.icon || ""
                                    size: 18
                                    color: Theme.surfaceText
                                }

                                StyledText {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.label || ""
                                    font.pixelSize: Theme.fontSizeMedium
                                    color: Theme.surfaceText
                                }
                            }

                            MouseArea {
                                id: itemMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: menuVariants.runAction(modelData.action)
                            }
                        }
                    }
                }
            }
        }
    }
}
