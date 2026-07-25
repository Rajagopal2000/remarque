import QtQuick 2.15

// M1 spike: verifies (a) pen input reaches QML handlers inside an AppLoad window,
// (b) XMLHttpRequest can reach the Mac companion server, (c) rcc/QML load at all.
Rectangle {
    id: root
    anchors.fill: parent
    color: "white"

    signal close
    function unloading() {}

    Config { id: config }

    property string log: "Write in each strip with the PEN, then with a finger.\nWatch which strips get ink and what the log says."

    function addLog(line) {
        var lines = (log + "\n" + line).split("\n");
        if (lines.length > 6)
            lines = lines.slice(lines.length - 6);
        log = lines.join("\n");
    }

    Column {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        Row {
            width: parent.width
            height: 50
            Text {
                text: "AI Pen Spike"
                font.pixelSize: 32
                font.bold: true
                width: parent.width - 260
            }
            Rectangle {
                width: 180; height: 46
                border.width: 2; radius: 6
                Text { anchors.centerIn: parent; text: "Test server"; font.pixelSize: 22 }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        root.addLog("GET " + config.serverUrl + "/healthz ...");
                        var xhr = new XMLHttpRequest();
                        xhr.onreadystatechange = function() {
                            if (xhr.readyState === XMLHttpRequest.DONE)
                                root.addLog("healthz -> status " + xhr.status + " body " + xhr.responseText);
                        };
                        xhr.open("GET", config.serverUrl + "/healthz");
                        xhr.timeout = 5000;
                        xhr.send();
                    }
                }
            }
            Rectangle {
                width: 60; height: 46
                border.width: 2; radius: 6
                Text { anchors.centerIn: parent; text: "✕"; font.pixelSize: 24 }
                MouseArea { anchors.fill: parent; onClicked: root.close() }
            }
        }

        InkStrip {
            width: parent.width
            height: (parent.height - 300) / 3
            label: "1. MouseArea"
            mode: "mouse"
            onEvent: (info) => root.addLog(info)
        }
        InkStrip {
            width: parent.width
            height: (parent.height - 300) / 3
            label: "2. PointHandler"
            mode: "point"
            onEvent: (info) => root.addLog(info)
        }
        InkStrip {
            width: parent.width
            height: (parent.height - 300) / 3
            label: "3. MultiPointTouchArea"
            mode: "touch"
            onEvent: (info) => root.addLog(info)
        }

        Rectangle {
            width: parent.width
            height: 170
            color: "#f2f2f2"
            border.width: 1
            Text {
                anchors.fill: parent
                anchors.margins: 8
                text: root.log
                font.pixelSize: 18
                font.family: "monospace"
                wrapMode: Text.Wrap
            }
        }
    }
}
