from __future__ import annotations

"""LiteConvert application entrypoint."""

import sys
from PyQt5.QtWidgets import QApplication

from .settings import SettingsManager
from .ui import LiteConvertWindow


def main() -> int:
    app = QApplication(sys.argv)
    settings = SettingsManager()
    settings.load()
    win = LiteConvertWindow(settings)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())


