"""Compare full and paginated reads for one representative saved model.

The benchmark is opt-in and is not run locally before a push.
Token figures are estimates (UTF-8 bytes / 4), not measured tokenizer counts.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import tracemalloc
from pathlib import Path

from cadyx.core import CadyxCore


def _p95(values: list[float] | list[int]) -> float | int:
    return values[max(0, math.ceil(0.95 * len(values)) - 1)]


def measure(action, samples: int) -> dict:
    times = []
    peaks = []
    lengths = []
    for _ in range(samples):
        tracemalloc.start()
        start = time.perf_counter()
        payload = action()
        elapsed = (time.perf_counter() - start) * 1000
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        times.append(elapsed)
        peaks.append(peak)
        lengths.append(len(data))
    times.sort()
    peaks.sort()
    lengths.sort()
    return {"samples": samples, "bytes_p50": statistics.median(lengths),
            "estimated_tokens_p50": round(statistics.median(lengths) / 4),
            "latency_ms_p50": round(statistics.median(times), 3),
            "latency_ms_p95": round(_p95(times), 3),
            "peak_allocated_bytes_p50": statistics.median(peaks),
            "peak_allocated_bytes_p95": _p95(peaks)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects-dir", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--section", choices=("summary", "entities", "parameters", "changes"), default="entities")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    if not 5 <= args.samples <= 200:
        parser.error("--samples must be 5 through 200")
    core = CadyxCore(str(args.projects_dir))
    options = {"section": args.section, "limit": args.limit}
    if args.section == "changes":
        options["since_revision"] = 0
    core.inspect_project(args.project_id)
    core.inspect_compact(args.project_id, **options)
    output = {
        "project_id": args.project_id,
        "revision": core.workspace.load(args.project_id)["document"]["revision"],
        "comparison_scope": f"full project versus {args.section} first page (limit {args.limit})",
        "full": measure(lambda: core.inspect_project(args.project_id), args.samples),
        "compact": measure(lambda: core.inspect_compact(args.project_id, **options), args.samples),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
