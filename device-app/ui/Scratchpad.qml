import QtQuick 2.15
import net.asivery.ApploadUtils

// Handwriting capture area. Strokes are kept as point lists and sent to the
// server as JSON; the server rasterizes them for the vision model.
// Input backend is MouseArea (pen arrives as synthesized mouse events in Qt).
// If M1 shows pen events do not reach MouseArea, swap this one component.
Rectangle {
    id: pad
    color: "white"
    border.width: 2
    border.color: "black"
    clip: true

    property var strokes: []
    property var current: []
    property bool hasInk: strokes.length > 0 || current.length > 0

    function clearInk() {
        strokes = [];
        current = [];
        ink.repaintAll();
    }

    function undoStroke() {
        if (current.length > 0) {
            current = [];
        } else if (strokes.length > 0) {
            strokes = strokes.slice(0, strokes.length - 1);
        }
        ink.repaintAll();
    }

    // Strokes as [[x, y], ...] lists for the /api/ask payload.
    // Single-point strokes are kept: i-dots, periods and short cursive entry
    // taps are real ink (the server rasterizes them as dots).
    function strokesForJson() {
        var all = strokes.slice();
        if (current.length > 0)
            all.push(current);
        return all.map(function(stroke) {
            return stroke.map(function(p) { return [p.x, p.y]; });
        });
    }

    Text {
        anchors.centerIn: parent
        visible: !pad.hasInk
        text: "Write your question here"
        font.pixelSize: 28
        color: "#999999"
    }

    // Ink is drawn with Canvas: xochitl's e-ink renderer does not rasterize
    // QtQuick.Shapes items, so vector Shape strokes never appear on the display.
    // While writing, only the new pen segment is painted and only its small
    // dirty rect is flushed to the e-ink; full repaints happen on undo/clear.
    Canvas {
        id: ink
        anchors.fill: parent
        renderStrategy: Canvas.Immediate
        renderTarget: Canvas.Image
        property bool fullRepaint: true
        property var pending: []   // line segments [x1, y1, x2, y2] not yet drawn

        // A resize reallocates the backing image, which can come up holding
        // stale frame content; repaint from the stroke lists.
        onWidthChanged: repaintAll()
        onHeightChanged: repaintAll()

        function drawDot(ctx, x, y) {
            ctx.beginPath();
            ctx.arc(x, y, ctx.lineWidth / 2 + 0.5, 0, 2 * Math.PI);
            ctx.fillStyle = "black";
            ctx.fill();
        }
        function drawSegment(x1, y1, x2, y2) {
            pending.push([x1, y1, x2, y2]);
            var m = 4;
            markDirty(Qt.rect(Math.min(x1, x2) - m, Math.min(y1, y2) - m,
                              Math.abs(x2 - x1) + 2 * m, Math.abs(y2 - y1) + 2 * m));
        }
        function repaintAll() {
            fullRepaint = true;
            requestPaint();
        }
        onPaint: {
            var ctx = getContext("2d");
            ctx.strokeStyle = "black";
            ctx.lineWidth = 3;
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            if (fullRepaint) {
                fullRepaint = false;
                pending = [];
                ctx.clearRect(0, 0, width, height);
                var all = pad.strokes.slice();
                if (pad.current.length > 0)
                    all.push(pad.current);
                for (var i = 0; i < all.length; i++) {
                    var pts = all[i];
                    if (pts.length === 0)
                        continue;
                    if (pts.length === 1) {
                        drawDot(ctx, pts[0].x, pts[0].y);
                        continue;
                    }
                    ctx.beginPath();
                    ctx.moveTo(pts[0].x, pts[0].y);
                    for (var j = 1; j < pts.length; j++)
                        ctx.lineTo(pts[j].x, pts[j].y);
                    ctx.stroke();
                }
            } else {
                for (var k = 0; k < pending.length; k++) {
                    var seg = pending[k];
                    if (seg[0] === seg[2] && seg[1] === seg[3]) {
                        drawDot(ctx, seg[0], seg[1]);
                        continue;
                    }
                    ctx.beginPath();
                    ctx.moveTo(seg[0], seg[1]);
                    ctx.lineTo(seg[2], seg[3]);
                    ctx.stroke();
                }
                pending = [];
            }
        }
    }

    // Append a point to the in-flight stroke if it moved at least a pixel
    // (filters duplicate events).
    function addPoint(x, y) {
        var c = current;
        var prev = c[c.length - 1];
        var dx = x - prev.x, dy = y - prev.y;
        if (dx * dx + dy * dy < 1)
            return;
        c.push({ "x": x, "y": y });
        ink.drawSegment(prev.x, prev.y, x, y);
    }

    // Remove every stroke that passes near (x, y): the Marker's tail eraser.
    function eraseAt(x, y) {
        var r = 24;
        var kept = [];
        for (var i = 0; i < strokes.length; i++) {
            var hit = false;
            var pts = strokes[i];
            for (var j = 0; j < pts.length; j++) {
                var dx = pts[j].x - x, dy = pts[j].y - y;
                if (dx * dx + dy * dy <= r * r) {
                    hit = true;
                    break;
                }
            }
            if (!hit)
                kept.push(pts);
        }
        if (kept.length !== strokes.length) {
            strokes = kept;
            ink.repaintAll();
        }
    }

    // Erase mode: the pen removes strokes instead of drawing. A panel toggle
    // drives this; the Marker's tail eraser is indistinguishable from the tip
    // in a windowed app (verified: no distinct button or pointer type arrives).
    property bool eraseMode: false
    onHasInkChanged: if (!hasInk) eraseMode = false

    property double _t0: 0
    property bool _erasing: false

    function beginStroke(x, y) {
        _t0 = Date.now();
        current = [{ "x": x, "y": y }];
        // Ink from the very first event: a fast cursive entry stroke gets
        // its start point on screen before the first move arrives.
        ink.drawSegment(x, y, x, y);
    }

    function endStroke(src) {
        if (current.length > 0) {
            var s = strokes.slice();
            s.push(current);
            strokes = s;
            // firstGap: distance between the press point and the first move.
            // The device consistently shows ~20px here via the mouse path:
            // the input pipeline eats the start of every stroke.
            var pts = s[s.length - 1];
            var gap = 0;
            if (pts.length > 1) {
                var dx = pts[1].x - pts[0].x, dy = pts[1].y - pts[0].y;
                gap = Math.sqrt(dx * dx + dy * dy);
            }
            console.log("remarque ink[" + src + "]: pts=" + pts.length
                        + " ms=" + (Date.now() - _t0)
                        + " firstGap=" + gap.toFixed(1));
        }
        current = [];
    }

    // Capture: the pen tip as synthesized mouse events, full digitizer rate
    // (the first ~20px of each stroke are lost upstream in xochitl's input
    // pipeline; verified unavoidable - the pen never arrives as touch or
    // stylus events, so no alternative backend sees more).
    MouseArea {
        anchors.fill: parent
        onPressed: (mouse) => {
            if (pad.eraseMode) {
                pad._erasing = true;
                pad.eraseAt(mouse.x, mouse.y);
                return;
            }
            pad.beginStroke(mouse.x, mouse.y);
        }
        onPositionChanged: (mouse) => {
            if (pad._erasing)
                pad.eraseAt(mouse.x, mouse.y);
            else if (pad.current.length > 0)
                pad.addPoint(mouse.x, mouse.y);
        }
        onReleased: {
            if (pad._erasing) {
                pad._erasing = false;
                return;
            }
            pad.endStroke("mouse");
        }
    }


    // Lowest-latency e-ink waveform while writing (UFast trades ink quality
    // for the response time native inking gets).
    DisplayMethodArea {
        anchors.fill: parent
        displayMethod: DisplayMethodArea.UFast
    }
}
