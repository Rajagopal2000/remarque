import QtQuick 2.15

// Paginated answer display: no scrolling on e-ink, tap left/right halves to flip.
Rectangle {
    id: view
    color: "white"
    border.width: 1
    border.color: "#888888"

    property string fullText: ""
    property int currentPage: 0
    property var pages: []
    property int fontPx: 26

    onFullTextChanged: repaginate()
    onWidthChanged: repaginate()
    onHeightChanged: repaginate()
    onFontPxChanged: repaginate()

    // Approximate chars per page from geometry, then split at word boundaries.
    function repaginate() {
        if (fullText.length === 0) {
            pages = [];
            currentPage = 0;
            return;
        }
        var usableW = width - 32;
        var usableH = height - 60;
        var charsPerLine = Math.max(10, Math.floor(usableW / (fontPx * 0.52)));
        var linesPerPage = Math.max(3, Math.floor(usableH / (fontPx * 1.35)));
        var charsPerPage = charsPerLine * linesPerPage;
        var result = [];
        var remaining = fullText;
        while (remaining.length > 0) {
            if (remaining.length <= charsPerPage) {
                result.push(remaining);
                break;
            }
            var cut = remaining.lastIndexOf(" ", charsPerPage);
            var nl = remaining.lastIndexOf("\n", charsPerPage);
            cut = Math.max(cut, nl);
            if (cut < charsPerPage * 0.5)
                cut = charsPerPage;
            result.push(remaining.substring(0, cut));
            remaining = remaining.substring(cut).replace(/^\s+/, "");
        }
        var wasLast = currentPage >= pages.length - 1;
        pages = result;
        if (wasLast || currentPage >= pages.length)
            currentPage = pages.length - 1;
    }

    Text {
        anchors.fill: parent
        anchors.margins: 16
        anchors.bottomMargin: 44
        text: view.pages.length > 0 ? view.pages[view.currentPage] : ""
        font.pixelSize: view.fontPx
        wrapMode: Text.Wrap
        color: "black"
    }

    Text {
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottomMargin: 10
        visible: view.pages.length > 1
        text: "◀   " + (view.currentPage + 1) + " / " + view.pages.length + "   ▶"
        font.pixelSize: 22
        color: "#555555"
    }

    MouseArea {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: parent.width / 2
        onClicked: if (view.currentPage > 0) view.currentPage--
    }
    MouseArea {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: parent.width / 2
        onClicked: if (view.currentPage < view.pages.length - 1) view.currentPage++
    }

    // Font size controls; z above the page-flip tap zones.
    Rectangle {
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        anchors.margins: 6
        width: 44; height: 32
        border.width: 1; radius: 4
        z: 5
        visible: view.fullText.length > 0
        Text { anchors.centerIn: parent; text: "A-"; font.pixelSize: 17 }
        MouseArea {
            anchors.fill: parent
            onClicked: view.fontPx = Math.max(18, view.fontPx - 3)
        }
    }
    Rectangle {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 6
        width: 44; height: 32
        border.width: 1; radius: 4
        z: 5
        visible: view.fullText.length > 0
        Text { anchors.centerIn: parent; text: "A+"; font.pixelSize: 17 }
        MouseArea {
            anchors.fill: parent
            onClicked: view.fontPx = Math.min(40, view.fontPx + 3)
        }
    }
}
