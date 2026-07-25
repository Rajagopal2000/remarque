import QtQuick 2.15

// Full-panel overlay showing the conversation history for the current document.
// Paginated like AnswerView (tap left/right halves to flip): e-ink friendly.
Rectangle {
    id: view
    color: "white"
    visible: false

    property alias historyText: pages.fullText

    function open(turns) {
        pages.fullText = format(turns);
        pages.currentPage = 0;
        visible = true;
    }

    function format(turns) {
        if (!turns || turns.length === 0)
            return "No history yet for this document.";
        var out = [];
        var lastDay = "";
        for (var i = 0; i < turns.length; i++) {
            var t = turns[i];
            if (t.ts) {
                var day = new Date(t.ts * 1000).toDateString();
                if (day !== lastDay) {
                    out.push("--- " + day + " ---");
                    lastDay = day;
                }
            }
            out.push((t.role === "user" ? "You: " : "AI: ") + t.content);
        }
        return out.join("\n\n");
    }

    Column {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        Row {
            width: parent.width
            height: 44
            spacing: 10
            Text {
                text: "History"
                font.pixelSize: 26
                font.bold: true
                width: parent.width - 70
            }
            Rectangle {
                width: 56; height: 40
                border.width: 2; radius: 6
                Text { anchors.centerIn: parent; text: "✕"; font.pixelSize: 22 }
                MouseArea {
                    objectName: "historyCloseButton"
                    anchors.fill: parent
                    onClicked: view.visible = false
                }
            }
        }

        AnswerView {
            id: pages
            objectName: "historyPages"
            width: parent.width
            height: parent.height - 54
            fontPx: 23
        }
    }
}
