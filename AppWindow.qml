import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import qs.Commons
import "Client.js" as Client

Window {
    id: root
    title: "Proxy"
    width: 760
    height: 560
    color: Color.background
    visible: false

    property var host: null
    property string filterText: ""
    property string sortKey: "name"
    property string importText: ""
    property bool confirmDelete: false
    property string pendingId: ""

    function show() {
        visible = true
        raise()
        requestActivate()
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

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Style.space(12)
        spacing: Style.space(8)

        RowLayout {
            Layout.fillWidth: true
            TextField {
                Layout.fillWidth: true
                placeholderText: "Filter by name or protocol"
                text: root.filterText
                color: Color.foreground
                onTextChanged: root.filterText = text
            }
            ComboBox {
                model: ["name", "ping", "recent"]
                onActivated: root.sortKey = currentText
            }
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: root.shownServers()
            delegate: RowLayout {
                required property var modelData
                width: ListView.view.width
                Label {
                    textFormat: Text.PlainText
                    text: (modelData.name || modelData.id) + "  " + Client.schemeOf(modelData.uri)
                    color: Color.foreground
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                Button {
                    text: "Connect"
                    onClicked: root.host.run(["--connect", String(modelData.id)], function () { root.host.refresh() })
                }
                Button {
                    text: modelData.favorite ? "Unfavorite" : "Favorite"
                    onClicked: root.host.run(["--favorite", String(modelData.id)], function () { root.host.refresh() })
                }
                Button {
                    text: "Ping"
                    onClicked: root.host.run(["--ping", String(modelData.id)], function () { root.host.refresh() })
                }
                Button {
                    text: "Delete"
                    onClicked: {
                        root.pendingId = String(modelData.id)
                        root.confirmDelete = true
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            TextField {
                Layout.fillWidth: true
                placeholderText: "URI, file path, or subscription URL"
                text: root.importText
                onTextChanged: root.importText = text
            }
            Button {
                text: "Import"
                onClicked: root.host.run(["--import", root.importText], function () {
                    root.importText = ""
                    root.host.refresh()
                })
            }
        }

        RowLayout {
            Label { text: "Mode"; color: Color.foreground }
            ComboBox {
                model: ["manual", "system", "env-only"]
                onActivated: root.host.run(["--settings-set", "mode", currentText], function () { root.host.refresh() })
            }
            Label { text: "Backend"; color: Color.foreground }
            ComboBox {
                model: ["auto", "xray", "sing-box"]
                onActivated: root.host.run(["--settings-set", "backend", currentText], function () {})
            }
        }
    }

    Dialog {
        visible: root.confirmDelete
        title: "Delete server"
        modal: true
        anchors.centerIn: parent
        standardButtons: Dialog.Ok | Dialog.Cancel
        Label {
            textFormat: Text.PlainText
            text: "Remove this server from the catalog?"
            color: Color.foreground
        }
        onAccepted: {
            root.host.run(["--remove", root.pendingId, "--confirm"], function () { root.host.refresh() })
            root.confirmDelete = false
        }
        onRejected: root.confirmDelete = false
    }
}
