"""Lafz's window is a small web app (ui/) served to this computer only.

The server listens on 127.0.0.1 with a random port. Every API call must carry a secret token created at launch, and
requests addressed to any other host are refused, so web pages in a browser can't read or control Lafz (no CORS
headers are sent, and the custom token header can't be set cross-origin without a preflight we never allow).

GET  /api/state?since=N   long-poll: returns the app state once its version is newer than N (or after 25 s)
POST /api/action          {"type": ..., ...}: handed to the app's Tk thread
"""
import json, mimetypes, os, secrets, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = {"/": "ui/index.html", "/index.html": "ui/index.html", "/app.css": "ui/app.css", "/app.js": "ui/app.js",
          "/icon.png": "assets/icon_256.png"}


class Server:
    def __init__(self, on_action):
        self.on_action = on_action  # called from server threads; must hand work to the app's thread
        self.token = secrets.token_urlsafe(24)
        self.version, self.state = 0, {}
        self.cond = threading.Condition()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def publish(self, state):
        with self.cond:
            self.state, self.version = state, self.version + 1
            self.cond.notify_all()

    def wait(self, since, timeout=25):
        with self.cond:
            if since <= self.version:  # a newer 'since' comes from a page that outlived an app restart: answer now
                self.cond.wait_for(lambda: self.version > since, timeout)
            return self.version, self.state


def _handler(server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body=b"", ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self, need_token):
            host = self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{server.port}", f"localhost:{server.port}"):
                return False  # blocks DNS-rebinding: a malicious site's domain pointed at 127.0.0.1
            return not need_token or secrets.compare_digest(self.headers.get("X-Lafz-Token", ""), server.token)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in STATIC:
                if not self._allowed(False):
                    return self._send(403)
                path = os.path.join(HERE, STATIC[url.path])
                with open(path, "rb") as f:
                    return self._send(200, f.read(), mimetypes.guess_type(path)[0] or "application/octet-stream")
            if url.path == "/api/state":
                if not self._allowed(True):
                    return self._send(403)
                since = int((parse_qs(url.query).get("since") or ["0"])[0])
                version, state = server.wait(since)
                return self._send(200, json.dumps({"version": version, "state": state}).encode())
            self._send(404)

        def do_POST(self):
            try:
                length = max(0, min(int(self.headers.get("Content-Length", 0)), 1_000_000))
            except ValueError:
                return self._send(400)
            body = self.rfile.read(length)  # read before replying: Windows aborts a connection closed with unread data
            if urlparse(self.path).path != "/api/action" or not self._allowed(True):
                return self._send(403)
            try:
                action = json.loads(body)
            except ValueError:
                return self._send(400)
            server.on_action(action)
            self._send(200, b'{"ok": true}')

    return Handler
