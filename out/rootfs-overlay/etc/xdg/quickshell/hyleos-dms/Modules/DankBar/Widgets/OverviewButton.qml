import QtQuick
import qs.Common
import qs.Modules.Plugins
import qs.Services
import qs.Widgets

BasePill {
    id: root

    property var hyprlandOverviewLoader: null

    readonly property bool overviewOpen: CompositorService.isHyprland && !!hyprlandOverviewLoader?.item?.overviewOpen

    content: Component {
        Item {
            implicitWidth: icon.width
            implicitHeight: root.widgetThickness - root.horizontalPadding * 2

            DankIcon {
                id: icon
                anchors.centerIn: parent
                name: "grid_view"
                size: Theme.barIconSize(root.barThickness, undefined, root.barConfig?.maximizeWidgetIcons, root.barConfig?.iconScale)
                color: root.overviewOpen ? Theme.primary : Theme.widgetIconColor
            }
        }
    }

    onClicked: {
        if (CompositorService.isHyprland && hyprlandOverviewLoader?.item) {
            hyprlandOverviewLoader.item.overviewOpen = !hyprlandOverviewLoader.item.overviewOpen;
        } else if (CompositorService.isNiri) {
            NiriService.toggleOverview();
        }
    }
}
