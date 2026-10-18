"""CI entrypoint: python -m cadyx.check_project --help."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import CadyxCore
from .errors import CadyxError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Revision-bound CADYX headless project check.")
    parser.add_argument("--projects-dir", default=os.environ.get("CADYX_PROJECTS_DIR", "projects"))
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--specification", type=Path)
    parser.add_argument("--export-body-id")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--require-assembly", action="store_true")
    parser.add_argument("--require-drawings", action="store_true")
    parser.add_argument("--deterministic-rebuild", action="store_true")
    parser.add_argument("--max-bodies", type=int, default=128)
    parser.add_argument("--time-budget-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.specification.read_text(encoding="utf-8")) if args.specification else None
        core = CadyxCore(args.projects_dir)
        report = core.check_project(
            args.project_id, args.expected_revision, specification=spec,
            strict=args.strict, export_body_id=args.export_body_id,
            require_assembly=args.require_assembly, require_drawings=args.require_drawings,
            deterministic_rebuild=args.deterministic_rebuild,
            max_bodies=args.max_bodies, time_budget_seconds=args.time_budget_seconds,
        )
    except (CadyxError, OSError, ValueError) as exc:
        error = exc.as_dict() if isinstance(exc, CadyxError) else {
            "error": "CHECK_INPUT_ERROR", "message": str(exc),
        }
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
