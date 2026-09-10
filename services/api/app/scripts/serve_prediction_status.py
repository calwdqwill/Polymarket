import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.prediction.service_runtime import read_status


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/status", "/health"):
            self.send_error(404)
            return
        try:
            result = read_status(Path("/var/lib/poly-crypto-prediction/state"))
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (OSError, ValueError):
            self.send_error(503)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 18010), Handler).serve_forever()
