"""HTTP entrypoint for the Heating Assistant application."""

from __future__ import annotations

import argparse
import asyncio
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from heatingassistant.app.runtime import HeatingRuntime

_STATIC_DIR = Path(__file__).with_name("static")


class _Handler(BaseHTTPRequestHandler):
    runtime: HeatingRuntime

    server_version = "HeatingAssistantApp/2.0.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(self.runtime.status())
            return
        if path == "/api/config":
            self._send_json(self.runtime.config())
            return
        if path == "/api/state":
            self._send_json(self.runtime.state_snapshot())
            return
        if path in {"/", "/index.html"}:
            self._send_file(_STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            self._send_static(path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path != "/api/config":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not isinstance(payload, dict):
            self.send_error(HTTPStatus.BAD_REQUEST, "config payload must be a JSON object")
            return
        try:
            config = asyncio.run(self.runtime.update_config(payload))
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(config)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path != "/api/bindings":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        source = payload.get("bindings") if isinstance(payload, dict) else payload
        if not isinstance(source, list):
            self.send_error(HTTPStatus.BAD_REQUEST, "bindings payload must be a list")
            return
        try:
            bindings = asyncio.run(self.runtime.update_bindings(source))
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json({"bindings": bindings})

    def log_message(self, format: str, *args: object) -> None:
        """Keep the stub quiet unless the real runtime adds structured logging."""

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: str) -> None:
        relative = Path(unquote(path.removeprefix("/static/")))
        if relative.is_absolute() or ".." in relative.parts:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid static path")
            return
        target = (_STATIC_DIR / relative).resolve()
        static_root = _STATIC_DIR.resolve()
        if static_root not in (target, *target.parents) or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "static file not found")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send_file(target, content_type)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "file not found")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            return json.loads(body.decode("utf-8") if body else "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("request body must be valid JSON") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the HeatingAssistant App")
    parser.add_argument("--host", default=os.environ.get("HEATING_ASSISTANT_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(
            os.environ.get(
                "HEATINGASSISTANT_PORT",
                os.environ.get("HEATING_ASSISTANT_PORT", "8100"),
            )
        ),
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
