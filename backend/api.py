"""HTTP server: only standard-library dependencies, defaults to loopback binding.

Run: python -m backend.api --port 8765 [--data-dir ./runtime-data]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import logging
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .service import MAX_VIDEO_SIZE, UploadError, Service
from .video import VideoProcessingError, VideoValidationError, analyse_video

LOG = logging.getLogger("framesure")
STATIC = Path(__file__).resolve().parent.parent / "frontend"
CHUNK_ENDPOINT = re.compile(r"^/api/uploads/([0-9a-f-]{36})/chunks/(\d+)$")
SESSION_ENDPOINT = re.compile(r"^/api/uploads/([0-9a-f-]{36})(?:/(finalize|report|pause))?$")
MAX_JSON = 8192


class APIHandler(BaseHTTPRequestHandler):
    server_version = "FrameSure/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def service(self) -> Service:
        return self.server.service  # type: ignore[attr-defined]

    def setup(self):
        super().setup()
        self.connection.settimeout(18)

    def log_message(self, fmt, *args):
        LOG.info("%s %s", self.address_string(), fmt % args)

    def _json(self, data: dict, status: int = 200):
        # One request per connection: unread or interrupted bodies must never be
        # interpreted as a subsequent HTTP request.
        self.close_connection = True
        encoded = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, exc: Exception):
        status = exc.status if isinstance(exc, UploadError) else (503 if isinstance(exc, VideoProcessingError) else 500)
        if status == 500:
            LOG.exception("Internal error")
        try:
            self._json({"ok": False, "error": str(exc) if status != 500 else "Internal server error"}, status)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _length(self, cap: int):
        if self.headers.get("Transfer-Encoding") is not None:
            raise UploadError("Transfer-Encoding is unsupported", 400)
        if len(self.headers.get_all("Content-Length", [])) != 1:
            raise UploadError("Exactly one Content-Length required", 411)
        raw = self.headers.get("Content-Length")
        try:
            size = int(raw) if raw is not None else -1
        except ValueError:
            raise UploadError("Invalid Content-Length", 411)
        if size < 0:
            raise UploadError("Content-Length required", 411)
        if size > cap:
            self.close_connection = True
            raise UploadError("Request too large", 413)
        return size

    def _body(self, cap: int) -> bytes:
        n = self._length(cap)
        data = self.rfile.read(n)
        if len(data) != n:
            self.close_connection = True
            raise UploadError("Request body interrupted/incomplete", 422)
        return data

    def _req_json(self):
        try:
            content = json.loads(self._body(MAX_JSON))
            if not isinstance(content, dict):
                raise ValueError("Root must be object")
            return content
        except (ValueError, UnicodeDecodeError) as exc:
            raise UploadError("Invalid JSON request") from exc

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == "/api/health":
                self._json({"ok": True, "service": "framesure", "max_video_bytes": MAX_VIDEO_SIZE})
                return
            m = SESSION_ENDPOINT.fullmatch(path)
            if m:
                id_, sub = m.groups()
                if sub is None:
                    self._json(self.service.status(id_))
                elif sub == "report":
                    self._json(self.service.report(id_))
                else:
                    raise UploadError("Unsupported GET endpoint", 404)
                return
            if path == "/":
                path = "/index.html"
            if path in ("/index.html", "/app.js", "/style.css"):
                self.close_connection = True
                f = STATIC / path.lstrip("/")
                data = f.read_bytes()
                content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                                ".css": "text/css; charset=utf-8"}[f.suffix]
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
                return
            raise UploadError("Not found", 404)
        except (UploadError, OSError) as exc:
            self._error(exc)

    def do_OPTIONS(self):
        # No permissive CORS; same-origin browser frontend only.
        self.close_connection = True
        self.send_response(204)
        self.send_header("Connection", "close")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if path == "/api/legacy-upload":
                if not getattr(self.server, 'enable_legacy_benchmark', False):
                    raise UploadError("Legacy benchmark endpoint not enabled", 404)
                expected = self.headers.get("X-File-SHA256", "")
                if not re.fullmatch(r"[0-9a-f]{64}",expected):
                    raise UploadError("X-File-SHA256 missing or malformed")
                body = self._body(MAX_VIDEO_SIZE)
                actual = hashlib.sha256(body).hexdigest()
                if actual != expected:
                    raise UploadError("Baseline SHA-256 verification failed", 422)
                # Not a production path: 100 MiB maximum in-memory request is
                # intentionally a conventional non-resumable baseline.
                with tempfile.TemporaryDirectory() as td:
                    dest = Path(td) / "baseline.video"
                    with open(dest, "wb") as out:
                        out.write(body)
                        out.flush(); os.fsync(out.fileno())
                    report = analyse_video(dest)
                self._json({"status":"COMPLETED","bytes":len(body),"sha256":actual,
                            "video_validated":True,"source_frame_count":report['source']['source_frame_count']})
                return
            if path == "/api/uploads":
                args = self._req_json()
                self._json(self.service.start(args.get("filename"), args.get("size"), args.get("sha256")), 201)
                return
            m = SESSION_ENDPOINT.fullmatch(path)
            if m:
                id_, sub = m.groups()
                if sub == "finalize":
                    result = self.service.finalize(id_)
                    self._json(result, 200 if result["status"] == "COMPLETED" else (503 if result['status']=='FAILED' else 422))
                    return
                if sub == "pause":
                    self._json(self.service.pause(id_))
                    return
            raise UploadError("Not found", 404)
        except (UploadError, VideoValidationError, VideoProcessingError, OSError) as exc:
            self._error(exc)

    def do_PUT(self):
        path = urlsplit(self.path).path
        try:
            m = CHUNK_ENDPOINT.fullmatch(path)
            if not m:
                raise UploadError("Not found", 404)
            id_, idx = m.groups()
            # Hard cap at one chunk, no unbounded user request reads.
            body = self._body(256 * 1024)
            result = self.service.put(id_, int(idx), body, self.headers.get("X-Chunk-SHA256", ""))
            self._json(result, 200 if result["duplicate"] else 201)
        except (UploadError, OSError) as exc:
            self._error(exc)


def create_server(host: str, port: int, data_dir: Path, enable_legacy_benchmark: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), APIHandler)
    server.daemon_threads = True
    server.service = Service(data_dir)  # type: ignore[attr-defined]
    server.enable_legacy_benchmark = enable_legacy_benchmark  # type: ignore[attr-defined]
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1", help="Loopback by default; expose only behind TLS/auth proxy")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path("runtime-data"))
    parser.add_argument("--enable-legacy-benchmark", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(args.host, args.port, args.data_dir, args.enable_legacy_benchmark)
    LOG.info("FrameSure ready: http://%s:%d", *server.server_address)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
