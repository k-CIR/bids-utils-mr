#!/usr/bin/env python3
"""Minimal HTTP server: serves static files and handles /run-ls."""
import http.server
import subprocess
import json
import os

PORT = int(os.environ.get("PORT", 8080))


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/run-ls":
            result = subprocess.run(
                ["ls", "-la"],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
            output = result.stdout + result.stderr
            body = json.dumps({"output": output}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def log_message(self, fmt, *args):
        pass  # suppress request logs


if __name__ == "__main__":
    with http.server.HTTPServer(("localhost", PORT), Handler) as httpd:
        print(f"Serving at http://localhost:{PORT}  (Ctrl+C to stop)")
        httpd.serve_forever()
