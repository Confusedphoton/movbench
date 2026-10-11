import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .storage import get_runs, leaderboard
from .tasks import TASKS, TIERS
from .validation import POLICY


def make_handler(db: Path):
    web = Path(__file__).with_name("web")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/api/leaderboard":
                return self.send_json(
                    leaderboard(
                        get_runs(db),
                        query.get("cohort"),
                        query.get("tier"),
                        query.get("task"),
                        query.get("q", ""),
                    )
                )
            if url.path == "/api/meta":
                return self.send_json(
                    {"tasks": [t.spec() for t in TASKS.values()], "tiers": TIERS, "policy": POLICY}
                )
            if url.path == "/api/runs":
                return self.send_json(get_runs(db))
            if url.path.startswith("/api/runs/"):
                run = next(
                    (r for r in get_runs(db) if r["id"] == url.path.rsplit("/", 1)[-1]), None
                )
                return self.send_json(run, 200 if run else 404)
            files = {
                "/": "index.html",
                "/app.js": "app.js",
                "/style.css": "style.css",
                "/favicon.svg": "favicon.svg",
            }
            if url.path not in files:
                return self.send_json({"error": "Not found"}, 404)
            file = web / files[url.path]
            self.send_body(
                file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            )

        def send_json(self, value, status=200):
            self.send_body(json.dumps(value).encode(), "application/json", status)

        def send_body(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(db: Path, host="127.0.0.1", port=8080):
    server = ThreadingHTTPServer((host, port), make_handler(db))
    print(f"MOVBench dashboard → http://{host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
