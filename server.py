from __future__ import annotations

import json
import mimetypes
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from core import TaskStore


class BridgePortInUseError(RuntimeError):
    """Raised when another desktop instance already owns the local bridge."""


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """Never share the automation port between different desktop versions."""

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Bridge:
    def __init__(self, store: TaskStore, host="127.0.0.1", port=17843):
        self.store, self.host, self.port = store, host, port
        self.httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        if self.httpd:
            return
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def end_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                super().end_headers()

            def reply(self, status=200, body=None, content_type="application/json"):
                raw = body if isinstance(body, bytes) else json.dumps(body or {}, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_OPTIONS(self):
                self.reply(204, b"")

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/api/ping":
                    return self.reply(body={"ok": True, **bridge.store.stats()})
                if path == "/api/helper-version":
                    stats = bridge.store.stats()
                    return self.reply(body={"version": bridge.store.helper_version, "busy": bridge.store.running,
                                            "helper_state": stats["helper_state"]})
                if path == "/api/next":
                    return self.reply(body=bridge.store.dispatch_next() or {})
                match = re.fullmatch(r"/api/image/([a-f0-9]+)", path)
                if match:
                    task = next((t for t in bridge.store.tasks if t.id == match.group(1)), None)
                    if not task or not Path(task.path).is_file():
                        return self.reply(404, {"error": "image not found"})
                    raw = Path(task.path).read_bytes()
                    return self.reply(body=raw, content_type=mimetypes.guess_type(task.path)[0] or "application/octet-stream")
                return self.reply(404, {"error": "not found"})

            def do_POST(self):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                if path == "/api/heartbeat":
                    try:
                        payload = json.loads(raw or b"{}")
                    except ValueError:
                        payload = {}
                    client = str(payload.get("client", ""))
                    now = time.time()
                    if client and (not bridge.store.browser_owner or bridge.store.browser_owner == client or now - bridge.store.browser_seen > 15):
                        bridge.store.browser_owner = client
                    active = bool(client and bridge.store.browser_owner == client)
                    if active:
                        bridge.store.browser_seen = now
                        bridge.store.connected_helper_version = str(payload.get("version", ""))
                        bridge.store.connected_browser = str(payload.get("browser", ""))
                    stats = bridge.store.stats()
                    return self.reply(body={"ok": True, "active": active, "running": bridge.store.running,
                                            "helper_state": stats["helper_state"],
                                            "required_version": bridge.store.helper_version})
                if path == "/api/event":
                    try:
                        payload = json.loads(raw)
                        ok = bridge.store.event(payload.pop("id"), payload.pop("status"), **payload)
                        return self.reply(200 if ok else 404, {"ok": ok})
                    except (ValueError, TypeError, KeyError) as exc:
                        return self.reply(400, {"error": str(exc)})
                return self.reply(404, {"error": "not found"})

            def log_message(self, *_args):
                pass

        try:
            self.httpd = ExclusiveThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            raise BridgePortInUseError(
                f"本机端口 {self.port} 已被占用，可能已有另一个版本正在运行。"
            ) from exc
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
