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
    function strokesForJson() {
        var all = strokes.slice();
        if (current.length > 1)
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
                    if (pts.length < 2)
                        continue;
                    ctx.beginPath();
                    ctx.moveTo(pts[0].x, pts[0].y);
                    for (var j = 1; j < pts.length; j++)
                        ctx.lineTo(pts[j].x, pts[j].y);
                    ctx.stroke();
                }
            } else {
                for (var k = 0; k < pending.length; k++) {
                    var seg = pending[k];
                    ctx.beginPath();
                    ctx.moveTo(seg[0], seg[1]);
                    ctx.lineTo(seg[2], seg[3]);
                    ctx.stroke();
                }
                pending = [];
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        onPressed: (mouse) => { pad.current = [{ "x": mouse.x, "y": mouse.y }]; }
        onPositionChanged: (mouse) => {
            // Append in place: reassigning the property here would cost an
            // O(n) array copy plus change notifications at pen input rate.
            var c = pad.current;
            var prev = c.length > 0 ? c[c.length - 1] : null;
            c.push({ "x": mouse.x, "y": mouse.y });
            if (prev !== null)
                ink.drawSegment(prev.x, prev.y, mouse.x, mouse.y);
        }
        onReleased: {
            if (pad.current.length > 1) {
                var s = pad.strokes.slice();
                s.push(pad.current);
                pad.strokes = s;
            }
            pad.current = [];
        }
    }

    // Lowest-latency e-ink waveform while writing (UFast trades ink quality
    // for the response time native inking gets).
    DisplayMethodArea {
        anchors.fill: parent
        displayMethod: DisplayMethodArea.UFast
    }
}
