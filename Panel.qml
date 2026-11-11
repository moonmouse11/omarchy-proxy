import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
    id: root
    moduleName: "omarchy-proxy"
    ipcTarget: "omarchy-proxy"
    manageIpc: false

    property var host: null
    property var anchorItem: null
    property string busy: ""

    KeyboardPanel {
        id: popup
        anchorItem: root.anchorItem
        owner: root.host || root
        bar: root.bar
        open: root.opened
        centerOnBar: false
        padding: Style.space(12)
        contentWidth: popup.fittedContentWidth(Style.space(360))
        contentHeight: popup.fittedContentHeight(column.implicitHeight)

        ColumnLayout {
            id: column
            width: popup.contentWidth - popup.padding * 2
            spacing: Style.space(10)

            Label {
                textFormat: Text.PlainText
                text: root.host && root.host.setupNeeded ? "Setup needed"
                      : root.host && root.host.connected ? root.host.serverName()
                      : root.host && root.host.failed ? "Proxy failed"
                      : "Proxy stopped"
                color: Color.foreground
                font.pixelSize: Style.font.heading
            }

            RowLayout {
                Button {
                    text: root.host && root.host.connected ? "Disconnect" : "Connect"
                    enabled: root.busy === ""
                    onClicked: {
                        if (!root.host)
                            return
                        if (root.host.connected) {
                            root.busy = "Stopping"
                            root.host.run(["--disconnect"], function () {
                                root.busy = ""
                                root.host.refresh()
                            })
                            return
                        }
                        var favorite = null
                        var rows = root.host.servers || []
                        for (var i = 0; i < rows.length; i++) {
                            if (rows[i].favorite) {
                                favorite = rows[i]
                                break
                            }
                        }
                        if (!favorite && rows.length)
                            favorite = rows[0]
                        if (!favorite)
                            return
                        root.connectTo(String(favorite.id))
                    }
                }
                Button {
                    visible: root.host && root.host.setupNeeded
                    text: "Finish setup"
                    onClicked: setupProc.running = true
                }
            }

            Label {
                visible: root.busy !== ""
                textFormat: Text.PlainText
                text: root.busy
                color: Color.muted
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Style.space(6)
                Repeater {
                    model: root.favorites()
                    delegate: Button {
                        required property var modelData
                        text: modelData.name || modelData.id
                        onClicked: root.connectTo(String(modelData.id))
                    }
                }
            }

            Label {
                visible: root.host && root.host.connected
                textFormat: Text.PlainText
                color: Color.foreground
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                text: root.summary()
            }

            RowLayout {
                Button { text: "Refresh"; onClicked: root.host.run(["--refresh"], function () { root.host.refresh() }) }
                Button { text: "Ping"; onClicked: root.host.run(["--ping"], function () { root.host.refresh() }) }
                Button {
                    text: "Doctor"
                    onClicked: root.host.run(["--doctor"], function (payload) {
                        if (payload && payload.ok === false && payload.error)
                            root.busy = String(payload.error.message || payload.error.code || "")
                        else
                            root.busy = ""
                    })
                }
                Button { text: "Settings"; onClicked: root.host.openWindow() }
            }
        }
    }

    Process {
        id: setupProc
        command: ["omarchy-launch-floating-terminal-with-presentation", "printf '%s\n' 'yay -S xray-bin' 'pacman -S sing-box'"]
    }

    function favorites() {
        var rows = root.host && root.host.servers ? root.host.servers : []
        var picked = []
        for (var i = 0; i < rows.length && picked.length < 5; i++) {
            if (rows[i].favorite)
                picked.push(rows[i])
        }
        return picked
    }

    function summary() {
        var status = root.host ? root.host.status : ({})
        return [
            status.exit_ip || "",
            status.country || "",
            status.backend || "",
            status.health || ""
        ].filter(function (part) { return part !== "" }).join(" · ")
    }

    function connectTo(id) {
        root.busy = "Connecting"
        root.host.run(["--connect", id], function () {
            root.busy = ""
            root.host.refresh()
        })
    }
}
