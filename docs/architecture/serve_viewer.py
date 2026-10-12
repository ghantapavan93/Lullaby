"""Serve docs/architecture for the Excalidraw check, locally.

    python docs/architecture/serve_viewer.py      # http://127.0.0.1:8730/viewer.html?board=A-backend-architecture

GET serves this directory. POST /save?name=<board> writes the SVG (or, with &kind=png, the PNG)
that the real Excalidraw library exported in the browser to exports/. Only
the three board names are accepted; nothing else is written.
"""
import http.server
import socketserver
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
BOARDS = {"A-backend-architecture", "B-same-symptom-different-cause", "C-demo-storyboard"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def do_POST(self):
        name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
        if urlparse(self.path).path != "/save" or name not in BOARDS:
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        (HERE / "exports").mkdir(exist_ok=True)
        kind = "png" if parse_qs(urlparse(self.path).query).get("kind") == ["png"] else "svg"
        (HERE / "exports" / f"{name}.{kind}").write_bytes(body)
        self.send_response(201)
        self.end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8730
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        httpd.serve_forever()
