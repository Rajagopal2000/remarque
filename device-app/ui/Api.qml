import QtQuick 2.15

// HTTP client for the Mac companion service.
Item {
    id: api
    property string serverUrl: ""
    property string apiToken: ""
    property bool busy: false
    property bool searching: false
    property string jobId: ""
    property int cursor: 0

    signal searchResults(var data)
    signal refreshed(var data)
    signal refreshFailed(string message)
    signal sessionCleared(var data)
    signal historyLoaded(var turns)
    signal exported(var data)
    signal cancelled()
    signal syncAge(var seconds)
    signal questionRead(string question)
    signal phaseChanged(string phase)
    signal textAppended(string text)
    signal usageInfo(var usage)
    signal sessionInfo(var info)
    signal finished()
    signal failed(string message)

    // Prefer the API's human-readable detail over raw JSON in error messages.
    function _errorText(xhr) {
        if (xhr.status === 0)
            return "server unreachable";
        try {
            var detail = JSON.parse(xhr.responseText).detail;
            if (detail)
                return "" + detail;
        } catch (e) {}
        return "" + xhr.status + " " + xhr.responseText;
    }

    function _get(path, onOk, onErr) {
        var xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            if (xhr.status === 200)
                onOk(JSON.parse(xhr.responseText));
            else
                onErr(_errorText(xhr));
        };
        xhr.open("GET", serverUrl + path);
        if (apiToken !== "")
            xhr.setRequestHeader("X-Api-Token", apiToken);
        xhr.timeout = 15000;
        xhr.send();
    }

    function _post(path, body, onOk, onErr, timeoutMs) {
        var xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            if (xhr.status === 200)
                onOk(JSON.parse(xhr.responseText));
            else
                onErr(_errorText(xhr));
        };
        xhr.open("POST", serverUrl + path);
        xhr.setRequestHeader("Content-Type", "application/json");
        if (apiToken !== "")
            xhr.setRequestHeader("X-Api-Token", apiToken);
        xhr.timeout = timeoutMs !== undefined ? timeoutMs : 120000;
        xhr.send(body !== null ? JSON.stringify(body) : "");
    }

    function refresh() {
        // Short timeout: a wedged server must show as unreachable quickly,
        // not leave the panel blank for the two-minute job timeout.
        _post("/api/refresh", null, function(data) { api.refreshed(data); },
              function(msg) { api.refreshFailed(msg); }, 15000);
    }

    function fetchHistory(docId) {
        _get("/api/history/" + docId, function(data) { api.historyLoaded(data.turns); },
             function(msg) { api.failed("history failed: " + msg); });
    }

    function clearSession(docId) {
        _post("/api/session/" + docId + "/clear", null,
              function(data) { api.sessionCleared(data); },
              function(msg) { api.failed("clear failed: " + msg); });
    }

    function ask(strokes, canvasW, canvasH, includeHighlights, includeDocText, brief, strongTranscribe) {
        if (busy)
            return;
        busy = true;
        jobId = "";
        cursor = 0;
        _post("/api/ask", {
            "strokes": strokes,
            "canvas_w": canvasW,
            "canvas_h": canvasH,
            "include_highlights": includeHighlights,
            "include_doc_text": includeDocText,
            "brief": brief === true,
            "strong_transcribe": strongTranscribe === true
        }, function(resp) {
            api.jobId = resp.job_id;
            api.syncAge(resp.sync_age_seconds);
            pollTimer.start();
        }, function(msg) {
            api.busy = false;
            api.failed(msg);
        });
    }

    function pageAsk(includeHighlights, brief) {
        if (busy)
            return;
        busy = true;
        jobId = "";
        cursor = 0;
        _post("/api/ask/page", {
            "include_highlights": includeHighlights,
            "brief": brief === true
        }, function(resp) {
            api.jobId = resp.job_id;
            api.syncAge(resp.sync_age_seconds);
            pollTimer.start();
        }, function(msg) {
            api.busy = false;
            api.failed(msg);
        });
    }

    function quick(action, docId, brief) {
        if (busy)
            return;
        busy = true;
        jobId = "";
        cursor = 0;
        _post("/api/quick", {
            "action": action,
            "doc_id": docId !== "" ? docId : null,
            "brief": brief === true
        }, function(resp) {
            api.jobId = resp.job_id;
            api.syncAge(resp.sync_age_seconds);
            pollTimer.start();
        }, function(msg) {
            api.busy = false;
            api.failed(msg);
        });
    }

    function quizStart(docId) {
        if (busy)
            return;
        busy = true;
        jobId = "";
        cursor = 0;
        _post("/api/quiz", {
            "doc_id": docId !== "" ? docId : null
        }, function(resp) {
            api.jobId = resp.job_id;
            api.syncAge(resp.sync_age_seconds);
            pollTimer.start();
        }, function(msg) {
            api.busy = false;
            api.failed(msg);
        });
    }

    function quizAnswer(strokes, canvasW, canvasH, docId) {
        if (busy)
            return;
        busy = true;
        jobId = "";
        cursor = 0;
        _post("/api/quiz/answer", {
            "strokes": strokes,
            "canvas_w": canvasW,
            "canvas_h": canvasH,
            "doc_id": docId !== "" ? docId : null
        }, function(resp) {
            api.jobId = resp.job_id;
            api.syncAge(resp.sync_age_seconds);
            pollTimer.start();
        }, function(msg) {
            api.busy = false;
            api.failed(msg);
        });
    }

    function search(strokes, canvasW, canvasH) {
        if (busy || searching)
            return;
        searching = true;
        _post("/api/search", {
            "strokes": strokes,
            "canvas_w": canvasW,
            "canvas_h": canvasH
        }, function(data) {
            api.searching = false;
            api.searchResults(data);
        }, function(msg) {
            api.searching = false;
            api.failed("search failed: " + msg);
        });
    }

    function cancel() {
        if (jobId === "")
            return;
        pollTimer.stop();
        busy = false;
        _post("/api/answer/" + jobId + "/cancel", null,
              function(resp) { api.cancelled(); },
              function(msg) { api.cancelled(); });
        jobId = "";
    }

    function anki(docId) {
        if (busy)
            return;
        busy = true;
        jobId = "";
        cursor = 0;
        _post("/api/anki/" + docId, null, function(resp) {
            api.jobId = resp.job_id;
            pollTimer.start();
        }, function(msg) {
            api.busy = false;
            api.failed(msg);
        });
    }

    function compactSession(docId) {
        if (busy)
            return;
        busy = true;
        jobId = "";
        cursor = 0;
        _post("/api/session/" + docId + "/compact", null, function(resp) {
            api.jobId = resp.job_id;
            pollTimer.start();
        }, function(msg) {
            api.busy = false;
            api.failed(msg);
        });
    }

    function exportNotes(docId) {
        _post("/api/export/" + docId + "?push=true", null,
              function(data) { api.exported(data); },
              function(msg) { api.failed("export failed: " + msg); });
    }

    Timer {
        id: pollTimer
        interval: 1500
        repeat: true
        onTriggered: api.poll()
    }

    function poll() {
        if (jobId === "")
            return;
        _get("/api/answer/" + jobId + "?cursor=" + cursor, function(snap) {
            if (snap.phase)
                api.phaseChanged(snap.phase);
            if (snap.question_read)
                api.questionRead(snap.question_read);
            if (snap.text_so_far && snap.text_so_far.length > 0) {
                api.textAppended(snap.text_so_far);
                api.cursor = snap.next_cursor;
            }
            if (snap.session)
                api.sessionInfo(snap.session);
            if (snap.status === "done") {
                pollTimer.stop();
                api.busy = false;
                if (snap.usage)
                    api.usageInfo(snap.usage);
                api.finished();
            } else if (snap.status === "error") {
                pollTimer.stop();
                api.busy = false;
                api.failed(snap.error || "LLM error");
            }
        }, function(msg) {
            pollTimer.stop();
            api.busy = false;
            api.failed("poll failed: " + msg);
        });
    }
}
