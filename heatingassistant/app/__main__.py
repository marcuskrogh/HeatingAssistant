"""HTTP entrypoint for the Heating Assistant application."""

from __future__ import annotations

import argparse
import asyncio
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from typing import Any

from heatingassistant.app.runtime import HeatingRuntime


class _Handler(BaseHTTPRequestHandler):
    runtime: HeatingRuntime

    server_version = "HeatingAssistantApp/2.0.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/api/health":
            self._send_json(self.runtime.status())
            return
        if self.path in {"/", "/index.html"}:
            self._send_html(_INDEX_HTML)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        """Keep the stub quiet unless the real runtime adds structured logging."""

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Heating Assistant</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
  <main>
    <h1>Heating Assistant</h1>
    <p>Ingress UI placeholder for the MQTT-based application runtime.</p>
  </main>
</body>
</html>
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the HeatingAssistant App")
    parser.add_argument("--host", default=os.environ.get("HEATING_ASSISTANT_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("HEATING_ASSISTANT_PORT", "8099")),
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("HEATING_ASSISTANT_DATA_DIR", "/data"),
    )
    parser.add_argument("--ha-runtime", action="store_true")
    return parser


async def _start_runtime(runtime: HeatingRuntime) -> None:
    await runtime.start()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)

    runtime = HeatingRuntime(Path(args.data_dir))
    asyncio.run(_start_runtime(runtime))

    handler = type("HeatingAssistantHandler", (_Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"HeatingAssistant App listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        asyncio.run(runtime.stop())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
