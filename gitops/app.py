from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from gitops.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("GitOps")
    app.setOrganizationName("gitops")
    window = MainWindow()
    if len(argv) > 1:
        window.open_repo(argv[1])
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
