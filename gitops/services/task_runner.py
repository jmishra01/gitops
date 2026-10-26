"""Run git work off the UI thread.

Every git call goes through here so the window never freezes. A task is
either a plain function (result delivered once) or a generator (items are
delivered in small batches as they arrive, and the task can be cancelled).
"""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


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

    def run(self) -> None:
        try:
            out = self.fn(*self.args, **self.kwargs)
            if inspect.isgenerator(out):
                self._drain(out)
            elif not self.cancelled:
                self.signals.result.emit(out)
        except Exception as exc:  # surfaced in the UI, never crashes the app
            if not self.cancelled:
                self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()

    def _drain(self, gen) -> None:
        batch: list[Any] = []
        last_emit = time.monotonic()
        try:
            for item in gen:
                if self.cancelled:
                    return
                batch.append(item)
                if time.monotonic() - last_emit >= self.BATCH_INTERVAL:
                    self.signals.items.emit(batch)
                    batch, last_emit = [], time.monotonic()
            if batch:
                self.signals.items.emit(batch)
        finally:
            gen.close()  # kills the git process if we stopped early


class TaskRunner(QObject):
    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self._active: set[Task] = set()

    def submit(self, task: Task) -> Task:
        self._active.add(task)
        task.signals.finished.connect(lambda: self._active.discard(task))
        self.pool.start(task)
        return task

    def cancel_all(self) -> None:
        for task in list(self._active):
            task.cancel()
