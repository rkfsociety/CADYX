"""Maintain a validated agent lease while one admitted command is running."""
from __future__ import annotations

import threading
from typing import Any

from .errors import ConflictError


class LeaseHeartbeat:
    def __init__(self, core: Any, project_id: str, lease_id: str) -> None:
        self._core = core
        self._project_id = project_id
        self._lease_id = lease_id
        timeout = core.leases.timeout_seconds
        self._interval = max(0.001, min(timeout / 3.0, 30.0))
        self._stopped = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._failure: BaseException | None = None

    def start(self) -> None:
        # Starting twice would orphan the first worker; starting after stop
        # would silently run without any lease renewals.
        with self._lifecycle_lock:
            if self._stopped.is_set():
                raise RuntimeError("Cannot start a stopped CADYX lease heartbeat.")
            if self._thread is not None:
                raise RuntimeError("CADYX lease heartbeat has already started.")
            thread = threading.Thread(target=self._run, name="cadyx-agent-heartbeat", daemon=True)
            thread.start()
            self._thread = thread

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stopped.set()
            thread = self._thread
        if thread is not None:
            thread.join()

    def check(self) -> None:
        if self._failure is not None:
            raise RuntimeError("The CADYX agent lease heartbeat failed.") from self._failure

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            try:
                self._core.heartbeat(self._project_id, self._lease_id)
            except ConflictError as exc:
                if exc.code == "PROJECT_BUSY":
                    continue
                self._failure = exc
                return
            except BaseException as exc:
                self._failure = exc
                return
