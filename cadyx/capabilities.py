"""Machine-readable discovery for the public Agent API and model commands."""

from __future__ import annotations

from typing import Any


# Parameter names are an operator guide; CommandSystem and Core validators remain
# authoritative for types, ranges, cross-field rules, references and geometry.
COMMAND_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {"type": "create_box", "description": "Create an editable box B-rep.", "required": ["width", "height", "depth"], "optional": ["name"], "constraints": ["Dimensions must be positive finite numbers."]},
    {"type": "create_sketch", "description": "Create an XY or XZ sketch.", "required": [], "optional": ["name", "plane"], "constraints": ["plane is XY or XZ; default XY."]},
    {"type": "add_sketch_element", "description": "Add a point, line, or circle to a sketch.", "required": ["sketch_id", "element_type"], "optional": ["x", "y", "start_id", "end_id", "center_id", "radius"], "constraints": ["element_type is point (x,y), line (start_id,end_id), or circle (center_id,radius). Prefer the atomic geometry commands when possible."]},
    {"type": "add_sketch_polyline", "description": "Add connected points and lines as one operation.", "required": ["sketch_id", "points"], "optional": ["closed"], "constraints": ["Coordinates must form a valid open path or closed profile."]},
    {"type": "add_sketch_rectangle", "description": "Add a rectangle profile.", "required": ["sketch_id", "x", "y", "width", "height"], "optional": [], "constraints": ["Width and height must be positive."]},
    {"type": "add_sketch_circle", "description": "Add a circle profile.", "required": ["sketch_id", "x", "y", "radius"], "optional": [], "constraints": ["Radius must be positive."]},
    {"type": "add_sketch_involute_gear", "description": "Generate an external involute spur-gear outline.", "required": ["sketch_id", "module", "tooth_count"], "optional": ["pressure_angle_degrees", "center_x", "center_y", "rotation_degrees", "backlash"], "constraints": ["See the engineering profile contract for supported gear limits."]},
    {"type": "add_sketch_constraint", "description": "Add a checked sketch constraint.", "required": ["sketch_id", "kind", "references"], "optional": ["value"], "constraints": ["Constraints validate supplied geometry; they do not solve or move it."]},
    {"type": "update_sketch_elements", "description": "Update supported sketch point or circle values atomically.", "required": ["updates"], "optional": [], "constraints": ["Each update has object_id and non-empty changes; dependent features rebuild atomically."]},
    {"type": "replace_sketch_profile", "description": "Replace a closed sketch profile atomically.", "required": ["sketch_id", "points"], "optional": [], "constraints": ["Profile must remain closed and valid."]},
    {"type": "replace_sketch_path", "description": "Replace an open sketch path atomically.", "required": ["sketch_id", "points"], "optional": [], "constraints": ["Path must remain open and valid."]},
    {"type": "remove_sketch_object", "description": "Remove an unreferenced sketch object.", "required": ["object_id"], "optional": [], "constraints": ["A sketch used by a solid feature cannot be removed."]},
    {"type": "extrude_sketch", "description": "Create an editable OCCT extrusion from a closed profile.", "required": ["sketch_id", "depth"], "optional": ["name", "centered"], "constraints": ["Requires a closed valid profile."]},
    {"type": "revolve_sketch", "description": "Create an editable OCCT revolution from a closed profile.", "required": ["sketch_id"], "optional": ["name", "angle_degrees"], "constraints": ["Requires a closed valid profile; the document contract defines its revolution axis."]},
    {"type": "sweep_sketch", "description": "Sweep a circular section along an open sketch path.", "required": ["sketch_id", "radius"], "optional": ["name"], "constraints": ["Creates an editable OCCT swept B-rep; requires a valid open path."]},
    {"type": "fuse_bodies", "description": "Boolean-fuse two editable solid B-reps.", "required": ["base_body_id", "tool_body_id"], "optional": ["name"], "constraints": ["Inputs must be different valid solids; inspect final solid count and intersections."]},
    {"type": "fillet_edges", "description": "Fillet topology-selected B-rep edges.", "required": ["body_id", "edge_indices", "radius"], "optional": ["name"], "constraints": ["Refresh topology after upstream edits; edge indices come from the current topology response."]},
    {"type": "create_metric_thread", "description": "Create a native single-start external ISO metric thread.", "required": ["major_diameter", "pitch", "length"], "optional": ["name", "handedness"], "constraints": ["See the Document Model v3 engineering feature contract for limits."]},
    {"type": "set_parameter", "description": "Change a supported feature parameter and rebuild dependents.", "required": ["feature_id", "name", "value"], "optional": [], "constraints": ["Only parameters supported by the selected feature can be changed."]},
    {"type": "import_scan", "description": "Import and process a workspace PLY as an editable triangle mesh.", "required": ["folder", "file"], "optional": ["name", "parameters"], "constraints": ["The source remains unchanged; output is a triangle mesh, not an OCCT B-rep. Scale follows source coordinates."]},
    {"type": "set_scan_parameter", "description": "Reprocess a scan feature from its unchanged source.", "required": ["feature_id", "name", "value"], "optional": [], "constraints": ["Rejects a changed source file; parameter bounds are in the Document Model v3 contract."]},
    {"type": "import_step", "description": "Import a single-solid STEP model as an editable OCCT B-rep.", "required": ["file"], "optional": ["name"], "constraints": ["Reads a direct project workspace file; single-solid validity is enforced."]},
    {"type": "resize_imported_thread", "description": "Resize a supported imported thread feature.", "required": ["body_id", "x_start", "x_end", "major_diameter", "diameter_delta"], "optional": ["axis_origin_y", "axis_origin_z", "name"], "constraints": ["Uses the exact range and axis parameters required by its feature validator."]},
    {"type": "transform_imported_brep", "description": "Create a rigidly transformed editable imported B-rep.", "required": ["body_id", "rotation_axis", "angle_degrees", "translation"], "optional": ["name"], "constraints": ["Rotation axis and translation are XYZ vectors."]},
    {"type": "export_brep", "description": "Publish a revision-bound STEP and optional STL delivery with verification manifest.", "required": ["body_id"], "optional": ["include_stl", "linear_deflection", "angular_deflection"], "constraints": ["Requires expected_revision; exports only a validated single-solid B-rep and never replaces user files."]},
)


