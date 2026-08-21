#!/usr/bin/env python3
"""Serve the HA-format preview vs room-view sandbox.

Maps production Ingress static assets so the inspect page uses the same
Chart.js stack, industrial.css, and room-charts.js as the App.
"""
from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATIC = ROOT / "heatingassistant" / "app" / "static"
INSPECT = HERE / "inspect"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def translate_path(self, path: str) -> str:
        raw = path.split("?", 1)[0]
        if raw in ("/", "/index.html"):
            return str(HERE / "index.html")
        if raw.startswith("/inspect/"):
            rel = raw[len("/inspect/") :]
            return str((INSPECT / rel).resolve())
        if raw.startswith("/static/"):
            rel = raw[len("/static/") :]
            return str((STATIC / rel).resolve())
        if raw.startswith("/ha-industrial-panel/"):
            rel = raw[len("/ha-industrial-panel/") :]
            return str((STATIC / rel).resolve())
        return super().translate_path(path)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[sandbox] {fmt % args}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{args.port}/")
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
