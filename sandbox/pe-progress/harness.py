#!/usr/bin/env python3
"""Serve the PE-progress sandbox and capture stills with headless Chrome."""

from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SANDBOX = Path(__file__).resolve().parent
STATIC = ROOT / "heatingassistant" / "app" / "static"
INSPECT = SANDBOX / "inspect"
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome-stable")


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0]
        if clean.startswith("/ha-industrial-panel/"):
            rel = clean[len("/ha-industrial-panel/") :].lstrip("/")
            return str(STATIC / rel)
        if clean in ("/", "/index.html"):
            return str(SANDBOX / "index.html")
        if clean.startswith("/sandbox/pe-progress/"):
            rel = clean[len("/sandbox/pe-progress/") :].lstrip("/")
            return str(SANDBOX / rel)
        local = SANDBOX / clean.lstrip("/")
        if local.is_file():
            return str(local)
        return str(SANDBOX / "index.html")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def _serve() -> tuple[socketserver.TCPServer, str]:
    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    return httpd, f"http://{host}:{port}"


def _screenshot(url: str, dest: Path, width: int = 1100, height: int = 980) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--hide-scrollbars",
        "--virtual-time-budget=3500",
        f"--window-size={width},{height}",
        f"--screenshot={dest}",
        url,
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="01")
    parser.add_argument("--serve-only", action="store_true")
    args = parser.parse_args()
    httpd, origin = _serve()
    print(f"sandbox: {origin}")
    if args.serve_only:
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.shutdown()
        return
    try:
        for mode, name in (
            ("start", f"{args.tag}_start.png"),
            ("mid", f"{args.tag}_mid.png"),
            ("late", f"{args.tag}_late.png"),
            ("timeout", f"{args.tag}_timeout.png"),
        ):
            dest = INSPECT / name
            _screenshot(f"{origin}/?mode={mode}", dest)
            print(f"wrote {dest}")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
