"""Run the tablet app's QML on the Mac against a local companion server.

Interactive mode (default): opens a window, draw with the mouse, click Ask.
    uvx --from pyside6-essentials python scripts/simulator.py

Automated mode (--test): offscreen; injects "HI" strokes, clicks Ask, waits
for the answer, prints results. Full-stack E2E without the tablet.
    uvx --from pyside6-essentials python scripts/simulator.py --test --server-url http://localhost:8123
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_UI = ROOT / "device-app" / "ui"
STUBS = Path(__file__).resolve().parent / "qml-stubs"


def make_hi_strokes(w: float, h: float) -> list:
    """Stroke polylines drawing the letters HI, scaled to the pad size."""
    def seg(x1, y1, x2, y2, n=12):
        return [
            {"x": x1 + (x2 - x1) * i / n, "y": y1 + (y2 - y1) * i / n}
            for i in range(n + 1)
        ]

    u = min(w, h) * 0.5
    x0, y0 = w * 0.25, h * 0.25
    return [
        seg(x0, y0, x0, y0 + u),                       # H left
        seg(x0 + u * 0.6, y0, x0 + u * 0.6, y0 + u),   # H right
        seg(x0, y0 + u / 2, x0 + u * 0.6, y0 + u / 2), # H bar
        seg(x0 + u * 1.1, y0, x0 + u * 1.1, y0 + u),   # I stem
        seg(x0 + u * 0.9, y0, x0 + u * 1.3, y0),       # I top
        seg(x0 + u * 0.9, y0 + u, x0 + u * 1.3, y0 + u),  # I bottom
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="automated offscreen run")
    parser.add_argument("--server-url", default="http://localhost:8000")
    parser.add_argument("--api-token", default="")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    import os

    if args.test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QMetaObject, QPoint, Qt, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView
    from PySide6.QtTest import QTest

    config_in = APP_UI / "Config.qml.in"
    (APP_UI / "Config.qml").write_text(
        config_in.read_text()
        .replace("@SERVER_URL@", args.server_url)
        .replace("@API_TOKEN@", args.api_token)
    )

    app = QGuiApplication(sys.argv)
    view = QQuickView()
    view.engine().addImportPath(str(STUBS))
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setSource(QUrl.fromLocalFile(str(APP_UI / "main.qml")))
    if view.status() == QQuickView.Status.Error:
        for err in view.errors():
            print(err.toString())
        return 1
    view.resize(760, 1010)  # tablet aspect ratio, scaled down
    view.setTitle("reMarkable AI Assistant (simulator)")
    view.show()

    if not args.test:
        return app.exec()

    root = view.rootObject()
    app.processEvents()

    scratchpad = root.findChild(object, "scratchpad")
    ask_area = root.findChild(object, "askButton")
    answer_view = root.findChild(object, "answerView")
    assert scratchpad and ask_area and answer_view, "objectNames not found"

    strokes = make_hi_strokes(scratchpad.width(), scratchpad.height())
    scratchpad.setProperty("strokes", strokes)
    app.processEvents()
    assert scratchpad.property("hasInk"), "stroke injection failed"
    print(f"injected {len(strokes)} strokes into scratchpad")

    center = ask_area.mapToScene(
        ask_area.boundingRect().center()
    ).toPoint()
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(center))
    print("clicked Ask, waiting for answer...")

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        app.processEvents()
        status = root.property("status")
        answer = answer_view.property("fullText")
        if status and (status.startswith("Q:") or status.startswith("Error")) and answer:
            break
        if status and status.startswith("Error"):
            break
        time.sleep(0.2)

    status = root.property("status")
    answer = answer_view.property("fullText") or ""
    question = root.property("questionRead") or ""
    pages = answer_view.property("pages")
    if hasattr(pages, "toVariant"):
        pages = pages.toVariant()
    print(f"status: {status}")
    print(f"question_read: {question!r}")
    print(f"answer ({len(answer)} chars, {len(pages) if pages else 0} page(s)): {answer[:400]}")

    # Open the history overlay and verify it shows this conversation.
    history_button = root.findChild(object, "historyButton")
    history_view = root.findChild(object, "historyView")
    center = history_button.mapToScene(history_button.boundingRect().center()).toPoint()
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(center))
    history_deadline = time.monotonic() + 15
    while time.monotonic() < history_deadline and not history_view.property("visible"):
        app.processEvents()
        time.sleep(0.1)
    history_text = history_view.property("historyText") or ""
    history_ok = history_view.property("visible") and question in history_text
    print(f"history overlay: visible={bool(history_view.property('visible'))}, "
          f"{len(history_text)} chars, contains question: {question in history_text}")

    ok = bool(answer) and not str(status).startswith("Error") and history_ok
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
