from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .catalog import Catalog
from .service import WorkbenchService


class WorkbenchServer(ThreadingHTTPServer):
    # server_close waits for accepted requests, including in-flight transactions.
    daemon_threads = False
    allow_reuse_address = False

    def __init__(self, address, catalog=None, token=None):
        self.catalog = catalog or Catalog()
        self.service = WorkbenchService(self.catalog)
        self.closing = threading.Event()
        self.shutdown_lock = threading.Lock()
        self.desktop = None
        self.token = token or secrets.token_urlsafe(40)
        self.static = Path(__file__).parent / "static"
        self.assets = {
            (
                "/"
                if p.name == "index.html"
                else "/" + p.relative_to(self.static).as_posix()
            ): p
            for p in self.static.rglob("*")
            if p.is_file() and p.suffix in (".html", ".css", ".js", ".svg")
        }
        super().__init__(address, Handler)

    def server_close(self):
        super().server_close()
        self.catalog.close()
        self.service.workflows.close()
        self.service.conversations.close()
        self.service.runs.close()

    def request_shutdown(self):
        with self.shutdown_lock:
            if self.closing.is_set():
                return
            self.closing.set()
            threading.Thread(target=self.shutdown, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentWorkbench"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        # Requests may contain user-defined paths; do not log request bodies or tokens.
        pass

    def respond(self, status, value, cookie=None):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.headers_common()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            # Navigating away during log polling does not invalidate the task.
            self.close_connection = True

    def headers_common(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )

    def same_host(self):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return not origin or origin in {"http://" + x for x in allowed}

    def authenticated(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            name = f"awb_session_{self.server.server_port}"
            return name in cookie and hmac.compare_digest(
                cookie[name].value, self.server.token
            )
        except Exception:
            return False

    def do_GET(self):
        if not self.same_host():
            return self.respond(403, {"error": "仅接受本机管理界面请求"})
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/api/health":
            return self.respond(200, {"app": "agent-workbench", "version": __version__})
        if path.startswith("/api/"):
            if not self.authenticated():
                return self.respond(401, {"error": "请从桌面入口重新打开管理器"})
            try:
                if path == "/api/desktop/status":
                    return self.respond(
                        200,
                        self.server.desktop.status()
                        if self.server.desktop else {"available": False, "closing": False},
                    )
                result = self.server.service.get(path, parsed.query)
                return self.respond(200, result)
            except LookupError:
                return self.respond(404, {"error": "未找到资源"})
            except Exception:
                return self.respond(
                    500, {"error": "读取本地数据失败，请检查数据目录权限或重新扫描"}
                )
        file = self.server.assets.get(path)
        if file is None:
            return self.respond(404, {"error": "未找到页面"})
        body = file.read_bytes()
        self.send_response(200)
        self.headers_common()
        self.send_header(
            "Content-Type",
            (mimetypes.guess_type(str(file))[0] or "application/octet-stream")
            + "; charset=utf-8",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.same_host() or self.headers.get("X-Workbench") != "1":
            return self.respond(403, {"error": "请求来源校验失败"})
        path = urlsplit(self.path).path
        try:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.respond(415, {"error": "需要 JSON 请求"})
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 2_000_000:
                return self.respond(413, {"error": "请求过大"})
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("请求必须是对象")
            if path == "/api/auth":
                token = body.get("token", "")
                if not isinstance(token, str) or not hmac.compare_digest(
                    token, self.server.token
                ):
                    return self.respond(401, {"error": "入口已失效，请从桌面重新打开"})
                return self.respond(
                    200,
                    {"ok": True},
                    f"awb_session_{self.server.server_port}={self.server.token}; HttpOnly; SameSite=Strict; Path=/",
                )
            if not self.authenticated():
                return self.respond(401, {"error": "请从桌面入口重新打开管理器"})
            if self.server.closing.is_set():
                return self.respond(
                    503, {"error": "管理器正在退出，请稍后从桌面入口重新打开"}
                )
            if path == "/api/shutdown":
                self.server.request_shutdown()
                self.respond(200, {"ok": True})
                return
            if path in ("/api/desktop/open", "/api/desktop/minimize"):
                if not self.server.desktop:
                    raise ValueError("窗口控制仅在桌面主程序中可用")
                action = (
                    self.server.desktop.open if path.endswith("/open")
                    else self.server.desktop.minimize
                )
                return self.respond(200, action())
            result = self.server.service.post(path, body)
            self.respond(200, result)
        except LookupError:
            self.respond(404, {"error": "未找到操作"})
        except (ValueError, TypeError) as exc:
            # Error messages originate in the app; parser content is never echoed.
            self.respond(
                400,
                {
                    "error": (
                        "请求字段或 JSON 格式不正确"
                        if isinstance(exc, (json.JSONDecodeError, TypeError))
                        else str(exc)
                    )
                },
            )
        except Exception:
            self.respond(
                500,
                {"error": "操作失败，请检查目录权限或配置格式；原始凭据不会显示在此处"},
            )
