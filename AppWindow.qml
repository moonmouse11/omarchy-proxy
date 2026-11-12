import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Client.js" as Client

FloatingWindow {
    id: root
    title: "Proxy"
    color: Color.background
    readonly property string uiFont: Style.resolvedFontFamily
    implicitWidth: 760
    implicitHeight: 560
    minimumSize: Qt.size(640, 420)
    visible: false

    property var host: null
    property string filterText: ""
    property string sortKey: "name"
    property string importText: ""
    property bool confirmDelete: false
    property bool confirmTun: false
    property string pendingId: ""

    function show() {
        visible = false
        visible = true
        if (host)
            host.refresh()
    }

    function shownServers() {
        var rows = host && host.servers ? host.servers.slice() : []
        var needle = filterText.toLowerCase()
        if (needle !== "") {
            rows = rows.filter(function (row) {
                var name = String(row.name || "").toLowerCase()
                var scheme = Client.schemeOf(row.uri)
                return name.indexOf(needle) >= 0 || scheme.indexOf(needle) >= 0
            })
        }
        rows.sort(function (a, b) {
            if (sortKey === "ping")
                return Number(a.ping_ms || 1e9) - Number(b.ping_ms || 1e9)
            if (sortKey === "recent")
                return String(b.last_used_at || "").localeCompare(String(a.last_used_at || ""))
            return String(a.name || "").localeCompare(String(b.name || ""))
        })
        return rows
    }

    Item {
        anchors.fill: parent
        anchors.margins: Style.space(16)

        Row {
            id: filters
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            spacing: Style.space(8)

            TextField {
                id: filterField
                width: parent.width - sortBox.width - parent.spacing
                font.family: root.uiFont
                placeholderText: "Filter by name or protocol"
                text: root.filterText
                onTextChanged: root.filterText = text
            }
            Dropdown {
                id: sortBox
                showLabel: false
                fontFamily: root.uiFont
                width: Style.space(140)
                options: ["name", "ping", "recent"]
                value: root.sortKey
                onChanged: function (next) { root.sortKey = next }
            }
        }

        ListView {
            id: list
            anchors.top: filters.bottom
            anchors.topMargin: Style.space(12)
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: importer.top
            anchors.bottomMargin: Style.space(12)
            clip: true
            spacing: Style.space(4)
            model: root.shownServers()
            delegate: Row {
                required property var modelData
                width: list.width
                spacing: Style.space(8)
                height: Math.max(nameLabel.implicitHeight, connectButton.implicitHeight)

                Text {
                    id: nameLabel
                    width: parent.width - connectButton.width - favoriteButton.width - pingButton.width - deleteButton.width - parent.spacing * 4
                    text: (modelData.name || modelData.id) + "  " + Client.schemeOf(modelData.uri)
                    color: Color.foreground
                    font.family: root.uiFont
                    elide: Text.ElideRight
                    anchors.verticalCenter: parent.verticalCenter
                }
                Button {
                    id: connectButton
                    text: "Connect"
                    fontFamily: root.uiFont
                    bordered: true
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: root.connectTo(String(modelData.id))
                }
                Button {
                    id: favoriteButton
                    text: modelData.favorite ? "Unfavorite" : "Favorite"
                    fontFamily: root.uiFont
                    bordered: true
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: root.host.run(["--favorite", String(modelData.id)], function () { root.host.refresh() })
                }
                Button {
                    id: pingButton
                    text: "Ping"
                    fontFamily: root.uiFont
                    bordered: true
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: root.host.run(["--ping", String(modelData.id)], function () { root.host.refresh() })
                }
                Button {
                    id: deleteButton
                    text: "Delete"
                    fontFamily: root.uiFont
                    bordered: true
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: {
                        root.pendingId = String(modelData.id)
                        root.confirmDelete = true
                    }
                }
            }
        }

        Column {
            id: importer
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            spacing: Style.space(8)

            Row {
                width: parent.width
                spacing: Style.space(8)
                TextField {
                    id: importField
                    width: parent.width - importButton.width - parent.spacing
                    font.family: root.uiFont
                    placeholderText: "URI, file path, or subscription URL"
                    text: root.importText
                    onTextChanged: root.importText = text
                }
                Button {
                    id: importButton
                    text: "Import"
                    fontFamily: root.uiFont
                    bordered: true
                    onClicked: root.host.run(["--import", root.importText], function () {
                        root.importText = ""
                        root.host.refresh()
                    })
                }
            }
            Row {
                spacing: Style.space(16)
                Dropdown {
                    label: "Mode"
                    fontFamily: root.uiFont
                    width: Style.space(220)
                    options: ["manual", "system", "env-only", "tun"]
                    value: root.host ? root.host.mode : "manual"
                    onChanged: function (next) {
                        root.host.run(["--settings-set", "mode", next], function () { root.host.refresh() })
                    }
                }
                Dropdown {
                    label: "Backend"
                    fontFamily: root.uiFont
                    width: Style.space(220)
                    options: ["auto", "xray", "sing-box"]
                    value: "auto"
                    onChanged: function (next) {
                        root.host.run(["--settings-set", "backend", next], function () {})
                    }
                }
            }
        }
    }

    ConfirmDialog {
        anchors.fill: parent
        opened: root.confirmDelete
        fontFamily: root.uiFont
        message: "Remove this server from the catalog?"
        confirmText: "Delete"
        onConfirmed: {
            root.host.run(["--remove", root.pendingId, "--confirm"], function () { root.host.refresh() })
            root.confirmDelete = false
        }
        onCanceled: root.confirmDelete = false
    }

    ConfirmDialog {
        anchors.fill: parent
        opened: root.confirmTun
        fontFamily: root.uiFont
        message: "Tun captures all machine traffic. If the core crashes, clear internet stays blocked until disconnect."
        confirmText: "Capture traffic"
        onConfirmed: {
            var id = root.pendingId
            root.confirmTun = false
            root.host.run(["--ack-tun"], function () {
                root.host.tunAck = true
                root.host.run(["--connect", id], function () { root.host.refresh() })
            })
        }
        onCanceled: root.confirmTun = false
    }

    function connectTo(id) {
        if (root.host && root.host.mode === "tun" && !root.host.tunAck) {
            root.pendingId = id
            root.confirmTun = true
            return
        }
        root.host.run(["--connect", id], function () { root.host.refresh() })
    }
}
