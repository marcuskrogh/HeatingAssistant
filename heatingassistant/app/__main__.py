"""Minimal HeatingAssistant App HTTP entrypoint."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Sequence


class HeatingAssistantHandler(BaseHTTPRequestHandler):
    """Serve the HAOS App watchdog endpoint."""

    server_version = "HeatingAssistantApp/2.0.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        body = b"HeatingAssistant App\n"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the stub quiet unless the real runtime adds structured logging."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the HeatingAssistant App stub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--options-path", default="/data/options.json")
    parser.add_argument("--data-dir", default="/data")
    parser.add_argument("--ha-runtime", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), HeatingAssistantHandler)
    print(f"HeatingAssistant App listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
