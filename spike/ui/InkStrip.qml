import QtQuick 2.15
import QtQuick.Shapes 1.15

// One test strip: draws polylines from whatever input its chosen handler receives.
Rectangle {
    id: strip
    property string label: ""
    property string mode: "mouse"   // mouse | point | touch
    signal event(string info)

    property var strokes: []        // finished strokes: list of list of {x, y}
    property var current: []        // in-progress stroke

    border.width: 2
    border.color: "black"
    color: "white"
    clip: true

    function startStroke(x, y, info) {
        current = [{ "x": x, "y": y }];
        event(info);
    }
    function extendStroke(x, y) {
        var c = current.slice();
        c.push({ "x": x, "y": y });
        current = c;
    }
    function endStroke() {
        if (current.length > 0) {
            var s = strokes.slice();
            s.push(current);
            strokes = s;
            current = [];
        }
    }
    function clearInk() {
        strokes = [];
        current = [];
    }

    Text {
        text: strip.label
        font.pixelSize: 22
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: 8
        color: "#666666"
        z: 2
    }

    Repeater {
        model: strip.strokes.length + (strip.current.length > 0 ? 1 : 0)
        delegate: Shape {
            anchors.fill: parent
            property var pts: index < strip.strokes.length ? strip.strokes[index] : strip.current
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
        enabled: strip.mode === "mouse"
        onPressed: (mouse) => strip.startStroke(mouse.x, mouse.y, strip.label + ": MouseArea pressed")
        onPositionChanged: (mouse) => strip.extendStroke(mouse.x, mouse.y)
        onReleased: strip.endStroke()
    }

    PointHandler {
        id: pointHandler
        enabled: strip.mode === "point"
        acceptedDevices: PointerDevice.AllDevices
        onActiveChanged: {
            if (active) {
                strip.startStroke(point.position.x, point.position.y,
                    strip.label + ": PointHandler device=" + (point.device ? point.device.pointerType : "?"))
            } else {
                strip.endStroke();
            }
        }
        onPointChanged: {
            if (active)
                strip.extendStroke(point.position.x, point.position.y);
        }
    }

    MultiPointTouchArea {
        anchors.fill: parent
        enabled: strip.mode === "touch"
        maximumTouchPoints: 1
        onPressed: (points) => {
            if (points.length > 0)
                strip.startStroke(points[0].x, points[0].y, strip.label + ": MultiPointTouchArea pressed")
        }
        onUpdated: (points) => {
            if (points.length > 0)
                strip.extendStroke(points[0].x, points[0].y)
        }
        onReleased: strip.endStroke()
    }
}
