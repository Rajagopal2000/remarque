"""Validate app QML on the Mac by loading it with PySide6 (offscreen).

Uses a stub net.asivery.ApploadUtils module so device-only imports resolve.
Run via: uvx --from pyside6-essentials python scripts/check-qml.py <app-dir>...
"""

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlComponent, QQmlEngine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STUBS = Path(__file__).resolve().parent / "qml-stubs"


def check_app(app_dir: Path) -> bool:
    config_in = app_dir / "ui" / "Config.qml.in"
    config_out = app_dir / "ui" / "Config.qml"
    config_out.write_text(
        config_in.read_text()
        .replace("@SERVER_URL@", "http://localhost:8000")
        .replace("@API_TOKEN@", "")
    )

    engine = QQmlEngine()
    engine.addImportPath(str(STUBS))
    component = QQmlComponent(engine)
    component.loadUrl(str(app_dir / "ui" / "main.qml"))
    if component.isError():
        print(f"FAIL {app_dir.name}:")
        for err in component.errors():
            print(f"  {err.toString()}")
        return False
    obj = component.create()
    ok = obj is not None
    if not ok:
        print(f"FAIL {app_dir.name}: component created no object")
        for err in component.errors():
            print(f"  {err.toString()}")
    else:
        print(f"OK   {app_dir.name}")
    return ok


def main() -> int:
    app = QGuiApplication([])
    dirs = [Path(a) for a in sys.argv[1:]] or [ROOT / "spike", ROOT / "device-app"]
    results = [check_app(d) for d in dirs]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
