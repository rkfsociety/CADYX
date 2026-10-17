"""Start the local CADYX host with ``python -m cadyx``."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .auth import AuthenticationConfigError, load_auth_token, validate_bind_auth
from .core import CadyxCore
from .web import CadyxHTTPServer, make_request_handler


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local CADYX Core and web interface.")
    parser.add_argument("--host", default=os.environ.get("CADYX_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CADYX_PORT", "8888")))
    parser.add_argument(
        "--projects-dir",
        default=os.environ.get("CADYX_PROJECTS_DIR", str(Path.cwd() / "projects")),
    )
    parser.add_argument(
        "--auth-token-file",
        default=os.environ.get("CADYX_AUTH_TOKEN_FILE"),
        help="Read the HTTP authentication token from a UTF-8 file.",
    )
    parser.add_argument(
        "--max-http-requests",
        type=_positive_int,
        default=os.environ.get("CADYX_MAX_HTTP_REQUESTS", "64"),
        help="Maximum concurrent HTTP handlers, including active SSE streams.",
    )
    parser.add_argument(
        "--max-sse-streams",
        type=_positive_int,
        default=os.environ.get("CADYX_MAX_SSE_STREAMS", "8"),
        help="Maximum concurrent server-sent-event streams.",
    )
    arguments = parser.parse_args()

    try:
        auth_token = load_auth_token(os.environ.get("CADYX_AUTH_TOKEN"), arguments.auth_token_file)
        validate_bind_auth(arguments.host, auth_token)
    except AuthenticationConfigError as exc:
        parser.error(str(exc))

    core = CadyxCore(arguments.projects_dir)
    server = CadyxHTTPServer(
        (arguments.host, arguments.port),
        make_request_handler(core, auth_token=auth_token),
        max_http_requests=arguments.max_http_requests,
        max_sse_streams=arguments.max_sse_streams,
    )
    print(f"CADYX listening at http://{arguments.host}:{server.server_port}")
    print(f"Project workspace: {core.workspace.root}")
    print(f"HTTP authentication: {'required' if auth_token else 'disabled (loopback only)'}")
    print(
        "HTTP concurrency: "
        f"{arguments.max_http_requests} handlers, {arguments.max_sse_streams} SSE streams"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CADYX.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
