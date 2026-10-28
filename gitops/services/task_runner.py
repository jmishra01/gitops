"""Run git work off the UI thread.

Every git call goes through here so the window never freezes. A task is
either a plain function (result delivered once) or a generator (items are
delivered in small batches as they arrive, and the task can be cancelled).
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from gitops.core.git_backend import GitError

log = logging.getLogger(__name__)


class TaskSignals(QObject):
    items = Signal(list)  # a batch of streamed items
    result = Signal(object)  # return value of a plain function
    error = Signal(str)
    finished = Signal()


class Task(QRunnable):
    BATCH_INTERVAL = 0.1  # seconds; keeps the UI responsive on huge result sets

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = TaskSignals()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _emit(self, signal_name: str, *args: Any) -> bool:
        """Emit unless the owner (and with it our signals object) is already gone."""
        try:
            getattr(self.signals, signal_name).emit(*args)
            return True
        except RuntimeError:  # C++ object deleted: the window closed mid-task
            self._cancelled.set()
            return False

    def run(self) -> None:
        try:
            out = self.fn(*self.args, **self.kwargs)
            if inspect.isgenerator(out):
                self._drain(out)
            elif not self.cancelled:
                self._emit("result", out)
        except Exception as exc:  # surfaced in the UI, never crashes the app
            if isinstance(exc, GitError):
                # The subcommand only: the full arguments can hold commit messages or paths.
                subcommand = exc.git_args[0] if exc.git_args else "?"
                log.warning("git %s failed (%s): %s", subcommand, exc.returncode, exc.stderr[:500])
            else:
                log.exception("Background task %s failed", getattr(self.fn, "__qualname__", self.fn))
            if not self.cancelled:
                self._emit("error", str(exc))
        finally:
            self._emit("finished")

    def _drain(self, gen) -> None:
        batch: list[Any] = []
        last_emit = time.monotonic()
        try:
            for item in gen:
                if self.cancelled:
                    return
                batch.append(item)
                if time.monotonic() - last_emit >= self.BATCH_INTERVAL:
                    if not self._emit("items", batch):
                        return
                    batch, last_emit = [], time.monotonic()
            if batch:
                self._emit("items", batch)
        finally:
            gen.close()  # kills the git process if we stopped early


class TaskRunner(QObject):
    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self._active: set[Task] = set()

    def submit(self, task: Task) -> Task:
        # Parenting the signals object to the runner (owned by the window) means
        # Qt discards queued results, and their connections, if the window goes
        # away before the task finishes.
        task.signals.setParent(self)
        self._active.add(task)
        task.signals.finished.connect(lambda: self._finished(task))
        self.pool.start(task)
        return task

    def _finished(self, task: Task) -> None:
        self._active.discard(task)
        # Hand the signals object back to Python so it's freed with its Task, exactly once.
        # (deleteLater here could fire after the runner itself had deleted it: a double delete.)
        task.signals.setParent(None)

    def cancel_all(self) -> None:
        for task in list(self._active):
            task.cancel()
