import QtQuick 2.15

// AI reading assistant panel: handwrite a question, get a typeset answer.
Rectangle {
    id: root
    anchors.fill: parent
    color: "white"

    signal close
    function unloading() {}

    Config { id: config }

    property string docTitle: ""
    property string docId: ""
    property string status: "Ready"
    property string questionRead: ""
    property string sessionLabel: ""
    property string usageLabel: ""
    property string staleNote: ""
    property bool includeHighlights: true
    property bool brief: false
    property string docTextMode: "none"   // none | page | full | image (extra context per ask)
    property var lastAction: null
    property bool canRetry: false
    property bool canReread: false
    property bool quizPending: false
    property int elapsedS: 0

    Timer {
        interval: 1000
        repeat: true
        running: api.busy
        onTriggered: root.elapsedS++
    }

    function describeSession(s) {
        if (!s || !s.supported)
            return "";
        if (!s.exists)
            return "Session: new";
        return "Session: " + s.turns + " turns · " + s.age_days + "d old";
    }

    function describeUsage(u) {
        if (!u || u.input_tokens === undefined || u.input_tokens === null)
            return "";
        var parts = [(u.input_tokens / 1000).toFixed(1) + "k in"];
        if (u.cached_input_tokens)
            parts.push((u.cached_input_tokens / 1000).toFixed(1) + "k cached");
        parts.push(u.output_tokens + " out");
        return parts.join(" · ");
    }

    Component.onCompleted: api.refresh()

    function startAction(label) {
        answerView.fullText = "";
        root.questionRead = "";
        root.usageLabel = "";
        root.staleNote = "";
        root.canRetry = false;
        root.canReread = false;
        root.elapsedS = 0;
        root.status = label;
    }

    // The transcription was off: same strokes, read by the answer model instead.
    function reread() {
        var a = root.lastAction;
        if (!a || a.kind !== "ask")
            return;
        root.startAction("Re-reading with the answer model...");
        api.ask(a.strokes, a.w, a.h, root.includeHighlights, root.docTextMode, root.brief, true);
    }

    function retry() {
        var a = root.lastAction;
        if (!a)
            return;
        if (a.kind === "ask") {
            root.startAction("Reading handwriting...");
            api.ask(a.strokes, a.w, a.h, root.includeHighlights, root.docTextMode, root.brief);
        } else if (a.kind === "quick") {
            root.startAction("Thinking...");
            api.quick(a.action, root.docId, root.brief);
        } else if (a.kind === "search") {
            root.status = "Searching...";
            root.canRetry = false;
            api.search(a.strokes, a.w, a.h);
        } else if (a.kind === "quizstart") {
            root.startAction("Thinking of a question...");
            api.quizStart(root.docId);
        } else if (a.kind === "quizanswer") {
            root.startAction("Reading handwriting...");
            api.quizAnswer(a.strokes, a.w, a.h, root.docId);
        } else if (a.kind === "pageask") {
            root.startAction("Reading page ink...");
            api.pageAsk(root.includeHighlights, root.brief);
        } else if (a.kind === "anki") {
            root.startAction("Creating Anki deck...");
            api.anki(root.docId !== "" ? root.docId : "__no_document__");
        } else if (a.kind === "compact") {
            root.startAction("Compacting session...");
            api.compactSession(root.docId !== "" ? root.docId : "__no_document__");
        }
    }

    function formatAge(seconds) {
        if (seconds < 3600)
            return Math.round(seconds / 60) + "m";
        return (seconds / 3600).toFixed(1) + "h";
    }

    Api {
        id: api
        serverUrl: config.serverUrl
        apiToken: config.apiToken
        onExported: (data) => {
            root.status = (data.pushed
                ? "Notes on tablet: restart device to see them"
                : "Notes exported")
                + (data.obsidian_path ? " · saved to Obsidian" : "");
        }
        onRefreshed: (data) => {
            root.docTitle = data.title || "";
            root.docId = data.doc_id || "";
            root.sessionLabel = root.describeSession(data.session);
            root.status = data.sync_error
                ? "Sync failed, showing last-synced data"
                : "Ready";
        }
        onRefreshFailed: (msg) => root.status = "No document: " + msg
        onHistoryLoaded: (turns) => historyView.open(turns)
        onSearchResults: (data) => {
            scratchpad.clearInk();
            root.status = "Search: " + data.query;
            searchView.open(data);
        }
        onSessionCleared: (data) => {
            root.sessionLabel = "Session: new";
            root.quizPending = false;
            root.status = "Context cleared, next question starts fresh";
        }
        onPhaseChanged: (phase) => {
            if (api.busy)
                root.status = phase === "transcribing" ? "Reading handwriting..." : "Thinking...";
        }
        onQuestionRead: (q) => {
            if (root.questionRead === "")
                scratchpad.clearInk();   // ink served its purpose; pad ready for follow-up
            root.questionRead = q;
            root.status = "Q: " + q;
        }
        onTextAppended: (t) => answerView.fullText = answerView.fullText + t
        onSessionInfo: (info) => root.sessionLabel = root.describeSession(info)
        onUsageInfo: (u) => root.usageLabel = root.describeUsage(u)
        onSyncAge: (age) => {
            if (age !== null && age !== undefined && age > 120)
                root.staleNote = "using data synced " + root.formatAge(age) + " ago";
        }
        onCancelled: root.status = "Stopped"
        onFinished: {
            if (root.lastAction && root.lastAction.kind === "quizstart") {
                root.quizPending = true;
                root.status = "Quiz: handwrite your answer, then tap Answer";
            } else if (root.lastAction && root.lastAction.kind === "quizanswer") {
                root.quizPending = false;
                root.status = "Quiz graded";
            } else {
                root.status = root.questionRead !== "" ? "Q: " + root.questionRead : "Done";
                root.canReread = root.lastAction !== null && root.lastAction.kind === "ask";
            }
        }
        onFailed: (msg) => {
            if (root.lastAction && root.lastAction.kind === "quizanswer")
                root.quizPending = false;
            root.status = "Error: " + msg;
            root.canRetry = root.lastAction !== null;
        }
    }

    Column {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        // Header
        Row {
            width: parent.width
            height: 44
            spacing: 10
            Text {
                text: "AI Assistant" + (root.docTitle !== "" ? "  ·  " + root.docTitle : "")
                font.pixelSize: 26
                font.bold: true
                width: parent.width - 70
                elide: Text.ElideRight
            }
            Rectangle {
                width: 56; height: 40
                border.width: 2; radius: 6
                Text { anchors.centerIn: parent; text: "✕"; font.pixelSize: 22 }
                MouseArea { anchors.fill: parent; onClicked: root.close() }
            }
        }

        Scratchpad {
            id: scratchpad
            objectName: "scratchpad"
            width: parent.width
            height: parent.height * 0.28
        }

        // Controls
        Row {
            width: parent.width
            height: 48
            spacing: 10

            Rectangle {
                width: 120; height: 44
                color: api.busy ? "white" : (!scratchpad.hasInk ? "#dddddd" : "black")
                border.width: api.busy ? 2 : 0
                radius: 6
                Text {
                    anchors.centerIn: parent
                    text: api.busy ? "Stop" : (root.quizPending ? "Answer" : "Ask")
                    color: api.busy ? "black" : (!scratchpad.hasInk ? "#888888" : "white")
                    font.pixelSize: 24
                    font.bold: true
                }
                MouseArea {
                    objectName: "askButton"
                    anchors.fill: parent
                    enabled: api.busy || scratchpad.hasInk
                    onClicked: {
                        if (api.busy) {
                            api.cancel();
                            return;
                        }
                        var strokes = scratchpad.strokesForJson();
                        if (root.quizPending) {
                            root.lastAction = { "kind": "quizanswer", "strokes": strokes,
                                                "w": scratchpad.width, "h": scratchpad.height };
                            root.startAction("Reading handwriting...");
                            api.quizAnswer(strokes, scratchpad.width, scratchpad.height, root.docId);
                            return;
                        }
                        root.lastAction = { "kind": "ask", "strokes": strokes,
                                            "w": scratchpad.width, "h": scratchpad.height };
                        root.startAction("Reading handwriting...");
                        api.ask(strokes, scratchpad.width, scratchpad.height,
                                root.includeHighlights, root.docTextMode, root.brief);
                    }
                }
            }
            Rectangle {
                width: 90; height: 44
                border.width: 2; radius: 6
                Text { anchors.centerIn: parent; text: "Undo"; font.pixelSize: 20 }
                MouseArea {
                    objectName: "undoButton"
                    anchors.fill: parent
                    onClicked: scratchpad.undoStroke()
                }
            }
            Rectangle {
                width: 90; height: 44
                border.width: 2; radius: 6
                Text { anchors.centerIn: parent; text: "Clear"; font.pixelSize: 20 }
                MouseArea { anchors.fill: parent; onClicked: scratchpad.clearInk() }
            }
            Rectangle {
                width: 110; height: 44
                border.width: 2; radius: 6
                color: (!scratchpad.hasInk || api.searching) ? "#dddddd" : "white"
                Text {
                    anchors.centerIn: parent
                    text: "Search"
                    color: (!scratchpad.hasInk || api.searching) ? "#888888" : "black"
                    font.pixelSize: 20
                }
                MouseArea {
                    objectName: "searchButton"
                    anchors.fill: parent
                    enabled: !api.busy && !api.searching && scratchpad.hasInk
                    onClicked: {
                        root.lastAction = { "kind": "search", "strokes": scratchpad.strokesForJson(),
                                            "w": scratchpad.width, "h": scratchpad.height };
                        root.canRetry = false;
                        root.status = "Searching...";
                        api.search(root.lastAction.strokes, root.lastAction.w, root.lastAction.h);
                    }
                }
            }
            Rectangle {
                width: 100; height: 44
                border.width: 2; radius: 6
                color: root.includeHighlights ? "#e8e8e8" : "white"
                Text {
                    anchors.centerIn: parent
                    text: root.includeHighlights ? "HL: on" : "HL: off"
                    font.pixelSize: 20
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: root.includeHighlights = !root.includeHighlights
                }
            }
            Rectangle {
                width: 140; height: 44
                border.width: 2; radius: 6
                Text { anchors.centerIn: parent; text: "Attach: " + root.docTextMode; font.pixelSize: 18 }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        var modes = ["none", "page", "full", "image"];
                        root.docTextMode = modes[(modes.indexOf(root.docTextMode) + 1) % modes.length];
                    }
                }
            }
        }

        // Quick actions: one tap, no handwriting, no transcription call.
        component QuickButton: Rectangle {
            property string label: ""
            property string action: ""
            width: (parent.width - 70) / 8
            height: 40
            border.width: 2; radius: 6
            opacity: api.busy ? 0.4 : 1
            Text { anchors.centerIn: parent; text: parent.label; font.pixelSize: 18 }
            MouseArea {
                anchors.fill: parent
                enabled: !api.busy
                onClicked: {
                    root.lastAction = { "kind": "quick", "action": parent.action };
                    root.startAction("Thinking...");
                    api.quick(parent.action, root.docId, root.brief);
                }
            }
        }

        Row {
            width: parent.width
            height: 44
            spacing: 10
            // Native-ink ask: the question is handwritten on the PDF page
            // itself (no scratchpad latency); the server reads the fresh ink.
            Rectangle {
                width: (parent.width - 70) / 8
                height: 40
                border.width: 2; radius: 6
                opacity: api.busy ? 0.4 : 1
                Text { anchors.centerIn: parent; text: "Page ask"; font.pixelSize: 18; font.bold: true }
                MouseArea {
                    objectName: "pageAskButton"
                    anchors.fill: parent
                    enabled: !api.busy
                    onClicked: {
                        root.lastAction = { "kind": "pageask" };
                        root.startAction("Reading page ink...");
                        api.pageAsk(root.includeHighlights, root.brief);
                    }
                }
            }
            QuickButton { label: "Sum page"; action: "summarize_page" }
            QuickButton { label: "Sum doc"; action: "summarize_doc" }
            QuickButton { label: "Explain HL"; action: "explain_highlights" }
            QuickButton { label: "Define HL"; action: "define_highlight" }
            Rectangle {
                width: (parent.width - 70) / 8
                height: 40
                border.width: 2; radius: 6
                color: root.quizPending ? "#e8e8e8" : "white"
                opacity: api.busy ? 0.4 : 1
                Text { anchors.centerIn: parent; text: "Quiz"; font.pixelSize: 18 }
                MouseArea {
                    objectName: "quizButton"
                    anchors.fill: parent
                    enabled: !api.busy
                    onClicked: {
                        root.lastAction = { "kind": "quizstart" };
                        root.startAction("Thinking of a question...");
                        api.quizStart(root.docId);
                    }
                }
            }
            Rectangle {
                width: (parent.width - 70) / 8
                height: 40
                border.width: 2; radius: 6
                opacity: api.busy ? 0.4 : 1
                Text { anchors.centerIn: parent; text: "Anki"; font.pixelSize: 18 }
                MouseArea {
                    objectName: "ankiButton"
                    anchors.fill: parent
                    enabled: !api.busy
                    onClicked: {
                        root.lastAction = { "kind": "anki" };
                        root.startAction("Creating Anki deck...");
                        api.anki(root.docId !== "" ? root.docId : "__no_document__");
                    }
                }
            }
            Rectangle {
                width: (parent.width - 70) / 8
                height: 40
                border.width: 2; radius: 6
                opacity: api.busy ? 0.4 : 1
                Text { anchors.centerIn: parent; text: "Export"; font.pixelSize: 18 }
                MouseArea {
                    objectName: "exportButton"
                    anchors.fill: parent
                    enabled: !api.busy
                    onClicked: {
                        root.status = "Exporting notes...";
                        api.exportNotes(root.docId !== "" ? root.docId : "__no_document__");
                    }
                }
            }
        }

        AnswerView {
            id: answerView
            objectName: "answerView"
            width: parent.width
            height: parent.height - scratchpad.height - 290
        }

        // Session row
        Row {
            width: parent.width
            height: 40
            spacing: 10
            Text {
                objectName: "sessionText"
                text: root.sessionLabel !== "" ? root.sessionLabel : "Session: -"
                font.pixelSize: 19
                color: "#555555"
                width: parent.width - 580
                anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
            }
            Rectangle {
                width: 120; height: 38
                border.width: 2; radius: 6
                color: root.brief ? "#e8e8e8" : "white"
                anchors.verticalCenter: parent.verticalCenter
                Text { anchors.centerIn: parent; text: root.brief ? "Brief: on" : "Brief: off"; font.pixelSize: 18 }
                MouseArea {
                    anchors.fill: parent
                    onClicked: root.brief = !root.brief
                }
            }
            Rectangle {
                width: 120; height: 38
                border.width: 2; radius: 6
                anchors.verticalCenter: parent.verticalCenter
                Text { anchors.centerIn: parent; text: "History"; font.pixelSize: 19 }
                MouseArea {
                    objectName: "historyButton"
                    anchors.fill: parent
                    onClicked: api.fetchHistory(root.docId !== "" ? root.docId : "__no_document__")
                }
            }
            Rectangle {
                width: 130; height: 38
                border.width: 2; radius: 6
                visible: root.sessionLabel !== ""
                opacity: api.busy ? 0.4 : 1
                anchors.verticalCenter: parent.verticalCenter
                Text { anchors.centerIn: parent; text: "Compact"; font.pixelSize: 19 }
                MouseArea {
                    objectName: "compactButton"
                    anchors.fill: parent
                    enabled: !api.busy
                    onClicked: {
                        root.lastAction = { "kind": "compact" };
                        root.startAction("Compacting session...");
                        api.compactSession(root.docId !== "" ? root.docId : "__no_document__");
                    }
                }
            }
            Rectangle {
                width: 170; height: 38
                border.width: 2; radius: 6
                anchors.verticalCenter: parent.verticalCenter
                Text { anchors.centerIn: parent; text: "New session"; font.pixelSize: 19 }
                MouseArea {
                    objectName: "newSessionButton"
                    anchors.fill: parent
                    enabled: !api.busy
                    onClicked: api.clearSession(root.docId !== "" ? root.docId : "__no_document__")
                }
            }
        }

        Row {
            width: parent.width
            height: 40
            spacing: 10
            Text {
                objectName: "statusText"
                width: parent.width - (root.canRetry ? 120 : 0)
                      - (root.canReread && !api.busy ? 130 : 0)
                anchors.verticalCenter: parent.verticalCenter
                text: root.status
                      + (api.busy && root.elapsedS > 2 ? "   " + root.elapsedS + "s" : "")
                      + (root.usageLabel !== "" ? "   [" + root.usageLabel + "]" : "")
                      + (root.staleNote !== "" ? "   (" + root.staleNote + ")" : "")
                font.pixelSize: 19
                color: "#555555"
                elide: Text.ElideRight
            }
            Rectangle {
                width: 120; height: 36
                visible: root.canReread && !api.busy
                border.width: 2; radius: 6
                anchors.verticalCenter: parent.verticalCenter
                Text { anchors.centerIn: parent; text: "Re-read"; font.pixelSize: 19 }
                MouseArea {
                    objectName: "rereadButton"
                    anchors.fill: parent
                    onClicked: root.reread()
                }
            }
            Rectangle {
                width: 110; height: 36
                visible: root.canRetry
                border.width: 2; radius: 6
                anchors.verticalCenter: parent.verticalCenter
                Text { anchors.centerIn: parent; text: "Retry"; font.pixelSize: 19; font.bold: true }
                MouseArea {
                    objectName: "retryButton"
                    anchors.fill: parent
                    onClicked: root.retry()
                }
            }
        }
    }

    HistoryView {
        id: historyView
        objectName: "historyView"
        anchors.fill: parent
        z: 10
    }

    SearchView {
        id: searchView
        objectName: "searchView"
        anchors.fill: parent
        z: 10
    }
}
