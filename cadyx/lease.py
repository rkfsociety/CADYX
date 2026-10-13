"""In-process edit leases with stale-lease recovery instead of silent release."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Callable
from uuid import uuid4

from .errors import ConflictError, LeaseRequiredError, ValidationError
from .models import EditLease, OwnerType, lock_state_for


class EditLeaseManager:
    def __init__(self, *, timeout_seconds: float = 60.0, clock: Callable[[], float] = time.time) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._leases: dict[str, EditLease] = {}
        self._guard = threading.RLock()

    def _expired(self, lease: EditLease) -> bool:
        return self._clock() - lease.heartbeat_at > self.timeout_seconds

    def snapshot(self, project_id: str) -> dict[str, object]:
        with self._guard:
            lease = self._leases.get(project_id)
            expired = lease is not None and self._expired(lease)
            return {
                "state": lock_state_for(lease).value,
                "lease": lease.as_dict(expired=expired) if lease else None,
                "recovery_required": bool(expired),
            }

    def get(self, project_id: str) -> EditLease | None:
        with self._guard:
            return self._leases.get(project_id)

    def acquire(self, project_id: str, owner_type: str, owner_id: str) -> EditLease:
        try:
            owner = OwnerType(owner_type)
        except (TypeError, ValueError) as exc:
            raise ValidationError("owner_type must be agent, human, or system.") from exc
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 160:
            raise ValidationError("owner_id must be a non-empty string of at most 160 characters.")

        with self._guard:
            current = self._leases.get(project_id)
            if current is not None:
                if self._expired(current):
                    raise ConflictError(
                        "The previous edit lease expired; validate the saved project before recovery.",
                        code="LEASE_RECOVERY_REQUIRED",
                    )
                raise ConflictError(
                    f"Project is already locked by {current.owner_type.value} '{current.owner_id}'.",
                    code="PROJECT_LOCKED",
                )
            now = self._clock()
            lease = EditLease(project_id, owner, owner_id.strip(), now, now, str(uuid4()))
            self._leases[project_id] = lease
            return lease

    def require(self, project_id: str, lease_id: str | None) -> EditLease:
        if not isinstance(lease_id, str) or not lease_id:
            raise LeaseRequiredError("A current edit lease is required.")
        with self._guard:
            lease = self._leases.get(project_id)
            if lease is None:
                raise LeaseRequiredError("Project has no active edit lease.")
            if lease.lease_id != lease_id:
                raise ConflictError("The supplied lease does not own this project.", code="PROJECT_LOCKED")
            if self._expired(lease):
                raise ConflictError(
                    "Edit lease expired; validate the saved project before recovery.",
                    code="LEASE_EXPIRED",
                )
            return lease

    def heartbeat(self, project_id: str, lease_id: str) -> EditLease:
        lease = self.require(project_id, lease_id)
        with self._guard:
            updated = replace(lease, heartbeat_at=self._clock())
            self._leases[project_id] = updated
            return updated

    def release(self, project_id: str, lease_id: str) -> EditLease:
        self.require(project_id, lease_id)
        with self._guard:
            return self._leases.pop(project_id)

    def release_expired(self, project_id: str) -> EditLease:
        with self._guard:
            lease = self._leases.get(project_id)
            if lease is None or not self._expired(lease):
                raise ConflictError("Project has no expired lease to recover.", code="LEASE_NOT_EXPIRED")
            return self._leases.pop(project_id)
