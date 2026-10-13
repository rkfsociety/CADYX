"""Start the local CADYX host with ``python -m cadyx``."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .core import CadyxCore
from .web import CadyxHTTPServer, make_request_handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local CADYX Core and web interface.")
    parser.add_argument("--host", default=os.environ.get("CADYX_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CADYX_PORT", "8000")))
    parser.add_argument(
        "--projects-dir",
        default=os.environ.get("CADYX_PROJECTS_DIR", str(Path.cwd() / "projects")),
    )
    arguments = parser.parse_args()
    core = CadyxCore(arguments.projects_dir)
    server = CadyxHTTPServer((arguments.host, arguments.port), make_request_handler(core))
    print(f"CADYX listening at http://{arguments.host}:{server.server_port}")
    print(f"Project workspace: {core.workspace.root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CADYX.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
