"""Serve the local WinchAI GUI and run simulations through a loopback-only API."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Mapping
from urllib.parse import parse_qs, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIRECTORY = PROJECT_ROOT / "dist"
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_RESULT_BYTES = 128 * 1024 * 1024
_simulation_lock = Lock()
_serve_frontend = True


@dataclass(frozen=True)
class SimulationSettings:
    payout_m: float
    initial_increment_m: float


def parse_simulation_settings(query: Mapping[str, list[str]]) -> SimulationSettings:
    def number(name: str) -> float:
        values = query.get(name)
        if not values or not values[0]:
            raise ValueError(f"Missing required parameter: {name}.")
        try:
            value = float(values[0])
        except ValueError as error:
            raise ValueError(f"{name} must be a number.") from error
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value

    payout_m = number("payout_m")
    initial_increment_m = number("initial_increment_m")
    if not -10_000 <= payout_m <= 10_000:
        raise ValueError("Cable payout must be between -10,000 and 10,000 meters.")
    if not 1e-3 <= initial_increment_m <= 0.05:
        raise ValueError("Initial cable increment must be between 1 mm and 50 mm.")
    return SimulationSettings(payout_m, initial_increment_m)


def build_solver_command(
    model_path: Path, output_path: Path, settings: SimulationSettings
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "simulation.winch_solver",
        str(model_path),
        "--output",
        str(output_path),
        "--payout-m",
        str(settings.payout_m),
        "--initial-increment-mm",
        str(settings.initial_increment_m * 1000),
    ]
    return command


class LocalAppHandler(SimpleHTTPRequestHandler):
    server_version = "WinchAI Local"

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(DIST_DIRECTORY), **kwargs)

    def _send_json(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local_request(self) -> bool:
        host = urlsplit(f"//{self.headers.get('Host', '')}").hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            self._send_json(403, "This local API only accepts loopback requests.")
            return False
        origin = self.headers.get("Origin")
        if origin:
            origin_host = urlsplit(origin).hostname
            if origin_host not in {"127.0.0.1", "localhost", "::1"}:
                self._send_json(403, "Cross-origin requests are not allowed.")
                return False
        return True

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/health":
            if not self._local_request():
                return
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/") or not _serve_frontend:
            self._send_json(404, "Not found.")
            return
        if not self._local_request():
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/simulate":
            self._send_json(404, "Not found.")
            return
        if not self._local_request():
            return
        if self.headers.get("X-WinchAI-Request") != "1":
            self._send_json(403, "Missing local application request header.")
            return

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(400, "A valid Content-Length header is required.")
            return
        if content_length < 1:
            self._send_json(400, "The selected model file is empty.")
            return
        if content_length > MAX_MODEL_BYTES:
            self._send_json(413, "The model file exceeds the 64 MiB upload limit.")
            return

        try:
            settings = parse_simulation_settings(parse_qs(urlsplit(self.path).query))
        except ValueError as error:
            self._send_json(400, str(error))
            return
        model_bytes = self.rfile.read(content_length)
        if len(model_bytes) != content_length:
            self._send_json(400, "The model upload was incomplete.")
            return
        if not _simulation_lock.acquire(blocking=False):
            self._send_json(409, "Another simulation is already running.")
            return

        try:
            with tempfile.TemporaryDirectory(prefix="winchai-") as temporary_directory:
                temporary_path = Path(temporary_directory)
                model_path = temporary_path / "input.amodel"
                output_path = temporary_path / "result.aqwr"
                model_path.write_bytes(model_bytes)
                command = build_solver_command(model_path, output_path, settings)
                try:
                    completed = subprocess.run(
                        command,
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                    )
                except OSError as error:
                    self._send_json(500, f"Could not start the Python solver: {error}")
                    return
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout).strip()
                    self._send_json(422, detail[-8000:] or "The Python solver failed.")
                    return
                if not output_path.is_file():
                    self._send_json(500, "The Python solver completed without producing a result.")
                    return
                result_size = output_path.stat().st_size
                if result_size > MAX_RESULT_BYTES:
                    self._send_json(
                        413,
                        "The result exceeds 128 MiB. Use a shorter cable travel or the Python CLI for larger results.",
                    )
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="winch-result.aqwr"')
                self.send_header("Content-Length", str(result_size))
                self.end_headers()
                with output_path.open("rb") as result_file:
                    while chunk := result_file.read(1024 * 1024):
                        self.wfile.write(chunk)
        finally:
            _simulation_lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Loopback HTTP port (default: 8765).")
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="Run only the API, for use with the Vite development server.",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65_535:
        parser.error("Port must be between 1 and 65535.")

    global _serve_frontend
    _serve_frontend = not args.api_only
    if _serve_frontend and not (DIST_DIRECTORY / "index.html").is_file():
        parser.error("Built GUI not found in dist/. Run `npm run build` first, or use --api-only.")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), LocalAppHandler)
    print(f"WinchAI local app: http://127.0.0.1:{args.port}/")
    if args.api_only:
        print("API-only mode; start the Vite GUI separately with `npm run dev`.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping WinchAI local app.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
