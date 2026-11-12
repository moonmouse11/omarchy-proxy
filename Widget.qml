import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Client.js" as Client

BarWidget {
    id: root
    moduleName: "omarchy-proxy"

    property var status: ({})
    property var servers: []
    property bool setupNeeded: false
    property bool statusFailed: false
    property string mode: "manual"
    property bool tunAck: false
    property string pollInterval: "3000"
    property var jobs: []

    readonly property bool connected: status.state === "running" && status.health === "ok"
    readonly property bool connecting: status.health === "starting" || status.state === "starting"
    readonly property bool failed: status.health === "failed" || status.health === "degraded" || statusFailed
    function serverName() {
        var id = String(status.server_id || "")
        for (var i = 0; i < servers.length; i++) {
            if (String(servers[i].id) === id)
                return String(servers[i].name || "")
        }
        return ""
    }
    readonly property color mark: failed ? Color.urgent : (connected || connecting ? Color.accent : Color.muted)

    function run(args, done) {
        var next = root.jobs.slice()
        next.push({ args: args, done: done || null })
        root.jobs = next
        pump()
    }

    function pump() {
        if (commandProc.running || root.jobs.length === 0)
            return
        var job = root.jobs[0]
        root.jobs = root.jobs.slice(1)
        commandProc.callback = job.done
        commandProc.command = ["omarchy-proxy"].concat(job.args)
        commandProc.running = true
    }

    function refresh() {
        run(["--status"], function (payload) {
            if (!payload || payload.ok === false) {
                root.statusFailed = true
                return
            }
            root.statusFailed = false
            root.status = payload
            root.tunAck = payload.tun_acknowledged === true
        })
        run(["--servers"], function (payload) {
            root.servers = payload && payload.servers ? payload.servers : []
        })
        run(["--settings-get"], function (payload) {
            var settings = payload && payload.settings ? payload.settings : null
            if (settings && settings.mode)
                root.mode = String(settings.mode)
        })
        run(["--doctor"], function (payload) {
            var issues = payload && payload.issues ? payload.issues : (payload && payload.error ? [payload.error] : [])
            root.setupNeeded = issues.some(function (item) { return item.code === "no_backend" })
        })
    }

    readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

    function open() { if (panelLoader.item) panelLoader.item.open() }
    function close() { if (panelLoader.item) panelLoader.item.close() }
    function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
    function openWindow() {
        close()
        if (!windowLoader.item)
            return
        windowLoader.item.visible = false
        windowLoader.item.visible = true
    }

    function injectPanel() {
        var target = panelLoader.item
        if (!target)
            return
        target.host = root
        target.anchorItem = button
        if ("bar" in target)
            target.bar = root.bar
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    onBarChanged: injectPanel()

    ShellIpc {
        target: "omarchy-proxy"
        function window(): void { root.openWindow() }
        function toggle(): void { root.togglePanel() }
    }

    Process {
        id: commandProc
        property var callback: null
        stdout: StdioCollector {
            onStreamFinished: {
                var payload = Client.last(this.text)
                var done = commandProc.callback
                commandProc.callback = null
                if (done)
                    done(payload)
                root.pump()
            }
        }
    }

    Timer {
        interval: Number(root.pollInterval)
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: "\uf0ec"
        slotSize: Style.bar.statusSlot
        foreground: root.mark
        tooltipText: root.setupNeeded ? "Setup needed" : (root.connected ? root.serverName() : "Proxy")
        labelText: root.setupNeeded ? "Setup needed" : (root.connected ? root.serverName() : "")
        onPressed: function (buttonCode) {
            if (buttonCode === Qt.RightButton)
                root.openWindow()
            else
                root.togglePanel()
        }
    }

    SequentialAnimation {
        running: root.connecting
        loops: Animation.Infinite
        onRunningChanged: if (!running) button.opacity = 1
        NumberAnimation { target: button; property: "opacity"; to: 0.35; duration: 500 }
        NumberAnimation { target: button; property: "opacity"; to: 1; duration: 500 }
    }

    Loader {
        id: panelLoader
        source: "Panel.qml"
        visible: false
        onLoaded: {
            root.injectPanel()
            Qt.callLater(root.injectPanel)
        }
    }

    Loader {
        id: windowLoader
        source: "AppWindow.qml"
        onLoaded: item.host = root
    }
}
