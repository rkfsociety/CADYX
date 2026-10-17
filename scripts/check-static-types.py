"""Run strict mypy checks plus an exact legacy-error ratchet for core services."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "pyproject.toml"
BASELINE_PATH = ROOT / "tests" / "mypy-error-baseline.json"
ERROR_PATTERN = re.compile(
    r"^(?P<path>.+?\.py):\d+(?::\d+)?: error: .+? \[(?P<code>[^\]]+)\]$"
)


def _configured_files() -> tuple[list[str], list[str]]:
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    quality = config["tool"]["cadyx"]["static-quality"]
    return list(quality["mypy-strict-files"]), list(quality["mypy-ratchet-files"])


def _run_mypy(files: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            *files,
            "--no-error-summary",
            "--no-pretty",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _module_name(path: str) -> str:
    normalized = path.replace("\\", "/")
    return Path(normalized).stem


def _error_counts(output: str) -> dict[str, Counter[str]]:
    counts: dict[str, Counter[str]] = {}
    for line in output.splitlines():
        if ": error:" not in line:
            continue
        match = ERROR_PATTERN.match(line)
        if match is None:
            raise RuntimeError(f"Could not parse mypy error line: {line}")
        counts.setdefault(_module_name(match.group("path")), Counter())[match.group("code")] += 1
    return counts


def _load_baseline() -> dict[str, Counter[str]]:
    raw = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("mypy error baseline must be an object")
    result: dict[str, Counter[str]] = {}
    for module, codes in raw.items():
        if not isinstance(module, str) or not isinstance(codes, dict):
            raise RuntimeError("mypy error baseline entries must map module names to code counts")
        counter: Counter[str] = Counter()
        for code, count in codes.items():
            if not isinstance(code, str) or type(count) is not int or count < 0:
                raise RuntimeError("mypy error baseline counts must be non-negative integers")
            if count:
                counter[code] = count
        result[module] = counter
    return result


def _print_process_output(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)


def main() -> int:
    strict_files, ratchet_files = _configured_files()

    strict = _run_mypy(strict_files)
    if strict.returncode != 0:
        _print_process_output(strict)
        print("Strict mypy targets failed.", file=sys.stderr)
        return 1

    ratchet = _run_mypy(ratchet_files)
    if ratchet.returncode not in (0, 1):
        _print_process_output(ratchet)
        print(f"mypy ratchet exited unexpectedly with {ratchet.returncode}.", file=sys.stderr)
        return 1

    expected = _load_baseline()
    actual = _error_counts(ratchet.stdout + ratchet.stderr)
    for file_name in ratchet_files:
        actual.setdefault(Path(file_name).stem, Counter())

    if actual != expected:
        _print_process_output(ratchet)
        print("mypy legacy-error ratchet changed; only baseline reductions are allowed.", file=sys.stderr)
        modules = sorted(set(expected) | set(actual))
        for module in modules:
            if actual.get(module, Counter()) != expected.get(module, Counter()):
                print(
                    f"  {module}: expected {dict(expected.get(module, Counter()))}, "
                    f"got {dict(actual.get(module, Counter()))}",
                    file=sys.stderr,
                )
        return 1

    total = sum(sum(counter.values()) for counter in actual.values())
    print(f"mypy ratchet matched {total} documented legacy errors across {len(ratchet_files)} core-service targets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
