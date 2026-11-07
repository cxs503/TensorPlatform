"""Toy HTTP service exposing a paginated ``/entries`` endpoint (stdlib only).

Run standalone:  python3 service.py [port]
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from paginate import PageOutOfRange, paginate

ITEMS = [{"id": i, "name": f"entry-{i:03d}"} for i in range(1, 11)]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path != "/entries":
            return self._send(404, {"error": "not found"})
        q = parse_qs(urlparse(self.path).query)
        try:
            page = int(q.get("page", ["1"])[0])
            per_page = int(q.get("per_page", ["3"])[0])
        except ValueError:
            return self._send(400, {"error": "page/per_page must be integers"})
        try:
            rows, total, total_pages = paginate(ITEMS, page, per_page)
        except PageOutOfRange as exc:
            return self._send(404, {"error": str(exc)})
        self._send(200, {"items": rows, "page": page, "per_page": per_page,
                          "total": total, "total_pages": total_pages})

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the log quiet
        pass


def make_server(port=0):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8931
    srv = make_server(port)
    print(f"toy service on http://127.0.0.1:{port}/entries", flush=True)
    srv.serve_forever()
