import QtQuick 2.15

// Full-panel overlay: a paginated list of past questions (newest first, day
// headers), tap a question for its full answer on its own screen. The
// "This page" chip narrows the list to questions asked on the page currently
// open in the document. E-ink friendly: fixed rows, page flips, no scrolling.
Rectangle {
    id: view
    color: "white"
    visible: false

    property int currentPageNumber: 0   // 1-based pdf page from refresh; 0 = unknown
    property bool onlyThisPage: false
    property var entries: []            // {q, a, page, ts}, newest first
    property var displayItems: []       // headers + entries after filtering
    property int questionCount: 0
    property var detailEntry: null
    property int listPage: 0

    // For the simulator: the filtered question texts, joined.
    property string questionsText: ""
    property alias detailText: detailPages.fullText

    property int rowsPerPage: Math.max(3, Math.floor(listArea.height / 60))
    property int pageCount: Math.max(1, Math.ceil(displayItems.length / rowsPerPage))
    property var pageItems: displayItems.slice(
        listPage * rowsPerPage, (listPage + 1) * rowsPerPage)

    function open(turns) {
        entries = pair(turns);
        onlyThisPage = false;
        detailEntry = null;
        listPage = 0;
        rebuild();
        visible = true;
    }

    // Turns alternate user/assistant; fold them into question+answer entries.
    function pair(turns) {
        var es = [];
        var cur = null;
        for (var i = 0; i < (turns ? turns.length : 0); i++) {
            var t = turns[i];
            if (t.role === "user") {
                cur = { "q": t.content, "a": "", "page": t.page || 0, "ts": t.ts || 0 };
                es.push(cur);
            } else if (cur !== null) {
                cur.a = cur.a === "" ? t.content : cur.a + "\n\n" + t.content;
            }
        }
        es.reverse();
        return es;
    }

    function rebuild() {
        var items = [];
        var qs = [];
        var lastDay = "";
        for (var i = 0; i < entries.length; i++) {
            var e = entries[i];
            if (onlyThisPage && currentPageNumber > 0 && e.page !== currentPageNumber)
                continue;
            var day = e.ts ? new Date(e.ts * 1000).toDateString() : "";
            if (day !== lastDay) {
                items.push({ "header": day });
                lastDay = day;
            }
            items.push({ "entry": e });
            qs.push(e.q);
        }
        displayItems = items;
        questionCount = qs.length;
        questionsText = qs.join("\n");
        if (listPage >= pageCount)
            listPage = 0;
    }

    function openDetail(e) {
        detailEntry = e;
        detailPages.fullText = "Q: " + e.q + "\n\n" + e.a;
        detailPages.currentPage = 0;
    }

    Column {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        Row {
            width: parent.width
            height: 44
            spacing: 10
            Rectangle {
                width: 110; height: 40
                visible: view.detailEntry !== null
                border.width: 2; radius: 6
                Text { anchors.centerIn: parent; text: "◀ Back"; font.pixelSize: 19 }
                MouseArea {
                    objectName: "historyBackButton"
                    anchors.fill: parent
                    onClicked: view.detailEntry = null
                }
            }
            Text {
                text: view.detailEntry !== null
                      ? (view.detailEntry.page ? "p." + view.detailEntry.page : "")
                        + (view.detailEntry.ts
                           ? (view.detailEntry.page ? " · " : "")
                             + new Date(view.detailEntry.ts * 1000).toDateString()
                           : "")
                      : "History"
                font.pixelSize: view.detailEntry !== null ? 19 : 26
                font.bold: view.detailEntry === null
                color: view.detailEntry !== null ? "#555555" : "black"
                width: parent.width - 70 - (view.detailEntry !== null ? 120 : 0)
                elide: Text.ElideRight
                anchors.verticalCenter: parent.verticalCenter
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

        // Filter chips (list mode only).
        Row {
            width: parent.width
            height: 40
            spacing: 10
            visible: view.detailEntry === null
            Rectangle {
                width: allText.implicitWidth + 26; height: 38
                border.width: 2; radius: 6
                color: view.onlyThisPage ? "white" : "#e8e8e8"
                Text { id: allText; anchors.centerIn: parent; text: "All"; font.pixelSize: 17 }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        view.onlyThisPage = false;
                        view.listPage = 0;
                        view.rebuild();
                    }
                }
            }
            Rectangle {
                width: thisPageText.implicitWidth + 26; height: 38
                border.width: 2; radius: 6
                color: view.onlyThisPage ? "#e8e8e8" : "white"
                opacity: view.currentPageNumber > 0 ? 1 : 0.4
                Text {
                    id: thisPageText
                    anchors.centerIn: parent
                    text: "This page" + (view.currentPageNumber > 0
                                         ? " (p." + view.currentPageNumber + ")" : "")
                    font.pixelSize: 17
                }
                MouseArea {
                    objectName: "thisPageChip"
                    anchors.fill: parent
                    enabled: view.currentPageNumber > 0
                    onClicked: {
                        view.onlyThisPage = true;
                        view.listPage = 0;
                        view.rebuild();
                    }
                }
            }
            Text {
                text: view.questionCount + (view.questionCount === 1 ? " question" : " questions")
                font.pixelSize: 17
                color: "#777777"
                height: 38
                verticalAlignment: Text.AlignVCenter
            }
        }

        // Question list.
        Item {
            id: listArea
            width: parent.width
            height: parent.height - 44 - 40 - 44 - 3 * parent.spacing
            visible: view.detailEntry === null

            Column {
                anchors.fill: parent
                spacing: 6
                Repeater {
                    model: view.pageItems
                    delegate: Item {
                        required property var modelData
                        required property int index
                        width: listArea.width
                        height: modelData.header !== undefined ? 34 : 54
                        Text {
                            visible: modelData.header !== undefined
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData.header !== undefined
                                  ? "—— " + modelData.header + " ——" : ""
                            font.pixelSize: 16
                            color: "#777777"
                        }
                        Rectangle {
                            visible: modelData.header === undefined
                            anchors.fill: parent
                            border.width: 1
                            border.color: "#999999"
                            radius: 6
                            Row {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                spacing: 8
                                Text {
                                    text: modelData.entry !== undefined && modelData.entry.page
                                          ? "p." + modelData.entry.page : ""
                                    font.pixelSize: 16
                                    color: "#777777"
                                    width: 52
                                    height: parent.height
                                    verticalAlignment: Text.AlignVCenter
                                }
                                Text {
                                    text: modelData.entry !== undefined ? modelData.entry.q : ""
                                    font.pixelSize: 19
                                    elide: Text.ElideRight
                                    width: parent.width - 52 - 24 - 2 * parent.spacing
                                    height: parent.height
                                    verticalAlignment: Text.AlignVCenter
                                }
                                Text {
                                    text: "▸"
                                    font.pixelSize: 19
                                    color: "#555555"
                                    width: 24
                                    height: parent.height
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                            MouseArea {
                                objectName: "historyRow" + index
                                anchors.fill: parent
                                onClicked: view.openDetail(modelData.entry)
                            }
                        }
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                visible: view.displayItems.length === 0
                text: view.onlyThisPage
                      ? "No questions on this page yet."
                      : "No history yet for this document."
                font.pixelSize: 20
                color: "#777777"
            }
        }

        // List pager.
        Row {
            width: parent.width
            height: 44
            spacing: 10
            visible: view.detailEntry === null
            Rectangle {
                width: 70; height: 40
                border.width: 2; radius: 6
                opacity: view.listPage > 0 ? 1 : 0.3
                anchors.verticalCenter: parent.verticalCenter
                Text { anchors.centerIn: parent; text: "◀"; font.pixelSize: 19 }
                MouseArea {
                    anchors.fill: parent
                    enabled: view.listPage > 0
                    onClicked: view.listPage--
                }
            }
            Text {
                text: (view.listPage + 1) + " / " + view.pageCount
                font.pixelSize: 19
                color: "#555555"
                height: 40
                verticalAlignment: Text.AlignVCenter
            }
            Rectangle {
                width: 70; height: 40
                border.width: 2; radius: 6
                opacity: view.listPage < view.pageCount - 1 ? 1 : 0.3
                anchors.verticalCenter: parent.verticalCenter
                Text { anchors.centerIn: parent; text: "▶"; font.pixelSize: 19 }
                MouseArea {
                    anchors.fill: parent
                    enabled: view.listPage < view.pageCount - 1
                    onClicked: view.listPage++
                }
            }
        }

        // Full answer for the tapped question.
        AnswerView {
            id: detailPages
            objectName: "historyDetail"
            width: parent.width
            height: parent.height - 54
            fontPx: 23
            visible: view.detailEntry !== null
        }
    }
}
