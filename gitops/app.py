from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gitops import __version__
from gitops.services.diagnostics import setup_logging
from gitops.ui import theme
from gitops.ui.main_window import MainWindow

ICON = Path(__file__).parent / "resources" / "gitops.png"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("GitOps")
    app.setOrganizationName("gitops")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(ICON)))
    # Matches packaging/linux/gitops.desktop, so Wayland docks show the right icon.
    app.setDesktopFileName("gitops")
    setup_logging()
    theme.apply(str(QSettings().value("theme", "system")))
    theme.follow_system_changes()
    window = MainWindow()
    if len(argv) > 1:
        window.open_repo(argv[1])
    window.show()
    window.check_for_updates()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
