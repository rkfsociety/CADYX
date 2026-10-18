"""Application Core shared by agent tools and the web interface."""

from __future__ import annotations

import copy
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Literal

from .commands import CommandSystem
from .capability_registry import CapabilityRegistry
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
from .runtime_cache import RuntimeCachePolicy
from .scan import ScanProcessor
from .self_evolving import SelfEvolvingCapabilityEngine
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
        self.capability_registry = CapabilityRegistry(self.workspace.root / ".cadyx-capabilities.sqlite3")
        self.self_evolving = SelfEvolvingCapabilityEngine(self, self.capability_registry)

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
    ) -> _ProjectAvailability:
        return self._lifecycle._require_project_policy(project_id, policy, message, code=code, valid_lease=valid_lease)

    @contextmanager
    def _selection_project_locks(
        self,
        current_selection: dict[str, str] | None,
        target_project_id: str | None,
    ) -> Iterator[None]:
        with self._lifecycle._selection_project_locks(current_selection, target_project_id):
            yield

    def _assert_workspace_selection_policy(
        self,
        current_selection: dict[str, str] | None,
        target_type: str,
        target_project_id: str | None,
    ) -> None:
        return self._lifecycle._assert_workspace_selection_policy(current_selection, target_type, target_project_id)

    @contextmanager
    def _project_lifecycle_lock(self, project_id: str) -> Iterator[None]:
        with self._lifecycle._project_lifecycle_lock(project_id):
            yield

    def create_project(self, name: str, *, select: bool = True) -> dict[str, Any]:
        return self._lifecycle.create_project(name, select=select)

    def upload_project_step(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        return self._lifecycle.upload_project_step(project_id, filename, content)

    def copy_project_files(self, target_project_id: str, source_project_id: str) -> dict[str, Any]:
        return self._lifecycle.copy_project_files(target_project_id, source_project_id)

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

    def create_project_group(self, name: str) -> dict[str, str]:
        return self._lifecycle.create_project_group(name)

    def assign_project_group(self, project_id: str, group_id: str | None) -> dict[str, Any]:
        return self._lifecycle.assign_project_group(project_id, group_id)

    def workspace_selection_snapshot(self) -> dict[str, str] | None:
        return self._lifecycle.workspace_selection_snapshot()

    def select_workspace_resource(self, selection: dict[str, Any]) -> dict[str, str]:
        return self._lifecycle.select_workspace_resource(selection)

    def agent_dashboard_snapshot(self, project_id: str) -> dict[str, Any]:
        return self._command_activity.agent_dashboard_snapshot(project_id)

    def report_agent_dashboard(self, project_id: str, report: dict[str, Any]) -> dict[str, Any]:
        return self._command_activity.report_agent_dashboard(project_id, report)

    def agent_capabilities(self) -> dict[str, Any]:
        from .capabilities import agent_api_capabilities

        return agent_api_capabilities(self.self_evolving.available())

    def develop_agent_capability(self, project_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        self.observe_agent_operation(
            project_id, "Проверяет существующие возможности",
            lambda: self.self_evolving.available(),
            detail_from_result=lambda available: {
                "phase": "capability_check", "reusable_count": len(available),
            },
        )
        prepared = self.observe_agent_operation(
            project_id, f"Разрабатывает инструмент: {definition.get('title', 'новая возможность')}",
            lambda: self.self_evolving.prepare_proposal(definition),
            detail_from_result=lambda item: {
                "phase": "development", "capability_id": item["id"],
                "title": item["title"], "reason": item["description"],
            },
        )
        test_result = self.observe_agent_operation(
            project_id, f"Тестирует инструмент на геометрии: {prepared['title']}",
            lambda: self.self_evolving.test_proposal(prepared),
            detail_from_result=lambda result: {
                "phase": "testing", "capability_id": prepared["id"],
                "title": prepared["title"], "reason": prepared["description"],
                "test_result": result,
            },
        )
        return self.observe_agent_operation(
            project_id, f"Интегрирует возможность: {prepared['title']}",
            lambda: self.self_evolving.register_tested(prepared, test_result),
            detail_from_result=lambda item: {
                "phase": "integration", "capability_id": item["id"],
                "title": item["title"], "reason": item["description"],
                "test_result": item["test_result"],
            },
        )

    def check_agent_capability(self, project_id: str, command_type: str) -> dict[str, Any]:
        result = self.self_evolving.missing_command({"type": command_type})
        if result is None:
            return {"status": "available", "command_type": command_type}
        return self.observe_agent_operation(
            project_id, f"Требуемая возможность отсутствует: {command_type}",
            lambda: result,
            detail_from_result=lambda missing: {
                "phase": "capability_missing", "capability_id": command_type,
                "title": command_type, "reason": missing["message"],
            },
        )

    def execute_agent_capability(
        self,
        project_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        *,
        owner_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        from .lease_heartbeat import LeaseHeartbeat

        revision = [expected_revision]

        def execute(arguments_for_step: dict[str, Any]) -> dict[str, Any]:
            resumed = self.resume_agent(project_id, owner_id, revision[0])
            lease_id = resumed["lease"]["lease_id"]
            heartbeat = LeaseHeartbeat(self, project_id, lease_id)
            primary_error: BaseException | None = None
            try:
                heartbeat.start()
                heartbeat.check()
                result = self.execute_command(
                    project_id, lease_id, arguments_for_step["command"],
                    expected_owner_type=OwnerType.AGENT, expected_revision=revision[0],
                )
                revision[0] = result["document"]["revision"]
                heartbeat.check()
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                heartbeat.stop()
                try:
                    self.stop_agent(project_id, lease_id)
                except BaseException as stop_error:
                    if primary_error is None:
                        raise
                    primary_error.add_note(f"Agent capability lease cleanup also failed: {stop_error}")
            return result

        self.observe_agent_operation(
            project_id, "Возобновляет построение модели",
            lambda: {"capability_id": capability_id},
            detail_from_result=lambda result: {
                "phase": "model_resume", "capability_id": result["capability_id"],
            },
        )
        result = self.self_evolving.execute(capability_id, arguments, execute)
        return {**result, "revision": revision[0]}

    def rollback_agent_capability(self, project_id: str, capability_id: str) -> dict[str, Any]:
        self._require_project_policy(
            project_id, "write", "Capability rollback requires an idle, unlocked project.",
        )
        return self.observe_agent_operation(
            project_id, "Откатывает версию возможности",
            lambda: self.capability_registry.rollback(capability_id),
        )

    def inspect_project(self, project_id: str) -> dict[str, Any]:
        return self._lifecycle.inspect_project(project_id)
    def acquire_lease(self, project_id: str, owner_type: str, owner_id: str) -> dict[str, Any]:
        return self._command_activity.acquire_lease(project_id, owner_type, owner_id)

    def compare_bodies(self, project_id: str, original_id: str | None = None, modified_id: str | None = None) -> dict:
        return self._rendering.compare_bodies(project_id, original_id, modified_id)

    def render_project(self, project_id: str) -> dict[str, Any]:
        return self._rendering.render_project(project_id)

    def _scene_for_project(self, project_id: str) -> tuple[tuple[str, str], dict[str, Any]]:
        return self._rendering._scene_for_project(project_id)

    def _remember_png_locked(
        self,
        cache_key: tuple[str, str, int, int, int],
        encoded: bytes,
    ) -> None:
        return self._rendering._remember_png_locked(cache_key, encoded)

    def _render_project_png_server(
        self, project_id: str, *, width: int = 960, height: int = 720, margin: int = 56,
    ) -> bytes:
        return self._rendering._render_project_png_server(project_id, width=width, height=height, margin=margin)

    def render_project_thumbnail_png(self, project_id: str) -> tuple[bytes, int]:
        return self._rendering.render_project_thumbnail_png(project_id)

    def publish_viewport_snapshot(self, project_id: str, revision: int, encoded: bytes) -> dict[str, int]:
        return self._rendering.publish_viewport_snapshot(project_id, revision, encoded)

    def render_project_png_with_source(self, project_id: str) -> tuple[bytes, str, int]:
        return self._rendering.render_project_png_with_source(project_id)

    def render_project_png(self, project_id: str) -> bytes:
        return self._rendering.render_project_png(project_id)

    def inspect_body_topology(self, project_id: str, body_id: str) -> dict[str, Any]:
        return self._rendering.inspect_body_topology(project_id, body_id)

    def validate_body_dimensions(
        self, project_id: str, body_id: str, expected_revision: Any,
        expected_dimensions: Any, tolerance: Any, *, persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_dimensions(project_id, body_id, expected_revision, expected_dimensions, tolerance, persist=persist)

    def validate_body_clearance(
        self, project_id: str, first_body_id: str, second_body_id: str,
        expected_revision: Any, minimum_clearance: Any, tolerance: Any,
        *, progress: Callable[[str, float], None] | None = None, persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_clearance(project_id, first_body_id, second_body_id, expected_revision, minimum_clearance, tolerance, progress=progress, persist=persist)

    def list_validation_records(self, project_id: str, *, limit: int = 50) -> dict[str, Any]:
        return self._engineering_validation.list_validation_records(project_id, limit=limit)

    def validate_body_preservation(
        self, project_id: str, reference_body_id: str, candidate_body_id: str,
        expected_revision: Any, maximum_lost_volume: Any, tolerance: Any,
        *, persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_preservation(project_id, reference_body_id, candidate_body_id, expected_revision, maximum_lost_volume, tolerance, persist=persist)

    def validate_body_thickness(
        self, project_id: str, body_id: str, expected_revision: Any,
        minimum_thickness: Any, tolerance: Any, *,
        progress: Callable[[str, float], None] | None = None, persist: bool = False,
        method: str = "sampled",
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_thickness(project_id, body_id, expected_revision, minimum_thickness, tolerance, progress=progress, persist=persist, method=method)

    def _maybe_persist_validation(
        self, project_id: str, kind: str, result: dict[str, Any], persist: bool,
    ) -> dict[str, Any]:
        return self._engineering_validation._maybe_persist_validation(project_id, kind, result, persist)

    def rotate_view(
        self, project_id: str, *, yaw_delta: float, pitch_delta: float = 0.0,
        zoom_factor: float = 1.0, duration_ms: int = 1200,
    ) -> dict[str, Any]:
        return self._command_activity.rotate_view(project_id, yaw_delta=yaw_delta, pitch_delta=pitch_delta, zoom_factor=zoom_factor, duration_ms=duration_ms)

    def observe_agent_operation(
        self,
        project_id: str,
        action: str,
        operation: Callable[..., Any],
        *,
        reports_progress: bool = False,
        detail_from_result: Callable[[Any], dict[str, Any]] | None = None,
    ) -> Any:
        return self._command_activity.observe_agent_operation(
            project_id, action, operation, reports_progress=reports_progress,
            detail_from_result=detail_from_result,
        )

    def heartbeat(self, project_id: str, lease_id: str) -> dict[str, Any]:
        return self._command_activity.heartbeat(project_id, lease_id)

    def release_lease(self, project_id: str, lease_id: str) -> dict[str, Any]:
        return self._command_activity.release_lease(project_id, lease_id)

    def recover_expired_lease(self, project_id: str) -> dict[str, Any]:
        return self._command_activity.recover_expired_lease(project_id)

    def stop_agent(self, project_id: str, lease_id: str) -> dict[str, Any]:
        return self._command_activity.stop_agent(project_id, lease_id)

    @staticmethod
    def _check_revision(document: Document, expected_revision: Any) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValidationError("expected_revision must be a non-negative integer.")
        if document.revision != expected_revision:
            raise ConflictError("Saved revision changed; inspect the project before continuing.", code="REVISION_CONFLICT")

    def resume_agent(self, project_id: str, owner_id: str, expected_revision: int) -> dict[str, Any]:
        return self._command_activity.resume_agent(project_id, owner_id, expected_revision)

    def execute_command(
        self,
        project_id: str,
        lease_id: str | None,
        command: dict[str, Any],
        *,
        expected_owner_type: OwnerType | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        return self._command_activity.execute_command(project_id, lease_id, command, expected_owner_type=expected_owner_type, expected_revision=expected_revision)

    def _publish_activity(self, project_id: str, activity: dict[str, Any]) -> None:
        steps = activity.setdefault("steps", [])
        phase = activity.get("phase")
        state = activity.get("state")
        if state == "completed":
            for step in steps:
                step["state"] = "completed"
        elif isinstance(phase, str) and phase != "completed":
            if steps and steps[-1]["phase"] == phase:
                steps[-1]["state"] = "failed" if state == "failed" else "active"
            else:
                if steps:
                    steps[-1]["state"] = "completed"
                steps.append({"phase": phase, "state": "failed" if state == "failed" else "active"})
        elif state == "failed" and steps:
            steps[-1]["state"] = "failed"
        activity["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.events.publish("agent.activity", project_id, copy.deepcopy(activity))

    @staticmethod
    def _scan_source(command: Any, saved_document: Any) -> dict[str, str] | None:
        if not isinstance(command, dict):
            return None
        if command.get("type") == "import_scan":
            folder, filename = command.get("folder"), command.get("file")
        elif command.get("type") == "set_scan_parameter":
            feature_id = command.get("feature_id")
            objects = saved_document.get("objects", []) if isinstance(saved_document, dict) else []
            feature = next((item for item in objects if isinstance(item, dict) and
                            item.get("id") == feature_id and item.get("operation") == "reconstruct_scan"), None)
            source = feature.get("source") if feature else None
            folder = source.get("folder") if isinstance(source, dict) else None
            filename = source.get("file") if isinstance(source, dict) else None
        else:
            return None
        if isinstance(folder, str) and isinstance(filename, str):
            return {"folder": folder, "file": filename}
        return None

    def _validate_geometry(self, document: Document) -> None:
        by_id = {item["id"]: item for item in document.objects}
        consumed_bodies = {
            source_id
            for item in document.objects
            if item.get("type") == "feature"
            for source_id in ((item.get("base_body_id"), item.get("tool_body_id"))
                              if item.get("operation") in {"fuse_bodies", "cut_bodies"}
                              else (item.get("source_body_id"),)
                              if item.get("operation") in {
                                  "fillet_edges", "resize_imported_thread", "transform_imported_brep",
                                  "pad_sketch_to_body",
                              } else ())
        }
        for item in document.objects:
            geometry = item.get("geometry")
            # Feature records duplicate their body's geometry, and consumed
            # source bodies are embedded in the current body's geometry graph.
            # Validate each live B-rep once instead of rebuilding every prefix
            # of a long revolve/fuse/fillet chain on every read-only refresh.
            if geometry is not None and item.get("type") == "body" and item["id"] not in consumed_bodies:
                self.kernel.validate(geometry)
            if item.get("type") != "feature":
                continue
            if item.get("operation") == "pad_sketch_to_body":
                body = by_id.get(item.get("body_id"))
                source = by_id.get(item.get("source_body_id"))
                feature_geometry = item.get("geometry")
                sketch_id = item.get("source_sketch_id")
                sketch = by_id.get(sketch_id)
                parameters = item.get("parameters")
                if (body is None or source is None or sketch is None or sketch.get("type") != "sketch"
                        or feature_geometry != body.get("geometry") or not isinstance(parameters, dict)):
                    raise ValidationError("Pad feature geometry or source references are invalid.")
                plane, profiles = closed_profiles(document.objects, sketch_id)
                tool = (self.kernel.create_extruded_profile(
                    profiles[0], plane=plane, depth=parameters["depth"], centered=parameters["centered"],
                ) if len(profiles) == 1 else self.kernel.create_extruded_profile_with_holes(
                    profiles, plane=plane, depth=parameters["depth"], centered=parameters["centered"],
                ))
                offset = sketch.get("section_offset", 0.0)
                if offset:
                    shift = [0.0, 0.0, 0.0]
                    shift[2 if plane == "XY" and sketch.get("section_normal_axis") == "Z" else 1] = offset
                    tool = self.kernel.translate_solid(tool, shift)
                expected = self.kernel.fuse_profiles(source["geometry"], tool)
                if expected != feature_geometry:
                    raise ValidationError("Pad feature geometry is out of date with its source sketch or body.")
                continue
            if item.get("operation") in {"fuse_bodies", "cut_bodies"}:
                feature_geometry = item.get("geometry")
                body = by_id.get(item.get("body_id"))
                base = by_id.get(item.get("base_body_id"))
                tool = by_id.get(item.get("tool_body_id"))
                if any(value is None or value.get("type") != "body" for value in (body, base, tool)):
                    raise ValidationError("Boolean feature refers to a missing body.")
                if feature_geometry != body.get("geometry"):
                    raise ValidationError("Boolean feature geometry must match its body.")
                expected_kind = {"fused_profiles", "fused_solids"} if item.get("operation") == "fuse_bodies" else {"cut_solids"}
                if (not isinstance(feature_geometry, dict)
                        or feature_geometry.get("kind") not in expected_kind
                        or feature_geometry.get("base") != base.get("geometry")
                        or feature_geometry.get("tool") != tool.get("geometry")):
                    raise ValidationError("Boolean feature geometry is out of date with its source bodies.")
                continue
            if item.get("operation") == "fillet_edges":
                body = by_id.get(item.get("body_id"))
                source = by_id.get(item.get("source_body_id"))
                feature_geometry = item.get("geometry")
                if (body is None or source is None or feature_geometry != body.get("geometry")
                        or not isinstance(feature_geometry, dict)
                        or feature_geometry.get("kind") != "filleted_solid"
                        or feature_geometry.get("base") != source.get("geometry")
                        or feature_geometry.get("edge_indices") != item.get("edge_indices")
                        or feature_geometry.get("radius") != item.get("parameters", {}).get("radius")):
                    raise ValidationError("Fillet geometry is out of date with its source body and parameters.")
                continue
            if item.get("operation") in {"resize_imported_thread", "transform_imported_brep"}:
                body = by_id.get(item.get("body_id"))
                source = by_id.get(item.get("source_body_id"))
                feature_geometry = item.get("geometry")
                parameters = item.get("parameters")
                operation = item.get("operation")
                expected_parameters = (
                    {"x_start", "x_end", "axis_origin_y", "axis_origin_z", "major_diameter", "diameter_delta"}
                    if operation == "resize_imported_thread"
                    else {"rotation_axis", "angle_degrees", "translation"}
                )
                if (body is None or body.get("type") != "body"
                        or source is None or source.get("type") != "body"
                        or feature_geometry != body.get("geometry")
                        or not isinstance(feature_geometry, dict)
                        or feature_geometry.get("kind") != "imported_brep"
                        or feature_geometry.get("brep_valid") is not True
                        or not isinstance(parameters, dict) or set(parameters) != expected_parameters):
                    raise ValidationError("Imported B-rep feature is out of date or invalid.")
                if operation == "resize_imported_thread":
                    dimensions = [parameters[key] for key in (
                        "x_start", "x_end", "axis_origin_y", "axis_origin_z",
                        "major_diameter", "diameter_delta",
                    )]
                    if (any(isinstance(value, bool) or not isinstance(value, (int, float))
                            or not math.isfinite(value) for value in dimensions)
                            or parameters["x_end"] <= parameters["x_start"]
                            or parameters["major_diameter"] <= 0 or parameters["diameter_delta"] <= 0):
                        raise ValidationError("Imported thread feature parameters are invalid.")
                else:
                    axis, angle, translation = (
                        parameters.get("rotation_axis"), parameters.get("angle_degrees"),
                        parameters.get("translation"),
                    )
                    if (not isinstance(axis, list) or len(axis) != 3
                            or not isinstance(translation, list) or len(translation) != 3
                            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                                   or not math.isfinite(value) for value in [*axis, *translation])
                            or isinstance(angle, bool) or not isinstance(angle, (int, float))
                            or not math.isfinite(angle) or abs(angle) > 360
                            or math.hypot(*axis) <= 1e-12):
                        raise ValidationError("Imported B-rep transform parameters are invalid.")
                continue
            if item.get("operation") == "create_ruled_shell":
                body = by_id.get(item.get("body_id"))
                feature_geometry = item.get("geometry")
                parameters = item.get("parameters")
                if (body is None or body.get("type") != "body"
                        or feature_geometry != body.get("geometry")
                        or not isinstance(parameters, dict)
                        or set(parameters) != {"patches", "sewing_tolerance"}):
                    raise ValidationError("Ruled shell feature geometry must match its body and patch parameters.")
                expected = self.kernel.create_ruled_shell(
                    parameters["patches"], sewing_tolerance=parameters["sewing_tolerance"],
                    compute_volume=False,
                )
                expected.pop("volume", None)
                comparable_geometry = {key: value for key, value in feature_geometry.items() if key != "volume"}
                if expected != comparable_geometry:
                    raise ValidationError("Ruled shell geometry is out of date with its saved rail patches.")
                continue
            if item.get("operation") == "loft_sketches":
                body = by_id.get(item.get("body_id"))
                feature_geometry = item.get("geometry")
                source_sketch_ids = item.get("source_sketch_ids")
                parameters = item.get("parameters")
                if (body is None or body.get("type") != "body"
                        or feature_geometry != body.get("geometry")
                        or not isinstance(source_sketch_ids, list) or not isinstance(parameters, dict)
                        or parameters.get("section_sketch_ids") != source_sketch_ids):
                    raise ValidationError("Loft feature geometry must match its body and ordered source sketches.")
                groups = parameters.get("section_sketch_groups", [[value] for value in source_sketch_ids])
                if (not isinstance(groups, list) or not groups
                        or [value for group in groups for value in group] != source_sketch_ids):
                    raise ValidationError("Loft feature contour groups do not match their source sketches.")
                sections, offsets = [], []
                for group in groups:
                    group_profiles, group_offsets = [], []
                    for sketch_id in group:
                        sketch = by_id.get(sketch_id)
                        if (sketch is None or sketch.get("type") != "sketch" or sketch.get("plane") != "XZ"
                                or sketch.get("section_normal_axis") != "Y"):
                            raise ValidationError("Loft feature source sketches must be Y-normal XZ sections.")
                        _, profile = closed_profile(document.objects, sketch_id)
                        group_profiles.append(profile)
                        group_offsets.append(sketch.get("section_offset"))
                    if not group_offsets or any(value != group_offsets[0] for value in group_offsets):
                        raise ValidationError("Loft feature contour group has inconsistent section offsets.")
                    sections.append(group_profiles)
                    offsets.append(group_offsets[0])
                if any(len(group) != 1 for group in sections):
                    expected = self.kernel.create_multicontour_loft(
                        sections, section_offsets=offsets, compute_volume=False,
                    )
                else:
                    expected = self.kernel.create_lofted_profiles(
                        [group[0] for group in sections], section_offsets=offsets,
                        alignment=feature_geometry.get("profile_alignment", "independent-start-v1"),
                    )
                    if "profile_alignment" not in feature_geometry:
                        expected.pop("profile_alignment", None)
                if expected.get("kind") == "multicontour_loft":
                    expected.pop("volume", None)
                    comparable_geometry = copy.deepcopy(feature_geometry)
                    comparable_geometry.pop("volume", None)
                    for branch in expected["branches"] + comparable_geometry.get("branches", []):
                        branch.pop("volume", None)
                else:
                    comparable_geometry = feature_geometry
                if expected != comparable_geometry:
                    raise ValidationError("Loft feature geometry is out of date with its section sketches.")
                continue
            if item.get("operation") == "import_step":
                body = by_id.get(item.get("body_id"))
                geometry = body.get("geometry") if body is not None and body.get("type") == "body" else None
                source_metrics = item.get("source_metrics")
                if (not isinstance(geometry, dict) or geometry.get("kind") != "imported_brep"
                        or not isinstance(source_metrics, dict)
                        or set(source_metrics) != {
                            "volume", "surface_area", "bounds", "topology", "brep_valid", "repaired",
                        }):
                    raise ValidationError("STEP import feature is missing its saved source geometry metrics.")
                actual_metrics = self.kernel.metrics(geometry)
                bounds_match = all(
                    math.isclose(actual_metrics["bounds"][side][index], source_metrics["bounds"][side][index],
                                 rel_tol=1e-12, abs_tol=1e-12)
                    for side in ("min", "max") for index in range(3)
                )
                if (not bounds_match
                        or actual_metrics.get("topology") != source_metrics.get("topology")
                        or actual_metrics.get("brep_valid") != source_metrics.get("brep_valid")
                        or not math.isclose(actual_metrics.get("volume", float("nan")),
                                            source_metrics.get("volume", float("nan")),
                                            rel_tol=1e-12, abs_tol=1e-12)
                        or not math.isclose(actual_metrics.get("surface_area", float("nan")),
                                            source_metrics.get("surface_area", float("nan")),
                                            rel_tol=1e-12, abs_tol=1e-12)):
                    raise ValidationError("STEP import B-rep does not match the geometry read from the source file.")
                continue
            if item.get("operation") == "metric_thread":
                body = by_id.get(item.get("body_id"))
                geometry = item.get("geometry")
                parameters = item.get("parameters")
                if (body is None or body.get("type") != "body" or geometry != body.get("geometry")
                        or not isinstance(geometry, dict) or geometry.get("kind") != "metric_thread"
                        or not isinstance(parameters, dict)
                        or set(parameters) != {"major_diameter", "pitch", "length", "handedness"}
                        or any(geometry.get(name) != parameters.get(name)
                               for name in ("major_diameter", "pitch", "length", "handedness"))):
                    raise ValidationError("Metric thread geometry is out of date with its feature parameters.")
                continue
            if item.get("operation") == "sweep_sketch":
                body = by_id.get(item.get("body_id"))
                feature_geometry = item.get("geometry")
                source_id = item.get("source_sketch_id")
                if body is None or feature_geometry != body.get("geometry"):
                    raise ValidationError("Sweep feature geometry must match its body.")
                plane, path = open_path(document.objects, source_id)
                if (feature_geometry.get("kind") != "swept_pipe" or feature_geometry.get("plane") != plane
                        or feature_geometry.get("path") != path
                        or feature_geometry.get("radius") != item.get("parameters", {}).get("radius")):
                    raise ValidationError("Sweep feature geometry is out of date with its source sketch.")
                continue
            if item.get("operation") not in {"extrude_sketch", "revolve_sketch"}:
                continue
            feature_geometry = item.get("geometry")
            body = by_id.get(item.get("body_id"))
            source_id = item.get("source_sketch_id")
            if body is None or feature_geometry != body.get("geometry"):
                raise ValidationError("Profile feature geometry must match its body.")
            operation = item["operation"]
            parameters = item.get("parameters")
            if not isinstance(feature_geometry, dict) or not isinstance(parameters, dict):
                raise ValidationError("Profile feature geometry is missing.")
            if operation == "extrude_sketch":
                plane, profiles = closed_profiles(document.objects, source_id)
                if len(profiles) > 1:
                    expected = self.kernel.create_extruded_profile_with_holes(
                        profiles, plane=plane, depth=parameters["depth"],
                        centered=parameters.get("centered", False),
                    )
                    if expected != feature_geometry:
                        raise ValidationError("Profile feature geometry is out of date with its source sketch or parameters.")
                    continue
                profile = profiles[0]
            else:
                plane, profile = closed_profile(document.objects, source_id)
            expected_kind = "extruded_profile" if operation == "extrude_sketch" else "revolved_profile"
            if not isinstance(parameters, dict):
                raise ValidationError("Profile feature parameters are invalid.")
            if feature_geometry.get("kind") != expected_kind or feature_geometry.get("plane") != plane or feature_geometry.get("profile") != profile:
                raise ValidationError("Profile feature geometry is out of date with its source sketch.")
            parameter_name = "depth" if operation == "extrude_sketch" else "angle_degrees"
            if feature_geometry.get(parameter_name) != parameters.get(parameter_name):
                raise ValidationError("Profile feature geometry is out of date with its parameters.")
            if feature_geometry.get("centered", False) != parameters.get("centered", False):
                raise ValidationError("Profile feature centered-extrusion state is out of date.")

    @staticmethod
    def _clone_scene(scene: dict[str, Any]) -> dict[str, Any]:
        clone = scene.copy()
        bounds = scene.get("bounds")
        clone["bounds"] = None if bounds is None else {
            "min": list(bounds["min"]),
            "max": list(bounds["max"]),
        }
        objects = []
        for item in scene.get("objects", []):
            copied = item.copy()
            for field in ("positions", "triangles", "lines", "points"):
                copied[field] = list(item[field])
            objects.append(copied)
        clone["objects"] = objects
        return clone

    @staticmethod
    def _document_cache_key(project_id: str, document: Document | dict[str, Any]) -> tuple[str, str]:
        # The revision changes atomically with every accepted document edit.
        # Use it instead of serializing and hashing large scan meshes on reads.
        revision = document.get("revision", 0) if isinstance(document, dict) else document.revision
        return project_id, f"{type(revision).__name__}:{revision!r}"

    def _remember_geometry_validation(self, project_id: str, document: Document) -> None:
        return self._rendering._remember_geometry_validation(project_id, document)

    def _invalidate_project_runtime_caches(self, project_id: str) -> None:
        return self._rendering._invalidate_project_runtime_caches(project_id)

    def _validate_geometry_once(self, project_id: str, document: Document) -> None:
        return self._rendering._validate_geometry_once(project_id, document)

    @staticmethod
    def _summary(project: dict[str, Any]) -> dict[str, Any]:
        return {
            key: project[key]
            for key in ("id", "format", "version", "name", "created_at", "modified_at")
        }