def agent_api_capabilities() -> dict[str, Any]:
    """Describe implemented routes without claiming validation beyond the source contracts."""
    return {
        "api_version": 1,
        "read_only": True,
        "selection": "POST /api/v1/agent/workspace/selection selects a project or workspace folder; GET returns current host selection.",
        "routes": [
            {"method": "GET", "path": "/api/v1/health", "description": "Minimal readiness; unauthenticated."},
            {"method": "GET", "path": "/api/v1/agent/projects", "description": "Project records, folders and current lock snapshots."},
            {"method": "POST", "path": "/api/v1/agent/projects", "description": "Create a project and select it in the viewer."},
            {"method": "GET", "path": "/api/v1/agent/workspace/selection", "description": "Current host resource selection."},
            {"method": "POST", "path": "/api/v1/agent/workspace/selection", "description": "Select an existing project or workspace folder."},
            {"method": "GET", "path": "/api/v1/agent/projects/{id}", "description": "Saved document, history, revision and lock snapshot."},
            {"method": "GET", "path": "/api/v1/agent/projects/{id}/files[/{name}]", "description": "List/read direct project files; no API write route."},
            {"method": "GET", "path": "/api/v1/agent/capabilities", "description": "This read-only route discovery and command parameter guide."},
            {"method": "GET", "path": "/api/v1/agent/projects/{id}/topology?body_id=...", "description": "Current editable B-rep topology; refresh after upstream edits."},
            {"method": "GET", "path": "/api/v1/agent/projects/{id}/viewport[.png]", "description": "Read-only scene or model image; PNG is returned as image content by MCP."},
            {"method": "POST", "path": "/api/v1/agent/projects/{id}/viewport/camera", "description": "Animate browser camera without document mutation."},
            {"method": "GET|POST", "path": "/api/v1/agent/projects/{id}/dashboard", "description": "Read/report workflow; reports do not mutate the CAD document."},
            {"method": "GET", "path": "/api/v1/agent/projects/{id}/activity", "description": "Recent operation snapshots."},
            {"method": "POST", "path": "/api/v1/agent/projects/{id}/leases", "description": "Acquire an agent edit lease."},
            {"method": "POST", "path": "/api/v1/agent/projects/{id}/leases/{lease_id}/heartbeat|release", "description": "Maintain or release an existing lease."},
            {"method": "POST", "path": "/api/v1/agent/projects/{id}/leases/recover", "description": "Validated stale-lease recovery; call only after API confirms expiration."},
            {"method": "POST", "path": "/api/v1/agent/projects/{id}/commands", "description": "Execute through the Core Command System; commands require a current agent lease; pass expected_revision."},
            {"method": "POST", "path": "/api/v1/agent/projects/{id}/stop|resume", "description": "Validated stop fence and revision-checked agent resume."},
        ],
        "commands": [dict(item) for item in COMMAND_CAPABILITIES],
        "errors": [
            {"code": "PROJECT_LOCKED", "meaning": "A different owner holds the project lease."},
            {"code": "PROJECT_BUSY", "meaning": "An in-progress project transaction prevents access."},
            {"code": "LEASE_RECOVERY_REQUIRED", "meaning": "The old lease expired; validate/recover before editing."},
            {"code": "REVISION_CONFLICT", "meaning": "The saved revision changed; inspect before continuing."},
            {"code": "AGENT_STOPPING", "meaning": "The current agent lease is fenced from new commands."},
            {"code": "LEASE_EXPIRED", "meaning": "The supplied edit lease expired."},
        ],
        "contracts": [
            ".codex/docs/architecture.md#edit-leases",
            ".codex/docs/architecture.md#safe-agent-stop-and-resume",
            ".codex/docs/document-model-v3.md",
            ".codex/docs/viewport-api.md",
        ],
        "limitations": [
            "Browser is a read-only observation surface; API selection and commands are authoritative.",
            "Imported PLY scan results are triangle meshes, not B-reps; no unmeasured scale or reconstruction accuracy is claimed.",
            "Parameter names are a guide. Core/Command System validators define accepted values and geometry constraints.",
        ],
    }
