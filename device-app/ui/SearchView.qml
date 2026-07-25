import QtQuick 2.15

// Full-panel overlay showing search hits across conversations and documents.
// Paginated like AnswerView (tap left/right halves to flip): e-ink friendly.
Rectangle {
    id: view
    color: "white"
    visible: false

    property alias resultsText: pages.fullText

    function open(data) {
        pages.fullText = format(data);
        pages.currentPage = 0;
        visible = true;
    }

    function format(data) {
        var out = ["Search: " + data.query];
        var i;
        if (data.history.length > 0) {
            out.push("--- In conversations ---");
            for (i = 0; i < data.history.length; i++) {
                var h = data.history[i];
                var day = h.ts ? new Date(h.ts * 1000).toDateString() : "";
                out.push("[" + h.title + (day !== "" ? " - " + day : "") + "]\n"
                         + (h.role === "user" ? "You: " : "AI: ") + h.snippet);
            }
        }
        if (data.documents.length > 0) {
            out.push("--- In documents ---");
            for (i = 0; i < data.documents.length; i++) {
                var d = data.documents[i];
                out.push("[" + d.title + " - p." + d.page + "]\n" + d.snippet);
            }
        }
        if (data.history.length === 0 && data.documents.length === 0)
            out.push("No matches.");
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
                text: "Search results"
                font.pixelSize: 26
                font.bold: true
                width: parent.width - 70
            }
            Rectangle {
                width: 56; height: 40
                border.width: 2; radius: 6
                Text { anchors.centerIn: parent; text: "✕"; font.pixelSize: 22 }
                MouseArea {
                    objectName: "searchCloseButton"
                    anchors.fill: parent
                    onClicked: view.visible = false
                }
            }
        }

        AnswerView {
            id: pages
            objectName: "searchPages"
            width: parent.width
            height: parent.height - 54
            fontPx: 23
        }
    }
}
