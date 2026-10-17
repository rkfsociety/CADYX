"""Application Core shared by agent tools and the web interface."""

from __future__ import annotations

import copy
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Literal

from .commands import CommandSystem
from .core_services import (
    CommandActivityService,
    EngineeringValidationService,
    ProjectLifecycleService,
    RenderingCacheService,
    _ProjectAvailability,
)
from .errors import ConflictError, ValidationError
from .events import EventBroker
from .kernel import GeometryKernel, OCCTKernelAdapter
from .lease import EditLeaseManager
from .models import Document, OwnerType
from .rendering import render_scene
from .runtime_cache import RuntimeCachePolicy
from .scan import ScanProcessor
from .workspace import ProjectWorkspace
from .sketches import closed_profile, closed_profiles, open_path


class CadyxCore:
    def __init__(
        self,
        projects_dir: str,
        *,
        kernel: GeometryKernel | None = None,
        scan_processor: ScanProcessor | None = None,
        lease_timeout_seconds: float = 60.0,
        lease_clock: Callable[[], float] | None = None,
        cache_policy: RuntimeCachePolicy | None = None,
    ) -> None:
        self.workspace = ProjectWorkspace(projects_dir)
        self.kernel = kernel if kernel is not None else OCCTKernelAdapter()
        self.commands = CommandSystem(
            self.kernel,
            resolve_scan=self.workspace.resolve_folder_file,
            resolve_project_file=self.workspace.resolve_project_file,
            scan_processor=scan_processor,
            publish_scan_output=self.workspace.publish_scan_output,
            publish_cad_delivery=self.workspace.publish_cad_delivery,
        )
        if lease_clock is None:
            self.leases = EditLeaseManager(self.workspace._project_dir, timeout_seconds=lease_timeout_seconds)
        else:
            self.leases = EditLeaseManager(self.workspace._project_dir, timeout_seconds=lease_timeout_seconds, clock=lease_clock)
        self.events = EventBroker(dashboard_state_path=self.workspace.root / ".cadyx-agent-dashboards.json")
        self._lifecycle = ProjectLifecycleService(self)
        self._command_activity = CommandActivityService(self)
        self._rendering = RenderingCacheService(self, cache_policy=cache_policy)
        self._engineering_validation = EngineeringValidationService(self)

    @contextmanager
    def _project_guard(self, project_id: str) -> Iterator[None]:
        with self.leases.guard(project_id):
            yield

    def _project_availability(
        self,
        project_id: str,
        *,
        nonblocking: bool = False,
    ) -> _ProjectAvailability:
        return self._lifecycle._project_availability(project_id, nonblocking=nonblocking)

    def _require_project_policy(
        self,
        project_id: str,
        policy: Literal["read", "write", "file_management", "selection", "destructive"],
        message: str,
        *,
        code: str = "PROJECT_BUSY",
        valid_lease: bool = False,
        sole_active_workflow_target: bool = False,
    ) -> None:
        return self._lifecycle._require_project_policy(
            project_id,
            policy,
            message,
            code=code,
            valid_lease=valid_lease,
            sole_active_workflow_target=sole_active_workflow_target,
        )

    def _selection_project_locks(self) -> Iterator[None]:
        return self._lifecycle._selection_project_locks()

    def _assert_workspace_selection_policy(
        self,
        *,
        target_project_id: str | None = None,
        target_folder: str | None = None,
    ) -> None:
        return self._lifecycle._assert_workspace_selection_policy(
            target_project_id=target_project_id,
            target_folder=target_folder,
        )

    def _project_lifecycle_lock(self, project_id: str):
        return self._lifecycle._project_lifecycle_lock(project_id)

    def create_project(self, name: str, *, select: bool = True) -> dict[str, Any]:
        return self._lifecycle.create_project(name, select=select)

    def upload_project_step(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        return self._lifecycle.upload_project_step(project_id, filename, content)

    def copy_project_files(self, project_id: str, source_project_id: str) -> dict[str, Any]:
        return self._lifecycle.copy_project_files(project_id, source_project_id)

    def remove_staged_project_step(self, project_id: str, filename: str) -> dict[str, Any]:
        return self._lifecycle.remove_staged_project_step(project_id, filename)

    def prune_project_files(self, project_id: str, keep_files: list[str]) -> dict[str, Any]:
        return self._lifecycle.prune_project_files(project_id, keep_files)

    def rename_project(self, project_id: str, name: str) -> dict[str, Any]:
        return self._lifecycle.rename_project(project_id, name)

    def delete_project(self, project_id: str) -> dict[str, Any]:
        return self._lifecycle.delete_project(project_id)

    def list_projects(self) -> list[dict[str, Any]]:
        return self._lifecycle.list_projects()

    def list_workspace(self) -> dict[str, Any]:
        return self._lifecycle.list_workspace()

    def create_project_group(self, name: str) -> dict[str, Any]:
        return self._lifecycle.create_project_group(name)

    def assign_project_group(self, project_id: str, group_id: str | None) -> dict[str, Any]:
        return self._lifecycle.assign_project_group(project_id, group_id)

    def workspace_selection_snapshot(self) -> dict[str, str] | None:
        return self._lifecycle.workspace_selection_snapshot()

    def select_workspace_resource(self, selection: dict[str, Any]) -> dict[str, str]:
        return self._lifecycle.select_workspace_resource(selection)

    def inspect_project(self, project_id: str) -> dict[str, Any]:
        return self._lifecycle.inspect_project(project_id)

    def agent_dashboard_snapshot(self, project_id: str) -> dict[str, Any]:
        return self._command_activity.agent_dashboard_snapshot(project_id)

    def report_agent_dashboard(self, project_id: str, dashboard: dict[str, Any]) -> dict[str, Any]:
        return self._command_activity.report_agent_dashboard(project_id, dashboard)

    def acquire_lease(self, project_id: str, owner_type: str, owner_id: str) -> dict[str, Any]:
        return self._command_activity.acquire_lease(project_id, owner_type, owner_id)

    def rotate_view(
        self,
        project_id: str,
        *,
        yaw_delta: float,
        pitch_delta: float = 0.0,
        zoom_factor: float = 1.0,
        duration_ms: int = 1200,
    ) -> dict[str, Any]:
        return self._command_activity.rotate_view(
            project_id,
            yaw_delta=yaw_delta,
            pitch_delta=pitch_delta,
            zoom_factor=zoom_factor,
            duration_ms=duration_ms,
        )

    def observe_agent_operation(
        self,
        project_id: str,
        action: str,
        operation: Callable[..., Any],
        *,
        reports_progress: bool = False,
    ) -> Any:
        return self._command_activity.observe_agent_operation(
            project_id,
            action,
            operation,
            reports_progress=reports_progress,
        )

    def heartbeat(self, project_id: str, lease_id: str) -> dict[str, Any]:
        return self._command_activity.heartbeat(project_id, lease_id)

    def release_lease(self, project_id: str, lease_id: str) -> dict[str, Any]:
        return self._command_activity.release_lease(project_id, lease_id)

    def recover_expired_lease(self, project_id: str) -> dict[str, Any]:
        return self._command_activity.recover_expired_lease(project_id)

    def stop_agent(self, project_id: str, lease_id: str | None) -> dict[str, Any]:
        return self._command_activity.stop_agent(project_id, lease_id)

    def resume_agent(
        self,
        project_id: str,
        owner_id: str | None,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        return self._command_activity.resume_agent(project_id, owner_id, expected_revision)

    def execute_command(
        self,
        project_id: str,
        lease_id: str | None,
        command: dict[str, Any] | None,
        *,
        expected_owner_type: OwnerType,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        return self._command_activity.execute_command(
            project_id,
            lease_id,
            command,
            expected_owner_type=expected_owner_type,
            expected_revision=expected_revision,
        )

    def compare_bodies(
        self,
        project_id: str,
        original_body_id: str | None,
        modified_body_id: str | None,
    ) -> dict[str, Any]:
        return self._rendering.compare_bodies(project_id, original_body_id, modified_body_id)

    def render_project(self, project_id: str) -> dict[str, Any]:
        return self._rendering.render_project(project_id)

    def _scene_for_project(self, project_id: str) -> tuple[dict[str, Any], int]:
        return self._rendering._scene_for_project(project_id)

    def _remember_png_locked(self, key: tuple[str, str, int, int, int], encoded: bytes) -> None:
        return self._rendering._remember_png_locked(key, encoded)

    def _render_project_png_server(
        self,
        project_id: str,
        *,
        width: int = 1280,
        height: int = 800,
        padding: int = 56,
    ) -> tuple[bytes, int]:
        return self._rendering._render_project_png_server(
            project_id,
            width=width,
            height=height,
            padding=padding,
        )

    def render_project_thumbnail_png(self, project_id: str) -> tuple[bytes, int]:
        return self._rendering.render_project_thumbnail_png(project_id)

    def publish_viewport_snapshot(self, project_id: str, revision: int, encoded: bytes) -> dict[str, Any]:
        return self._rendering.publish_viewport_snapshot(project_id, revision, encoded)

    def render_project_png_with_source(self, project_id: str) -> tuple[bytes, str, int]:
        return self._rendering.render_project_png_with_source(project_id)

    def render_project_png(self, project_id: str) -> bytes:
        return self._rendering.render_project_png(project_id)

    def inspect_body_topology(self, project_id: str, body_id: str | None) -> dict[str, Any]:
        return self._rendering.inspect_body_topology(project_id, body_id)

    def _document_cache_key(self, document: Document) -> tuple[str, str]:
        return self._rendering._document_cache_key(document)

    def _remember_geometry_validation(self, key: tuple[str, str]) -> None:
        return self._rendering._remember_geometry_validation(key)

    def _invalidate_project_runtime_caches(self, project_id: str) -> None:
        return self._rendering._invalidate_project_runtime_caches(project_id)

    def _validate_geometry_once(self, document: Document) -> None:
        return self._rendering._validate_geometry_once(document)

    def _clone_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        return self._rendering._clone_scene(scene)

    def validate_body_dimensions(
        self,
        project_id: str,
        body_id: str,
        expected_revision: int,
        expected_dimensions: dict[str, Any],
        tolerance: float,
        *,
        persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_dimensions(
            project_id,
            body_id,
            expected_revision,
            expected_dimensions,
            tolerance,
            persist=persist,
        )

    def validate_body_clearance(
        self,
        project_id: str,
        first_body_id: str,
        second_body_id: str,
        expected_revision: int,
        minimum_clearance: float,
        tolerance: float,
        *,
        progress: Callable[[int, int], None] | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_clearance(
            project_id,
            first_body_id,
            second_body_id,
            expected_revision,
            minimum_clearance,
            tolerance,
            progress=progress,
            persist=persist,
        )

    def list_validation_records(self, project_id: str, *, limit: int = 50) -> dict[str, Any]:
        return self._engineering_validation.list_validation_records(project_id, limit=limit)

    def validate_body_preservation(
        self,
        project_id: str,
        reference_body_id: str,
        candidate_body_id: str,
        expected_revision: int,
        maximum_lost_volume: float,
        tolerance: float,
        *,
        persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_preservation(
            project_id,
            reference_body_id,
            candidate_body_id,
            expected_revision,
            maximum_lost_volume,
            tolerance,
            persist=persist,
        )

    def validate_body_thickness(
        self,
        project_id: str,
        body_id: str,
        expected_revision: int,
        minimum_thickness: float,
        tolerance: float,
        *,
        progress: Callable[[int, int], None] | None = None,
        persist: bool = False,
        method: Literal["sampled", "global_minimum"] = "sampled",
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_thickness(
            project_id,
            body_id,
            expected_revision,
            minimum_thickness,
            tolerance,
            progress=progress,
            persist=persist,
            method=method,
        )

    def _maybe_persist_validation(
        self,
        project_id: str,
        operation: str,
        result: dict[str, Any],
        *,
        persist: bool,
    ) -> dict[str, Any]:
        return self._engineering_validation._maybe_persist_validation(
            project_id,
            operation,
            result,
            persist=persist,
        )

    @staticmethod
    def _clone_document(document: Document) -> Document:
        return Document.from_dict(copy.deepcopy(document.to_dict()))

    @staticmethod
    def _bounds_size(bounds: dict[str, list[float]]) -> tuple[float, float, float]:
        minimum = bounds["min"]
        maximum = bounds["max"]
        return (
            maximum[0] - minimum[0],
            maximum[1] - minimum[1],
            maximum[2] - minimum[2],
        )

    @staticmethod
    def _validate_expected_revision(actual_revision: int, expected_revision: int) -> None:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValidationError("expected_revision must be a non-negative integer.")
        if actual_revision != expected_revision:
            raise ConflictError(
                "REVISION_CONFLICT",
                f"Expected revision {expected_revision}, current revision is {actual_revision}.",
            )

    @staticmethod
    def _validated_nonnegative_number(name: str, value: Any) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(f"{name} must be a non-negative finite number.")
        normalized = float(value)
        if not math.isfinite(normalized) or normalized < 0:
            raise ValidationError(f"{name} must be a non-negative finite number.")
        return normalized

    @staticmethod
    def _validated_positive_number(name: str, value: Any) -> float:
        normalized = CadyxCore._validated_nonnegative_number(name, value)
        if normalized <= 0:
            raise ValidationError(f"{name} must be a positive finite number.")
        return normalized

    @staticmethod
    def _validated_body_id(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value:
            raise ValidationError(f"{name} must be a non-empty string.")
        return value

    @staticmethod
    def _validated_dimensions(value: Any) -> dict[str, float]:
        if not isinstance(value, dict) or set(value) != {"x", "y", "z"}:
            raise ValidationError("expected_dimensions must contain x, y and z.")
        return {
            axis: CadyxCore._validated_nonnegative_number(f"expected_dimensions.{axis}", value[axis])
            for axis in ("x", "y", "z")
        }

    @staticmethod
    def _body_by_id(document: Document, body_id: str) -> dict[str, Any]:
        for obj in document.objects:
            if obj.get("kind") == "body" and obj.get("id") == body_id:
                return obj
        raise ValidationError(f"Body '{body_id}' was not found.")

    @staticmethod
    def _brep_geometry(body: dict[str, Any]) -> dict[str, Any] | None:
        geometry = body.get("geometry")
        if isinstance(geometry, dict) and geometry.get("kind") == "cadyx-occt-brep-v1":
            return geometry
        return None

    @staticmethod
    def _fallback_bounds(document: Document) -> dict[str, list[float]] | None:
        geometry_bounds: list[dict[str, list[float]]] = []
        for obj in document.objects:
            if obj.get("kind") != "body":
                continue
            geometry = obj.get("geometry")
            if not isinstance(geometry, dict):
                continue
            bounds = geometry.get("bounds")
            if not isinstance(bounds, dict):
                continue
            minimum = bounds.get("min")
            maximum = bounds.get("max")
            if (
                isinstance(minimum, list)
                and isinstance(maximum, list)
                and len(minimum) == 3
                and len(maximum) == 3
            ):
                geometry_bounds.append(bounds)
        if not geometry_bounds:
            return None
        return {
            "min": [min(bounds["min"][axis] for bounds in geometry_bounds) for axis in range(3)],
            "max": [max(bounds["max"][axis] for bounds in geometry_bounds) for axis in range(3)],
        }

    @staticmethod
    def _closed_profile(document: Document, sketch_id: str):
        return closed_profile(document, sketch_id)

    @staticmethod
    def _closed_profiles(document: Document, sketch_ids: list[str]):
        return closed_profiles(document, sketch_ids)

    @staticmethod
    def _open_path(document: Document, sketch_id: str):
        return open_path(document, sketch_id)
