"""Application Core shared by agent tools and the web interface."""

from __future__ import annotations

import copy
import math
import threading
import time
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Literal, cast

from .commands import CommandSystem
from .capability_registry import CapabilityRegistry
from .core_services import (
    CommandActivityService,
    EngineeringValidationService,
    ProjectLifecycleService,
    RenderingCacheService,
    _ProjectAvailability,
)
from .errors import CadyxError, ConflictError, ValidationError
from .events import EventBroker
from .kernel import GeometryKernel, OCCTKernelAdapter
from .kernel_descriptors import GeometryDescriptor
from .lease import EditLeaseManager
from .models import Document, OwnerType
from .runtime_cache import RuntimeCachePolicy
from .scan import ScanProcessor
from .scan_capture import Scan360CaptureController
from .self_evolving import SelfEvolvingCapabilityEngine
from .source_extension import SourceExtensionEngine
from .workspace import ProjectWorkspace
from .profile_segments import sketch_segment_profiles, sketch_segments
from .sketch_workplanes import kernel_plane, place_geometry
from .sketches import (closed_profile, closed_profiles, closed_spline_profile,
                       generated_gear_curve_profiles, open_path)
from .manufacturing_features import grouped_profile_feature_geometry


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
        self._scan_capture = Scan360CaptureController(Path(__file__).resolve().parent.parent, self.workspace.root)
        self.kernel = kernel if kernel is not None else OCCTKernelAdapter()
        self.commands = CommandSystem(
            self.kernel,
            resolve_scan=self.workspace.resolve_folder_file,
            resolve_project_file=self.workspace.resolve_project_file,
            scan_processor=scan_processor,
            publish_scan_output=self.workspace.publish_scan_output,
            publish_project_scan_output=self.workspace.publish_project_scan_output,
            publish_cad_delivery=self.workspace.publish_cad_delivery,
        )
        if lease_clock is None:
            self.leases = EditLeaseManager(self.workspace._project_dir, timeout_seconds=lease_timeout_seconds)
        else:
            self.leases = EditLeaseManager(self.workspace._project_dir, timeout_seconds=lease_timeout_seconds, clock=lease_clock)
        self.events = EventBroker(
            dashboard_state_path=self.workspace.state_root / ".cadyx-agent-dashboards.json",
            dashboard_database_path=self.workspace.state_root / ".cadyx-agent-dashboards.sqlite3",
        )
        self._server_agent_lease_guard = threading.RLock()
        self._server_agent_leases: dict[str, tuple[str, str]] = {}
        self.events.prune_agent_dashboards(self.workspace._project_ids_for_dashboard_cleanup())
        self._lifecycle = ProjectLifecycleService(self)
        self._command_activity = CommandActivityService(self)
        self._rendering = RenderingCacheService(self, cache_policy=cache_policy)
        self._engineering_validation = EngineeringValidationService(self)
        self.capability_registry = CapabilityRegistry(self.workspace.state_root / ".cadyx-capabilities.sqlite3")
        self.self_evolving = SelfEvolvingCapabilityEngine(self, self.capability_registry)
        self.source_extensions = SourceExtensionEngine(self.workspace.state_root / ".cadyx-source-extensions.sqlite3")
        self._command_proposals: dict[str, dict[str, Any]] = {}
        self._proposal_guard = threading.RLock()

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
    def _selection_project_lock(self, target_project_id: str | None) -> Iterator[None]:
        with self._lifecycle._selection_project_lock(target_project_id):
            yield

    def _assert_workspace_selection_policy(
        self,
        target_type: str,
        target_project_id: str | None,
    ) -> None:
        return self._lifecycle._assert_workspace_selection_policy(target_type, target_project_id)

    @contextmanager
    def _project_lifecycle_lock(self, project_id: str) -> Iterator[None]:
        with self._lifecycle._project_lifecycle_lock(project_id):
            yield

    def create_project(
        self, name: str, *, select: bool = True, url_slug: str | None = None,
        project_type: str = "single_model",
    ) -> dict[str, Any]:
        return self._lifecycle.create_project(
            name, select=select, url_slug=url_slug, project_type=project_type,
        )

    def upload_project_step(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        return self._lifecycle.upload_project_step(project_id, filename, content)

    def upload_project_scan(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        return self._lifecycle.upload_project_scan(project_id, filename, content)

    def upload_project_file(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        return self._lifecycle.upload_project_file(project_id, filename, content)

    def start_scan_360(self, project_id: str, rotation_period_seconds: float = 18.0,
                       scanner_device_id: str | None = None, turntable_device_id: str | None = None,
                       step_degrees: int = 30, align_to_markers: bool = True) -> dict[str, Any]:
        return self._lifecycle.start_scan_360(
            project_id, rotation_period_seconds, scanner_device_id, turntable_device_id,
            step_degrees, align_to_markers,
        )

    def scan_360_status(self, project_id: str) -> dict[str, Any]:
        return self._lifecycle.scan_360_status(project_id)

    def scanner_devices(self) -> dict[str, list[dict[str, Any]]]:
        return self._scan_capture.list_connected_devices()

    def scanner_calibration(self, scanner_device_id: str | None = None) -> dict[str, Any]:
        return self._scan_capture.scanner_calibration(scanner_device_id)

    def turntable_settings(self, turntable_device_id: str | None = None,
                           profile_id: str | None = None) -> dict[str, Any]:
        return self._scan_capture.turntable_settings(turntable_device_id, profile_id)

    def save_turntable_settings(self, turntable_device_id: str,
                                rotation_period_seconds: float,
                                profile_id: str | None = None) -> dict[str, Any]:
        return self._scan_capture.save_turntable_settings(
            turntable_device_id, rotation_period_seconds, profile_id,
        )

    def turntable_control(self, command: str, turntable_device_id: str | None = None,
                          profile_id: str | None = None, value: float | None = None) -> dict[str, Any]:
        return self._scan_capture.turntable_control(command, turntable_device_id, profile_id, value)

    def cached_turntable_position(self, turntable_device_id: str | None = None,
                                  profile_id: str | None = None,
                                  allow_stale: bool = False) -> dict[str, Any] | None:
        return self._scan_capture.cached_turntable_position(
            turntable_device_id, profile_id, allow_stale=allow_stale,
        )

    def turntable_marker_map(self, turntable_device_id: str | None = None,
                             profile_id: str | None = None) -> tuple[bytes, int]:
        return self._scan_capture.turntable_marker_map(turntable_device_id, profile_id)

    def save_scanner_calibration(self, scanner_device_id: str, q_matrix: bytes,
                                 depth_scale_mm_per_raw_unit: float) -> dict[str, Any]:
        return self._scan_capture.save_scanner_calibration(
            scanner_device_id, q_matrix, depth_scale_mm_per_raw_unit,
        )

    def start_scanner_live_preview(self, scanner_device_id: str | None = None,
                                   fps: int = 10) -> dict[str, Any]:
        return self._scan_capture.start_live_preview(scanner_device_id, fps)

    def scanner_live_preview_status(self) -> dict[str, Any]:
        return self._scan_capture.live_preview_status()

    def record_scanner_live_preview_client_metrics(self, metrics: Any) -> dict[str, Any]:
        return self._scan_capture.record_live_preview_client_metrics(metrics)

    def stop_scanner_live_preview(self) -> dict[str, Any]:
        return self._scan_capture.stop_live_preview()

    def copy_project_files(self, target_project_id: str, source_project_id: str) -> dict[str, Any]:
        return self._lifecycle.copy_project_files(target_project_id, source_project_id)

    def remove_staged_project_step(self, project_id: str, filename: str) -> dict[str, Any]:
        return self._lifecycle.remove_staged_project_step(project_id, filename)

    def remove_project_subfolder_files(
        self, project_id: str, folder_name: str, filenames: list[str],
    ) -> dict[str, Any]:
        return self._lifecycle.remove_project_subfolder_files(project_id, folder_name, filenames)

    def remove_project_files(self, project_id: str, filenames: list[str]) -> dict[str, Any]:
        return self._lifecycle.remove_project_files(project_id, filenames)

    def rename_project_file(
        self, project_id: str, filename: str, new_name: str, folder_name: str | None = None,
    ) -> dict[str, Any]:
        return self._lifecycle.rename_project_file(project_id, filename, new_name, folder_name)

    def move_project_files_to_subfolder(
        self, project_id: str, filenames: list[str], folder_name: str,
    ) -> dict[str, Any]:
        return self._lifecycle.move_project_files_to_subfolder(project_id, filenames, folder_name)

    def remove_project_subfolder(self, project_id: str, folder_name: str) -> dict[str, Any]:
        return self._lifecycle.remove_project_subfolder(project_id, folder_name)

    def prune_project_files(self, project_id: str, keep_files: list[str]) -> dict[str, Any]:
        return self._lifecycle.prune_project_files(project_id, keep_files)

    def rename_project(self, project_id: str, name: str, *, url_slug: str | None = None) -> dict[str, Any]:
        return self._lifecycle.rename_project(project_id, name, url_slug=url_slug)

    def set_project_type(self, project_id: str, project_type: str) -> dict[str, Any]:
        return self._lifecycle.set_project_type(project_id, project_type)

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

    def list_standard_parts(self) -> dict[str, Any]:
        from .standard_parts import list_standard_parts

        return list_standard_parts()

    def resolve_standard_part(self, request: dict[str, Any]) -> dict[str, Any]:
        from .standard_parts import resolve_standard_part

        return resolve_standard_part(request)

    def read_agent_source_file(self, relative_path: str) -> dict[str, Any]:
        return self.source_extensions.read_file(relative_path)

    def list_agent_source_extensions(self) -> list[dict[str, Any]]:
        return self.source_extensions.available()

    def develop_agent_source_extension(
        self, project_id: str, proposal: dict[str, Any],
    ) -> dict[str, Any]:
        self.workspace.load(project_id, include_history=False)
        title = proposal.get("title", "новая возможность") if isinstance(proposal, dict) else "новая возможность"
        return self.observe_agent_operation(
            project_id, f"Разрабатывает исходный код CADYX: {title}",
            lambda report: self.source_extensions.develop(proposal, progress=report),
            reports_progress=True,
            detail_from_result=lambda result: {
                "phase": "integration" if result.get("files_applied") else "testing",
                "extension_id": result.get("id"), "title": title,
                "tests_passed": result.get("tests", {}).get("passed", False),
                "status": result.get("status"),
            },
        )

    def rollback_agent_source_extension(self, project_id: str, extension_id: str) -> dict[str, Any]:
        self.workspace.load(project_id, include_history=False)
        return self.observe_agent_operation(
            project_id, f"Откатывает исходный код CADYX: {extension_id}",
            lambda: self.source_extensions.rollback(extension_id),
            detail_from_result=lambda result: {"phase": "rollback", **result},
        )

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

    def inspect_compact(
        self, project_id: str, *, section: str = "summary",
        expected_revision: int | None = None, kind: str | None = None,
        body_id: str | None = None, shape: str | None = None,
        since_revision: int | None = None, offset: int = 0, limit: int = 25,
        include_metrics: bool = False,
    ) -> dict[str, Any]:
        from .compact_inspection import inspect_compact

        # workspace.load returns a validated, migrated committed snapshot; do not
        # copy its geometry or command payloads into a full inspect response.
        include_history = section == "changes"
        return inspect_compact(
            self.workspace.load(project_id, include_history=include_history), self.kernel,
            section=section, expected_revision=expected_revision, kind=kind,
            body_id=body_id, shape=shape, since_revision=since_revision,
            offset=offset, limit=limit, include_metrics=include_metrics,
        )

    def inspect_project(self, project_id: str, *, browser: bool = False) -> dict[str, Any]:
        return self._lifecycle.inspect_project(project_id, browser=browser)
    def acquire_lease(self, project_id: str, owner_type: str, owner_id: str) -> dict[str, Any]:
        return self._command_activity.acquire_lease(project_id, owner_type, owner_id)

    def compare_bodies(self, project_id: str, original_id: str | None = None, modified_id: str | None = None) -> dict:
        return self._rendering.compare_bodies(project_id, original_id, modified_id)

    def render_project(self, project_id: str) -> dict[str, Any]:
        return self._rendering.render_project(project_id)

    def render_project_step_preview(self, project_id: str, filename: str) -> dict[str, Any]:
        return self._rendering.render_project_step_preview(project_id, filename)

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

    def query_body_topology(
        self, project_id: str, body_id: str, expected_revision: int,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        return self._rendering.query_body_topology(project_id, body_id, expected_revision, query)

    def validate_body_cloud_processing(
        self, project_id: str, body_id: str, expected_revision: int,
        method: str, parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Analyze the full hashed cloud without saving files, features or a revision."""
        if not isinstance(method, str) or method not in {"simplify", "isolation_filter", "remove_overlaps", "smooth"}:
            raise ValidationError("Unsupported point-cloud analysis method.")
        with self._project_guard(project_id):
            self._require_project_policy(project_id, "read", "Project is not available for cloud analysis.")
            project = self.workspace.load(project_id, include_history=False)
            document = Document.from_validated_dict(project["document"])
            self._check_revision(document, expected_revision)
            result = self.commands._process_point_cloud(
                document, {"body_id": body_id, "method": method, "parameters": parameters if parameters is not None else {}},
                project_id=project_id, analysis_only=True,
            )
            return {"revision": document.revision, "read_only": True, **result}

    def validate_body_dimensions(
        self, project_id: str, body_id: str, expected_revision: Any,
        expected_dimensions: Any, tolerance: Any, *, persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_dimensions(project_id, body_id, expected_revision, expected_dimensions, tolerance, persist=persist)

    def validate_planetary_kinematics(
        self, project_id: str, expected_revision: int, sun_sketch_id: str,
        planet_sketch_ids: list[str], ring_sketch_id: str,
        sun_speed_radians_per_second: float, ring_speed_radians_per_second: float,
        *, carrier_body_id: str | None = None, persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_planetary_kinematics(
            project_id, expected_revision, sun_sketch_id, planet_sketch_ids,
            ring_sketch_id, sun_speed_radians_per_second, ring_speed_radians_per_second,
            carrier_body_id=carrier_body_id, persist=persist,
        )

    def validate_body_planetary_kinematics(
        self, project_id: str, expected_revision: int, sun_sketch_id: str,
        planet_sketch_ids: list[str], ring_sketch_id: str,
        sun_speed_radians_per_second: float, ring_speed_radians_per_second: float,
        *, carrier_body_id: str | None = None, persist: bool = False,
    ) -> dict[str, Any]:
        """Operation-named adapter for contract-driven validation dispatch."""
        return self.validate_planetary_kinematics(
            project_id, expected_revision, sun_sketch_id, planet_sketch_ids,
            ring_sketch_id, sun_speed_radians_per_second, ring_speed_radians_per_second,
            carrier_body_id=carrier_body_id, persist=persist,
        )

    def validate_body_clearance(
        self, project_id: str, first_body_id: str, second_body_id: str,
        expected_revision: Any, minimum_clearance: Any, tolerance: Any,
        *, progress: Callable[[str, float], None] | None = None, persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_body_clearance(project_id, first_body_id, second_body_id, expected_revision, minimum_clearance, tolerance, progress=progress, persist=persist)

    def validate_assembly_fit(
        self, project_id: str, expected_revision: int,
        minimum_clearance: float, tolerance: float, *,
        instance_pairs: list[dict[str, str]] | None = None,
        pose_samples: list[dict[str, Any]] | None = None,
        progress: Callable[[str, float], None] | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_assembly_fit(
            project_id, expected_revision, minimum_clearance, tolerance,
            instance_pairs=instance_pairs, pose_samples=pose_samples,
            progress=progress, persist=persist,
        )

    def validate_assembly_mass_properties(
        self, project_id: str, expected_revision: int, *, persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_assembly_mass_properties(
            project_id, expected_revision, persist=persist,
        )

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

    def list_revision_checkpoints(
        self, project_id: str, *, offset: int = 0, limit: int = 50,
    ) -> dict[str, Any]:
        """List actual archived model checkpoints, not inferred history states."""
        project = self.workspace.load(project_id)
        return self.workspace.revision_archive(project_id).list(project, offset=offset, limit=limit)

    def compare_model_revisions(
        self, project_id: str, before: dict[str, Any], after: dict[str, Any], *,
        expected_revision: int, absolute_tolerance: float = 1e-8,
        relative_tolerance: float = 1e-9,
    ) -> dict[str, Any]:
        """Compare two saved revisions or in-memory previews without a write lease."""
        from .revision_diff import compare_documents

        project = self.workspace.load(project_id)
        self._check_revision(Document.from_dict(project["document"]), expected_revision)
        archive = self.workspace.revision_archive(project_id)

        def resolve(selector: dict[str, Any]) -> dict[str, Any]:
            if not isinstance(selector, dict) or len(selector) != 1:
                raise ValidationError("Each comparison selector needs exactly revision or proposal_id.")
            if "revision" in selector:
                return Document.from_dict(archive.load(project, selector["revision"])).to_dict()
            if "proposal_id" in selector:
                proposal_id = selector["proposal_id"]
                if not isinstance(proposal_id, str) or not proposal_id:
                    raise ValidationError("proposal_id must be nonempty text.")
                with self._proposal_guard:
                    proposal = self._command_proposals.get(proposal_id)
                    if proposal is None or proposal["project_id"] != project_id:
                        raise ValidationError("Proposal is unavailable for this project.",
                                              code="REVISION_UNAVAILABLE")
                    document = copy.deepcopy(proposal["document"])
                return Document.from_dict(document).to_dict()
            raise ValidationError("Comparison selector must contain revision or proposal_id.")

        first = resolve(before)
        second = resolve(after)
        result = compare_documents(
            first, second, self.kernel,
            absolute_tolerance=absolute_tolerance, relative_tolerance=relative_tolerance,
        )
        result["project_id"] = project_id
        result["current_revision"] = project["document"]["revision"]
        result["selectors"] = {"before": copy.deepcopy(before), "after": copy.deepcopy(after)}
        result["snapshot_provenance"] = "saved_archive_or_process_local_preview"
        return result

    def check_project(
        self, project_id: str, expected_revision: int, *,
        specification: dict[str, Any] | None = None, strict: bool = False,
        export_body_id: str | None = None, require_assembly: bool = False,
        require_drawings: bool = False, max_bodies: int = 128,
        time_budget_seconds: float = 30.0, deterministic_rebuild: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Read-only headless validation of one saved project revision."""
        from .project_checks import check_project
        return check_project(
            self, project_id, expected_revision, specification=specification,
            strict=strict, export_body_id=export_body_id, require_assembly=require_assembly,
            require_drawings=require_drawings, max_bodies=max_bodies,
            time_budget_seconds=time_budget_seconds, deterministic_rebuild=deterministic_rebuild,
            cancelled=cancelled,
        )

    def validate_body_project_check(
        self, project_id: str, expected_revision: int, *,
        specification: dict[str, Any] | None = None, strict: bool = False,
        persist: bool = False, export_body_id: str | None = None,
        require_assembly: bool = False, require_drawings: bool = False,
        max_bodies: int = 128, time_budget_seconds: float = 30.0,
        deterministic_rebuild: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if type(persist) is not bool:
            raise ValidationError("persist must be a boolean.")
        result = self.check_project(
            project_id, expected_revision, specification=specification,
            strict=strict, export_body_id=export_body_id,
            require_assembly=require_assembly, require_drawings=require_drawings,
            max_bodies=max_bodies, time_budget_seconds=time_budget_seconds,
            deterministic_rebuild=deterministic_rebuild, cancelled=cancelled,
        )
        return self._maybe_persist_validation(project_id, "project_check", result, persist)

    def validate_body_specification(
        self, project_id: str, expected_revision: int, specification: dict[str, Any],
        *, persist: bool = False,
    ) -> dict[str, Any]:
        return self._engineering_validation.validate_model_specification(
            project_id, expected_revision, specification, persist=persist,
        )

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

    def stop_agent_owner(self, project_id: str, owner_id: str) -> dict[str, Any]:
        return self._command_activity.stop_agent_owner(project_id, owner_id)

    @staticmethod
    def _check_revision(document: Document, expected_revision: Any) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValidationError("expected_revision must be a non-negative integer.")
        if document.revision != expected_revision:
            raise ConflictError(
                "Saved revision changed; inspect the project before continuing.", code="REVISION_CONFLICT",
                diagnostics={"category": "stale_state", "revision": document.revision,
                             "expected_revision": expected_revision,
                             "failed_precondition": "the command revision must equal the current saved document revision",
                             "correction_hint": "Inspect the current project state and rebuild the request against its saved revision."},
            )

    def resume_agent(
        self, project_id: str, owner_id: str, expected_revision: int, *, compact_response: bool = False,
    ) -> dict[str, Any]:
        return self._command_activity.resume_agent(
            project_id, owner_id, expected_revision, compact_response=compact_response,
        )

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

    def execute_agent_command(
        self, project_id: str, owner_id: str, expected_revision: int, command: dict[str, Any],
        lease_id: str | None = None,
    ) -> dict[str, Any]:
        return self._command_activity.execute_agent_command(
            project_id, owner_id, expected_revision, command, lease_id,
        )

    def preview_commands(self, project_id: str, expected_revision: int, commands: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate an ordered command batch on an isolated copy without saving it."""
        if not isinstance(commands, list) or not commands or len(commands) > 100:
            raise ValidationError("commands must contain between 1 and 100 command objects.")
        with self._project_guard(project_id):
            self._require_project_policy(project_id, "read", "Project is not available for preview.")
            project = self.workspace.load(project_id, include_history=False)
            saved = Document.from_validated_dict(project["document"])
            self._check_revision(saved, expected_revision)
            staged = saved.clone()
            # File publishing and source removal are intentionally unavailable in previews.
            preview_system = CommandSystem(self.kernel, resolve_scan=self.workspace.resolve_folder_file,
                                           resolve_project_file=self.workspace.resolve_project_file)
            results = []
            for command in commands:
                if not isinstance(command, dict):
                    raise ValidationError("Every preview command must be an object.")
                if command.get("type") in {"import_scan", "import_project_scan", "import_project_point_cloud",
                                             "set_scan_parameter", "process_point_cloud", "process_mesh", "export_brep"} or (
                    command.get("type") == "remove_body" and command.get("remove_source_file") is True
                ):
                    raise ValidationError("This command has external file effects and cannot be previewed.")
                results.append(preview_system.execute(staged, copy.deepcopy(command), project_id=project_id))
                staged.validate()
                self._validate_geometry(staged)
            staged.revision = saved.revision + len(commands)
            staged.validate()
            self._validate_geometry(staged)
            before = {item["id"]: item for item in saved.objects}
            after = {item["id"]: item for item in staged.objects}
            added = [copy.deepcopy(after[key]) for key in sorted(after.keys() - before.keys())]
            changed = [copy.deepcopy(after[key]) for key in sorted(before.keys() & after.keys())
                       if before[key] != after[key]]
            proposal_id = str(uuid4())
            proposal = {"project_id": project_id, "revision": saved.revision,
                        "commands": copy.deepcopy(commands), "document": staged.to_dict(copy_objects=False),
                        "results": copy.deepcopy(results), "created_at": time.time()}
            with self._proposal_guard:
                if len(self._command_proposals) >= 128:
                    self._command_proposals.pop(next(iter(self._command_proposals)))
                self._command_proposals[proposal_id] = proposal
            return {"proposal_id": proposal_id, "revision": saved.revision,
                    "prospective_revision": staged.revision, "results": results,
                    "measurements": [copy.deepcopy(result) for result in results], "warnings": [],
                    "dependency_effects": [item for item in added + changed if item.get("type") == "feature"],
                    "document": staged.to_dict(),
                    "diff": {"added_ids": sorted(after.keys() - before.keys()), "added": added,
                             "removed_ids": sorted(before.keys() - after.keys()),
                             "changed_ids": sorted(key for key in before.keys() & after.keys() if before[key] != after[key]),
                             "changed": changed}}

    def commit_command_proposal(self, project_id: str, lease_id: str, expected_revision: int,
                                proposal_id: str, *, expected_owner_type: OwnerType = OwnerType.AGENT) -> dict[str, Any]:
        """Atomically apply a previously previewed batch under a fresh lease."""
        with self._project_guard(project_id):
            lease = self.leases.require(project_id, lease_id)
            if lease.owner_type is not expected_owner_type:
                raise ConflictError("This API requires an agent edit lease.", code="PROJECT_LOCKED")
            self.leases.check_agent_command(project_id, lease.lease_id)
            self._require_project_policy(project_id, "write", "Project has an operation in progress.", valid_lease=True)
            project = self.workspace.load(project_id, include_history=False)
            saved = Document.from_validated_dict(project["document"])
            self._check_revision(saved, expected_revision)
            with self._proposal_guard:
                proposal = self._command_proposals.get(proposal_id)
            if proposal is None or proposal["project_id"] != project_id or proposal["revision"] != expected_revision:
                raise ConflictError("Proposal is missing, already consumed, or bound to another revision.", code="PROPOSAL_STALE")
            if lease.acquired_at <= proposal["created_at"]:
                raise ConflictError("Commit requires an agent lease acquired after the preview.", code="PROPOSAL_LEASE_REQUIRED")
            staged = Document.from_validated_dict(proposal["document"])
            results = copy.deepcopy(proposal["results"])
            if staged.revision != saved.revision + len(results):
                raise ConflictError("Proposal receipt is inconsistent; preview the batch again.", code="PROPOSAL_STALE")
            staged.validate()
            self._validate_geometry(staged)
            next_project = dict(project)
            validated_history_prefix = project.get("_history_count", 0) + len(project["history"])
            next_project["document"] = staged.to_dict(copy_objects=False)
            next_project["history"].extend({"actor_type": lease.owner_type.value, "actor_id": lease.owner_id,
                                            "command": copy.deepcopy(command), "timestamp": datetime.now(timezone.utc).isoformat(),
                                            "result": copy.deepcopy(result), "revision": saved.revision + index}
                                           for index, (command, result) in enumerate(zip(proposal["commands"], results), 1))
            next_project["modified_at"] = datetime.now(timezone.utc).isoformat()
            self.workspace.save(
                next_project, validated_history_prefix=validated_history_prefix, document_validated=True,
            )
            with self._proposal_guard:
                self._command_proposals.pop(proposal_id, None)
            for index, (command, result) in enumerate(zip(proposal["commands"], results), 1):
                operation_id = str(uuid4())
                receipt = {"schema_version": 2, "operation_id": operation_id,
                           "revision": saved.revision + index, "command": command.get("type"),
                           "outcome": "completed"}
                if isinstance(result.get("feature_id"), str):
                    receipt["feature_id"] = result["feature_id"]
                self.events.publish("command.started", project_id, {
                    "command": command.get("type"), "actor_id": lease.owner_id,
                    "actor_type": lease.owner_type.value, "operation_id": operation_id,
                })
                self.events.publish("agent.activity", project_id, {
                    "operation_id": operation_id, "actor_type": lease.owner_type.value,
                    "actor_id": lease.owner_id, "action": self.commands.activity_action(command.get("type")),
                    "command": command.get("type"), "state": "completed", "phase": "completed",
                    "steps": [{"phase": "completed", "state": "completed"}], "result": result,
                    "error": None, "updated_at": datetime.now(timezone.utc).isoformat(),
                })
                self.events.publish("command.completed", project_id, receipt)
            self.events.publish("document.changed", project_id, {"revision": staged.revision})
            return {"document": staged.to_dict(), "results": results, "revision": staged.revision}

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
                try:
                    self.kernel.validate(geometry)
                except CadyxError as exc:
                    if exc.code == "VALIDATION_ERROR":
                        exc.code = "INVALID_GEOMETRY"
                    exc.add_diagnostics(category="kernel", body_id=item["id"], entity_id=item["id"],
                                        revision=document.revision,
                                        failed_precondition="every saved body must pass the geometry-kernel validity check",
                                        correction_hint="Inspect the body's feature inputs and topology, then revise the upstream modeling operation.")
                    raise
            if item.get("type") != "feature":
                continue
            if item.get("operation") in {"pad_sketch_to_body", "rib_from_sketch", "pocket_sketch"}:
                body = by_id.get(item.get("body_id"))
                source = by_id.get(item.get("source_body_id"))
                feature_geometry = item.get("geometry")
                sketch_id = item.get("source_sketch_id")
                if not isinstance(sketch_id, str):
                    raise ValidationError("Sketch manufacturing feature source sketch id is invalid.")
                sketch = by_id.get(sketch_id)
                parameters = item.get("parameters")
                if (body is None or source is None or sketch is None or sketch.get("type") != "sketch"
                        or feature_geometry != body.get("geometry") or not isinstance(parameters, dict)):
                    raise ValidationError("Sketch manufacturing feature geometry or source references are invalid.")
                plane, profiles = closed_profiles(document.objects, sketch_id)
                if "profile_groups" in parameters:
                    expected = grouped_profile_feature_geometry(
                        self.kernel, source["geometry"], sketch, document.objects, profiles,
                        parameters["profile_groups"], plane=plane, depth=parameters["depth"],
                        centered=parameters["centered"], operation=item.get("operation"),
                    )
                    if expected != feature_geometry:
                        raise ValidationError("Sketch manufacturing feature geometry is out of date with its grouped profiles.")
                    continue
                if len(profiles) == 1:
                    segments = sketch_segments(document.objects, sketch_id)
                    candidates = [segments, None] if segments is not None else [None]
                else:
                    segments = None
                    segment_profiles = sketch_segment_profiles(document.objects, sketch_id)
                    candidates = [segment_profiles, None] if segment_profiles is not None else [None]
                native_plane = kernel_plane(sketch, plane)
                # Older documents without per-contour arc descriptors remain readable.
                for candidate in candidates:
                    pad_tool: GeometryDescriptor
                    if len(profiles) == 1:
                        options = {"segments": candidate} if candidate is not None else {}
                        pad_tool = cast(GeometryDescriptor, self.kernel.create_extruded_profile(
                            profiles[0], plane=native_plane, depth=parameters["depth"],
                            centered=parameters["centered"], **options,
                        ))
                    else:
                        options = {"segment_profiles": candidate} if candidate is not None else {}
                        pad_tool = cast(GeometryDescriptor, self.kernel.create_extruded_profile_with_holes(
                            profiles, plane=native_plane, depth=parameters["depth"],
                            centered=parameters["centered"], **options,
                        ))
                    pad_tool = place_geometry(sketch, pad_tool, self.kernel)
                    pad_expected = (self.kernel.cut_solids(source["geometry"], pad_tool)
                                    if item.get("operation") == "pocket_sketch"
                                    else self.kernel.fuse_profiles(source["geometry"], pad_tool))
                    if pad_expected == feature_geometry:
                        break
                else:
                    raise ValidationError("Sketch manufacturing feature geometry is out of date with its source sketch or body.")
                continue
            if item.get("operation") in {"fuse_bodies", "cut_bodies"}:
                feature_geometry = item.get("geometry")
                body = by_id.get(item.get("body_id"))
                base = by_id.get(item.get("base_body_id"))
                tool_body = by_id.get(item.get("tool_body_id"))
                if (body is None or body.get("type") != "body"
                        or base is None or base.get("type") != "body"
                        or tool_body is None or tool_body.get("type") != "body"):
                    raise ValidationError("Boolean feature refers to a missing body.")
                if feature_geometry != body.get("geometry"):
                    raise ValidationError("Boolean feature geometry must match its body.")
                expected_kind = {"fused_profiles", "fused_solids"} if item.get("operation") == "fuse_bodies" else {"cut_solids"}
                if (not isinstance(feature_geometry, dict)
                        or feature_geometry.get("kind") not in expected_kind
                        or feature_geometry.get("base") != base.get("geometry")
                        or feature_geometry.get("tool") != tool_body.get("geometry")):
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
                shell_expected = dict(self.kernel.create_ruled_shell(
                    parameters["patches"], sewing_tolerance=parameters["sewing_tolerance"],
                    compute_volume=False,
                ))
                shell_expected.pop("volume", None)
                comparable_geometry = {key: value for key, value in feature_geometry.items() if key != "volume"}
                if shell_expected != comparable_geometry:
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
                sections, offsets, section_axes = [], [], []
                for group in groups:
                    group_profiles, group_offsets = [], []
                    for sketch_id in group:
                        sketch = by_id.get(sketch_id)
                        axis = sketch.get("section_normal_axis", {"XY": "Z", "XZ": "Y"}.get(sketch.get("plane"))) if sketch else None
                        expected_plane = "XZ" if axis == "Y" else "XY" if axis == "Z" else None
                        if (sketch is None or sketch.get("type") != "sketch" or sketch.get("plane") != expected_plane):
                            raise ValidationError("Loft feature source sketches must match their section normal axis.")
                        section_axes.append(axis)
                        _, profile = closed_profile(document.objects, sketch_id)
                        group_profiles.append(profile)
                        offset = sketch.get("section_offset", 0.0)
                        if isinstance(offset, bool) or not isinstance(offset, (int, float)):
                            raise ValidationError("Loft source sketch section offset is invalid.")
                        group_offsets.append(float(offset))
                    if not group_offsets or any(value != group_offsets[0] for value in group_offsets):
                        raise ValidationError("Loft feature contour group has inconsistent section offsets.")
                    sections.append(group_profiles)
                    offsets.append(group_offsets[0])
                if len(set(section_axes)) != 1 or section_axes[0] != parameters.get("section_axis", "Y"):
                    raise ValidationError("Loft feature normal axis does not match its saved section sketches.")
                if any(len(group) != 1 for group in sections):
                    loft_expected = dict(self.kernel.create_multicontour_loft(
                        sections, section_offsets=offsets, compute_volume=False, section_axis=section_axes[0],
                    ))
                else:
                    loft_expected = dict(self.kernel.create_lofted_profiles(
                        [group[0] for group in sections], section_offsets=offsets,
                        alignment=feature_geometry.get("profile_alignment", "independent-start-v1"),
                        section_axis=section_axes[0],
                    ))
                    if "profile_alignment" not in feature_geometry:
                        loft_expected.pop("profile_alignment", None)
                if loft_expected.get("kind") == "multicontour_loft":
                    loft_expected.pop("volume", None)
                    comparable_geometry = copy.deepcopy(feature_geometry)
                    comparable_geometry.pop("volume", None)
                    for branch in loft_expected["branches"] + comparable_geometry.get("branches", []):
                        branch.pop("volume", None)
                else:
                    comparable_geometry = feature_geometry
                if loft_expected != comparable_geometry:
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
                if not isinstance(source_id, str):
                    raise ValidationError("Sweep feature source sketch id is invalid.")
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
            if not isinstance(source_id, str):
                raise ValidationError("Profile feature source sketch id is invalid.")
            if body is None or feature_geometry != body.get("geometry"):
                raise ValidationError("Profile feature geometry must match its body.")
            operation = item["operation"]
            parameters = item.get("parameters")
            if not isinstance(feature_geometry, dict) or not isinstance(parameters, dict):
                raise ValidationError("Profile feature geometry is missing.")
            source_sketch = by_id.get(source_id)
            generated_gear = (source_sketch.get("generated_gear_profile")
                              if isinstance(source_sketch, dict) else None)

            def accepts_legacy_gear_geometry(plane: str) -> bool:
                # Existing projects can contain valid gear B-reps produced before
                # the editable spline sketch representation was introduced. Keep
                # them readable; the next parameter rebuild regenerates the B-rep.
                if (operation != "extrude_sketch" or not isinstance(generated_gear, dict)
                        or generated_gear.get("operation") not in {
                            "add_sketch_involute_gear", "add_sketch_internal_involute_gear",
                        }):
                    return False
                return (feature_geometry.get("kind") in {"extruded_profile", "profile_with_holes"}
                        and feature_geometry.get("plane") == plane
                        and feature_geometry.get("depth") == parameters.get("depth")
                        and feature_geometry.get("centered", False) == parameters.get("centered", False)
                        and isinstance(feature_geometry.get("profile"), list))

            def profile_geometry_matches(expected: dict[str, Any]) -> bool:
                if expected == feature_geometry:
                    return True
                if (expected.get("kind") != "imported_brep"
                        or feature_geometry.get("kind") != "imported_brep"):
                    return False
                comparison = self.kernel.compare_preserved_volume(expected, feature_geometry)
                lost = comparison.get("lost_volume")
                added = comparison.get("added_volume")
                resolution = comparison.get("estimated_volume_resolution")
                if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                       for value in (lost, added, resolution)):
                    raise ValidationError("OCCT returned invalid profile-geometry comparison metrics.")
                return lost <= resolution and added <= resolution

            spline_profile = closed_spline_profile(document.objects, source_id)
            if spline_profile is not None:
                plane, profile, curve_points = spline_profile
                if operation == "extrude_sketch":
                    sketch = by_id.get(source_id)
                    if sketch is None:
                        raise ValidationError("Extrusion source sketch is missing.")
                    spline_expected = self.kernel.create_extruded_profile(
                        profile, plane=kernel_plane(sketch, plane), depth=parameters["depth"],
                        centered=parameters.get("centered", False), curve_points=curve_points,
                    )
                    spline_expected = place_geometry(sketch, spline_expected, self.kernel)
                    if profile_geometry_matches(spline_expected):
                        continue
                else:
                    spline_expected = self.kernel.create_revolved_profile(
                        profile, angle_degrees=parameters["angle_degrees"], curve_points=curve_points,
                    )
                    if profile_geometry_matches(spline_expected):
                        continue
                if accepts_legacy_gear_geometry(plane):
                    continue
                raise ValidationError("Spline profile feature geometry is out of date with its source sketch or parameters.")
            if operation == "extrude_sketch":
                plane, profiles = closed_profiles(document.objects, source_id)
                sketch = by_id.get(source_id)
                if sketch is None:
                    raise ValidationError("Extrusion source sketch is missing.")
                native_plane = kernel_plane(sketch, plane)
                if len(profiles) > 1:
                    curve_profiles = generated_gear_curve_profiles(document.objects, source_id, profiles)
                    segment_profiles = sketch_segment_profiles(document.objects, source_id)
                    profiles_expected = self.kernel.create_extruded_profile_with_holes(
                        profiles, plane=native_plane, depth=parameters["depth"],
                        centered=parameters.get("centered", False),
                        curve_profiles=curve_profiles,
                        segment_profiles=segment_profiles,
                    )
                    profiles_expected = place_geometry(sketch, profiles_expected, self.kernel)
                    if not profile_geometry_matches(profiles_expected) and segment_profiles is not None:
                        profiles_expected = self.kernel.create_extruded_profile_with_holes(
                            profiles, plane=native_plane, depth=parameters["depth"],
                            centered=parameters.get("centered", False),
                            curve_profiles=curve_profiles,
                        )
                        profiles_expected = place_geometry(sketch, profiles_expected, self.kernel)
                    if not profile_geometry_matches(profiles_expected):
                        if accepts_legacy_gear_geometry(plane):
                            continue
                        raise ValidationError(
                            f"Profile feature geometry for {body.get('name') or item.get('body_id')} is out of date "
                            "with its source sketch or parameters."
                        )
                    continue
                profile = profiles[0]
                expected = self.kernel.create_extruded_profile(
                    profile, plane=native_plane, depth=parameters["depth"],
                    centered=parameters.get("centered", False),
                    segments=sketch_segments(document.objects, source_id),
                )
                expected = place_geometry(sketch, expected, self.kernel)
                if expected == feature_geometry:
                    continue
                if accepts_legacy_gear_geometry(plane):
                    continue
                raise ValidationError("Profile feature geometry is out of date with its source sketch or parameters.")
            else:
                plane, profile = closed_profile(document.objects, source_id)
            expected_kind = "extruded_profile" if operation == "extrude_sketch" else "revolved_profile"
            if not isinstance(parameters, dict):
                raise ValidationError("Profile feature parameters are invalid.")
            if feature_geometry.get("kind") != expected_kind or feature_geometry.get("plane") != plane or feature_geometry.get("profile") != profile:
                raise ValidationError("Profile feature geometry is out of date with its source sketch.")
            profile_parameter_name = "depth" if operation == "extrude_sketch" else "angle_degrees"
            if feature_geometry.get(profile_parameter_name) != parameters.get(profile_parameter_name):
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
        summary = {
            key: project[key]
            for key in ("id", "format", "version", "name", "url_slug", "created_at", "modified_at")
        }
        summary["project_type"] = project.get("project_type", "single_model")
        return summary
