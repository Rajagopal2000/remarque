import QtQuick 2.15
import QtQuick.Shapes 1.15
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
    }

    function undoStroke() {
        if (current.length > 0) {
            current = [];
        } else if (strokes.length > 0) {
            strokes = strokes.slice(0, strokes.length - 1);
        }
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

    Repeater {
        model: pad.strokes.length + (pad.current.length > 0 ? 1 : 0)
        delegate: Shape {
            anchors.fill: parent
            property var pts: index < pad.strokes.length ? pad.strokes[index] : pad.current
            ShapePath {
                strokeColor: "black"
                strokeWidth: 3
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                startX: pts.length > 0 ? pts[0].x : 0
                startY: pts.length > 0 ? pts[0].y : 0
                PathPolyline {
                    path: pts.map(function(p) { return Qt.point(p.x, p.y); })
                }
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        onPressed: (mouse) => { pad.current = [{ "x": mouse.x, "y": mouse.y }]; }
        onPositionChanged: (mouse) => {
            var c = pad.current.slice();
            c.push({ "x": mouse.x, "y": mouse.y });
            pad.current = c;
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

    // Low-latency e-ink refresh while writing.
    DisplayMethodArea {
        anchors.fill: parent
        displayMethod: DisplayMethodArea.Fast
    }
}
