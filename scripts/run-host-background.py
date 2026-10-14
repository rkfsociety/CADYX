"""Run the CADYX host without a console and keep logs for Task Scheduler."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    log_directory = Path(os.environ["LOCALAPPDATA"]) / "CADYX"
    log_directory.mkdir(parents=True, exist_ok=True)
    stdout_path = log_directory / "host.stdout.log"
    stderr_path = log_directory / "host.stderr.log"

    for log_path in (stdout_path, stderr_path):
        if log_path.exists():
            os.replace(log_path, log_path.with_name(log_path.name + ".previous"))

    os.chdir(project_root)
    sys.path.insert(0, str(project_root))
    sys.argv = [
        "cadyx",
        "--host",
        "127.0.0.1",
        "--port",
        "8888",
        "--projects-dir",
        str(project_root / "projects"),
    ]

    exit_code = 1
    with stdout_path.open("w", encoding="utf-8", buffering=1) as stdout, stderr_path.open(
        "w", encoding="utf-8", buffering=1
    ) as stderr:
        sys.stdout = stdout
        sys.stderr = stderr
        try:
            from cadyx.__main__ import main as run_host

            run_host()
        except BaseException:
            traceback.print_exc()
        # A normal return means the server stopped unexpectedly; Task Scheduler restarts it.

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
