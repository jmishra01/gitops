import logging
import os
import sys
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import qInstallMessageHandler, qWarning  # noqa: E402

from gitops import __version__  # noqa: E402
from gitops.core.git_backend import GitError  # noqa: E402
from gitops.services import diagnostics  # noqa: E402
from gitops.services.task_runner import Task  # noqa: E402


@pytest.fixture
def logs(tmp_path):
    hooks = sys.excepthook, threading.excepthook
    path = diagnostics.setup_logging(tmp_path / "logs")
    yield path
    for handler in list(diagnostics.log.handlers):
        diagnostics.log.removeHandler(handler)
        handler.close()
    sys.excepthook, threading.excepthook = hooks
    qInstallMessageHandler(None)


def test_setup_writes_a_startup_line(logs):
    assert logs.name == "gitops.log"
    assert diagnostics.log_file() == logs
    assert f"GitOps {__version__} started" in logs.read_text()


def test_uncaught_exceptions_and_qt_warnings_are_logged(logs, monkeypatch):
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: None)
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    qWarning("a qt warning")
    text = logs.read_text()
    assert "Uncaught exception" in text and "ValueError: boom" in text
    assert "a qt warning" in text


def test_failed_git_task_logs_subcommand_only(logs):
    def fail():
        raise GitError(["commit", "-m", "secret message"], 1, "nothing to commit")

    Task(fail).run()
    text = logs.read_text()
    assert "git commit failed (1): nothing to commit" in text
    assert "secret message" not in text


def test_other_task_errors_keep_the_traceback(logs):
    def broken():
        raise KeyError("missing")

    Task(broken).run()
    text = logs.read_text()
    assert "Background task" in text and "Traceback" in text


def test_diagnostics_text_has_versions_and_log_tail(logs):
    logging.getLogger("gitops.test").warning("last thing that happened")
    text = diagnostics.diagnostics_text()
    assert text.startswith(f"GitOps {__version__}")
    assert "PySide6" in text and "Git git version" in text
    assert "last thing that happened" in text


def test_log_rotates(tmp_path, monkeypatch, logs):
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 2000)
    path = diagnostics.setup_logging(tmp_path / "small")
    for i in range(200):
        diagnostics.log.info("line %d %s", i, "x" * 40)
    assert path.exists() and (path.parent / "gitops.log.1").exists()
    assert not (path.parent / f"gitops.log.{diagnostics.BACKUPS + 1}").exists()
