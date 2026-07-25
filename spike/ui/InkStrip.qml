import QtQuick 2.15

// One test strip: draws polylines from whatever input its chosen handler receives.
// Ink is drawn with Canvas: xochitl's e-ink renderer does not rasterize
// QtQuick.Shapes items, so vector Shape strokes never appear on the display.
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
        ink.requestPaint();
        event(info);
    }
    function extendStroke(x, y) {
        var c = current.slice();
        c.push({ "x": x, "y": y });
        current = c;
        ink.requestPaint();
    }
    function endStroke() {
        if (current.length > 0) {
            var s = strokes.slice();
            s.push(current);
            strokes = s;
            current = [];
            ink.requestPaint();
        }
    }
    function clearInk() {
        strokes = [];
        current = [];
        ink.requestPaint();
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

    Canvas {
        id: ink
        anchors.fill: parent
        renderStrategy: Canvas.Immediate
        renderTarget: Canvas.Image
        onPaint: {
            var ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            ctx.strokeStyle = "black";
            ctx.lineWidth = 3;
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            var all = strip.strokes.slice();
            if (strip.current.length > 0)
                all.push(strip.current);
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
