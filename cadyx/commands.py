"""Shared command facade; dispatch is composed from registered command families."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from .capabilities import COMMAND_REGISTRY
from .capabilities import COMMAND_CAPABILITIES as COMMAND_CAPABILITIES
from .errors import ValidationError
from .kernel import GeometryKernel
from .models import Document
from .scan import ScanProcessor
from .command_sketches import SketchCommands
from .command_sketch_profiles import SketchProfileCommands
from .command_sketch_curves import SketchCurveCommands
from .command_history import HistoryCommands
from .command_bodies import BodyCommands
from .command_parameters import ParameterCommands
from .command_rebuild import RebuildCommands
from .command_scan import ScanCommands
from .command_delivery import DeliveryCommands


class CommandSystem(
    SketchCommands, SketchProfileCommands, SketchCurveCommands, HistoryCommands,
    BodyCommands, ParameterCommands, RebuildCommands, ScanCommands, DeliveryCommands,
):
    def __init__(
        self,
        kernel: GeometryKernel,
        *,
        resolve_scan: Callable[[str, str], Path] | None = None,
        resolve_project_file: Callable[[str, str], Path] | None = None,
        scan_processor: ScanProcessor | None = None,
        publish_scan_output: Callable[[str, str, bytes], dict[str, Any]] | None = None,
        publish_cad_delivery: Callable[[str, dict[str, bytes]], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.kernel = kernel
        self.resolve_scan = resolve_scan
        self.resolve_project_file = resolve_project_file
        self.scan_processor = scan_processor if scan_processor is not None else ScanProcessor()
        self.publish_scan_output = publish_scan_output
        self.publish_cad_delivery = publish_cad_delivery
        self._extension_execute = COMMAND_REGISTRY.bind(self)

    @staticmethod
    def activity_action(command_name: str | None) -> str:
        return COMMAND_REGISTRY.activity_action(command_name)

    def execute(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(command, dict):
            raise ValidationError("command must be a JSON object.")
        kind = command.get("type")
        if not isinstance(kind, str) or kind not in COMMAND_REGISTRY.command_types:
            raise ValidationError(f"Unsupported command type: {kind!r}.", code="UNSUPPORTED_OPERATION",
                                  diagnostics={"category": "unsupported_operation",
                                               "failed_precondition": "command type must be present in the current capability registry",
                                               "correction_hint": "Call the capability discovery route and use a listed command type."})
        return self._extension_execute(document, command, progress=progress, project_id=project_id)
