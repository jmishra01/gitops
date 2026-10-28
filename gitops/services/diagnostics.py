"""Log to a rotating file and collect diagnostics for bug reports.

The log lives in the platform's app data folder (for example
~/Library/Application Support/GitOps/logs on macOS). It records errors and
crashes, never file contents or commit messages, so it is safe to attach to
an issue.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QStandardPaths, QtMsgType, qInstallMessageHandler, qVersion

from gitops import __version__

LOG_NAME = "gitops.log"
MAX_BYTES = 1_000_000
BACKUPS = 3
TAIL_LINES = 50

log = logging.getLogger("gitops")

_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


def log_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base or Path.home() / ".gitops") / "logs"


def log_file() -> Path | None:
    """The file the gitops logger writes to, or None before setup_logging."""
    for handler in log.handlers:
        if isinstance(handler, RotatingFileHandler):
            return Path(handler.baseFilename)
    return None


def setup_logging(directory: Path | None = None, level: int = logging.INFO) -> Path:
    """Send the gitops logger, uncaught exceptions and Qt warnings to a rotating file."""
    directory = directory or log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOG_NAME
    for handler in [h for h in log.handlers if isinstance(h, RotatingFileHandler)]:
        log.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(level)

    def on_uncaught(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = on_uncaught
    threading.excepthook = lambda args: on_uncaught(args.exc_type, args.exc_value, args.exc_traceback)
    qInstallMessageHandler(lambda kind, _context, message: log.log(_QT_LEVELS.get(kind, logging.INFO), message))
    log.info("GitOps %s started on %s", __version__, platform.platform())
    return path


def git_version() -> str:
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as exc:
        return f"not found ({exc})"
    return result.stdout.strip() or result.stderr.strip()


def diagnostics_text() -> str:
    """Versions, platform and the end of the log, ready to paste into an issue."""
    path = log_file()
    lines = [
        f"GitOps {__version__}",
        f"Python {platform.python_version()} ({sys.executable})",
        f"PySide6 {pyside_version}, Qt {qVersion()}",
        f"OS {platform.platform()}",
        f"Git {git_version()}",
        f"Log {path or 'not set up'}",
    ]
    if path and path.exists():
        tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]
        lines += ["", f"Last {len(tail)} log lines:", *tail]
    return "\n".join(lines)
