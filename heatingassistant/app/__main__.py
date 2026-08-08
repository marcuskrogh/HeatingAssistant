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
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import create_mqtt_bus
from heatingassistant.persistence import load_config

_STATIC_DIR = Path(__file__).with_name("static")

# Supervisor App options (config.yaml schema) overlaid onto durable config.json.
_SUPERVISOR_OPTION_KEYS = (
    "instance_id",
    "mqtt_broker",
    "mqtt_port",
    "mqtt_username",
    "mqtt_password",
)


def merge_supervisor_options(
    data_dir: str | Path,
    options_path: str | Path | None,
) -> dict[str, Any]:
    """Load durable App config and overlay Supervisor options when present.

    Supervisor writes ``/data/options.json``. The App keeps rooms/schedules/bindings
    in ``config.json``. Overlay only the known Supervisor keys so MQTT settings
    apply without wiping durable fields.
    """

    config = load_config(data_dir)
    if options_path is None:
        return config
    path = Path(options_path)
    if not path.is_file():
        return config
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    for key in _SUPERVISOR_OPTION_KEYS:
        if key in raw:
            config[key] = raw[key]
    return config


class _Handler(BaseHTTPRequestHandler):
    runtime: HeatingRuntime

    server_version = "HeatingAssistantApp/2.0.7"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/api/health":
            self._send_json(self.runtime.status())
            return
        if path == "/api/config":
            self._send_json(self.runtime.config())
            return
        if path == "/api/state":
            self._send_json(self.runtime.state_snapshot())
            return
        if path == "/api/schedules":
            self._send_json({"room_schedules": self.runtime.schedules()})
            return
        if path == "/api/controller_config":
            self._send_json({"config": self.runtime.controller_config()})
            return
        if path == "/api/ui_settings":
            self._send_json({"ui_settings": self.runtime.ui_settings()})
            return
        if path == "/api/model_config":
            self._send_json(self.runtime.model_config())
            return
        if path == "/api/forecasts":
            query = parse_qs(parsed.query)
            self._send_json(
                self.runtime.forecasts(
                    self._optional_float(query.get("plot_forecast_hours", [None])[0])
                )
            )
            return
        if path == "/api/datasets":
            query = parse_qs(parsed.query)
            room_slug = query.get("room_slug", [None])[0]
            self._send_json({"datasets": self.runtime.datasets(room_slug)})
            return
        if path.startswith("/api/datasets/"):
            dataset_id = unquote(path.removeprefix("/api/datasets/"))
            self._send_json({"dataset": self.runtime.dataset(dataset_id)})
            return
        if path == "/api/experiments":
            self._send_json({"experiments": self.runtime.experiments()})
            return
        if path == "/api/history":
            self._send_json({})
            return
        if path in {"/", "/index.html", "/ha-industrial"} or path.startswith("/ha-industrial/"):
            self._send_index_html()
            return
        if path.startswith("/static/"):
            self._send_static(path)
            return
        if path == "/ha-industrial-panel":
            self._send_index_html()
            return
        if path.startswith("/ha-industrial-panel/"):
            self._send_static(path, prefix="/ha-industrial-panel/")
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path not in {
            "/api/config",
            "/api/schedules",
            "/api/controller_config",
            "/api/ui_settings",
            "/api/services",
            "/api/forecasts/preview",
        }:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not isinstance(payload, dict):
            self.send_error(HTTPStatus.BAD_REQUEST, "payload must be a JSON object")
            return
        try:
            if path == "/api/config":
                result = asyncio.run(self.runtime.update_config(payload))
            elif path == "/api/schedules":
                room_name = payload.get("room_name")
                if not isinstance(room_name, str) or not room_name:
                    self.send_error(HTTPStatus.BAD_REQUEST, "room_name is required")
                    return
                result = {
                    "room_schedules": asyncio.run(
                        self.runtime.update_schedule(
                            room_name,
                            periods=payload.get("periods")
                            if isinstance(payload.get("periods"), list)
                            else None,
                            enabled=bool(payload["enabled"])
                            if "enabled" in payload
                            else None,
                        )
                    )
                }
            elif path == "/api/controller_config":
                result = {
                    "config": asyncio.run(self.runtime.update_config(payload)),
                }
            elif path == "/api/ui_settings":
                result = {
                    "ui_settings": asyncio.run(self.runtime.update_ui_settings(payload)),
                }
            elif path == "/api/services":
                domain = payload.get("domain")
                service = payload.get("service")
                data = payload.get("data", {})
                if not isinstance(domain, str) or not isinstance(service, str):
                    self.send_error(HTTPStatus.BAD_REQUEST, "domain and service are required")
                    return
                if not isinstance(data, dict):
                    self.send_error(HTTPStatus.BAD_REQUEST, "service data must be a JSON object")
                    return
                result = asyncio.run(self.runtime.apply_service(domain, service, data))
            else:
                result = self.runtime.forecasts(
                    self._optional_float(payload.get("plot_forecast_hours"))
                )
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(result)

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

    def _send_json(self, payload: Any) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: str, *, prefix: str = "/static/") -> None:
        relative = Path(unquote(path.removeprefix(prefix)))
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

    def _ingress_base_path(self) -> str | None:
        """Ingress proxy base path from Supervisor (e.g. /api/hassio_ingress/<key>)."""

        raw = self.headers.get("X-Ingress-Path")
        if not raw:
            return None
        path = raw.strip()
        if not path:
            return None
        if not path.startswith("/"):
            path = f"/{path}"
        trimmed = path.rstrip("/")
        return trimmed or None

    def _send_index_html(self) -> None:
        """Serve index.html with Ingress base href when proxied through HA."""

        index_path = _STATIC_DIR / "index.html"
        try:
            body = index_path.read_text(encoding="utf-8")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "file not found")
            return

        ingress = self._ingress_base_path()
        if ingress:
            base_tag = f'<base href="{ingress}/">\n'
            ingress_script = f'<script>window.__HA_INGRESS_BASE="{ingress}";</script>\n'
            if re.search(r"<base\b", body, flags=re.IGNORECASE):
                body = re.sub(r"<base\b[^>]*>", base_tag.strip(), body, count=1)
            else:
                body = body.replace(
                    "<head>",
                    f"<head>\n  {base_tag}  {ingress_script}",
                    1,
                )

        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

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

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


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
    parser.add_argument(
        "--options-path",
        default=os.environ.get(
            "HEATING_ASSISTANT_OPTIONS_PATH",
            os.environ.get("HEATINGASSISTANT_OPTIONS_PATH", "/data/options.json"),
        ),
        help="Supervisor options.json path (MQTT / instance_id overlay)",
    )
    parser.add_argument("--ha-runtime", action="store_true")
    return parser


async def _start_runtime(runtime: HeatingRuntime) -> None:
    await runtime.start()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    options = merge_supervisor_options(data_dir, args.options_path)
    bus = create_mqtt_bus(options) if args.ha_runtime else None
    runtime = HeatingRuntime(data_dir, bus=bus, options=options)
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
