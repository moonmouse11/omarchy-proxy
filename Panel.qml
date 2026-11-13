import QtQuick
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
    property bool confirmTun: false
    property string pendingId: ""

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

        Column {
            id: column
            width: popup.contentWidth - popup.padding * 2
            spacing: Style.space(10)

            Text {
                width: parent.width
                text: root.host && root.host.setupNeeded ? "Setup needed"
                      : root.host && root.host.connected ? root.host.serverName()
                      : root.host && root.host.failed ? "Proxy failed"
                      : "Proxy stopped"
                color: Color.foreground
                font.family: Style.resolvedFontFamily
                font.pixelSize: Style.font.heading
                elide: Text.ElideRight
            }

            Row {
                spacing: Style.space(8)
                Button {
                    text: root.host && root.host.connected ? "Disconnect" : "Connect"
                    bordered: true
                    fontFamily: Style.resolvedFontFamily
                    onClicked: {
                        if (root.busy !== "" || !root.host)
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
                    bordered: true
                    fontFamily: Style.resolvedFontFamily
                    onClicked: setupProc.running = true
                }
            }

            Text {
                visible: root.confirmTun
                width: parent.width
                wrapMode: Text.WordWrap
                color: Color.foreground
                font.family: Style.resolvedFontFamily
                font.pixelSize: Style.font.body
                text: "Tun captures all machine traffic. If the core crashes, clear internet stays blocked until disconnect."
            }
            Row {
                visible: root.confirmTun
                spacing: Style.space(8)
                Button {
                    text: "Capture traffic"
                    bordered: true
                    fontFamily: Style.resolvedFontFamily
                    onClicked: {
                        root.confirmTun = false
                        var id = root.pendingId
                        root.host.run(["--ack-tun"], function () {
                            root.host.tunAck = true
                            root.connectTo(id)
                        })
                    }
                }
                Button {
                    text: "Cancel"
                    bordered: true
                    fontFamily: Style.resolvedFontFamily
                    onClicked: root.confirmTun = false
                }
            }

            Text {
                visible: root.busy !== ""
                width: parent.width
                text: root.busy
                color: Color.muted
                font.family: Style.resolvedFontFamily
                font.pixelSize: Style.font.body
                wrapMode: Text.WordWrap
            }

            Row {
                width: parent.width
                spacing: Style.space(6)
                Repeater {
                    model: root.favorites()
                    delegate: Button {
                        required property var modelData
                        text: modelData.name || modelData.id
                        bordered: true
                        fontFamily: Style.resolvedFontFamily
                        onClicked: root.connectTo(String(modelData.id))
                    }
                }
            }

            Text {
                visible: root.host && root.host.connected
                width: parent.width
                color: Color.foreground
                font.family: Style.resolvedFontFamily
                font.pixelSize: Style.font.body
                wrapMode: Text.WordWrap
                text: root.summary()
            }

            Row {
                spacing: Style.space(8)
                Button {
                    text: "Refresh"
                    bordered: true
                    fontFamily: Style.resolvedFontFamily
                    onClicked: root.host.run(["--refresh"], function () { root.host.refresh() })
                }
                Button {
                    text: "Ping"
                    bordered: true
                    fontFamily: Style.resolvedFontFamily
                    onClicked: root.host.run(["--ping"], function () { root.host.refresh() })
                }
                Button {
                    text: "Doctor"
                    bordered: true
                    fontFamily: Style.resolvedFontFamily
                    onClicked: root.host.run(["--doctor"], function (payload) {
                        if (payload && payload.ok === false && payload.error)
                            root.busy = String(payload.error.message || payload.error.code || "")
                        else
                            root.busy = ""
                    })
                }
                Button {
                    text: "Settings"
                    bordered: true
                    fontFamily: Style.resolvedFontFamily
                    onClicked: root.host.openWindow()
                }
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
        if (root.host && root.host.mode === "tun" && !root.host.tunAck) {
            root.pendingId = id
            root.confirmTun = true
            return
        }
        root.busy = "Connecting"
        root.host.run(["--connect", id], function () {
            root.busy = ""
            root.host.refresh()
        })
    }
}
