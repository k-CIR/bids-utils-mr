#!/usr/bin/env python3
"""HTTP server: serves static files and handles BIDS utility endpoints."""
import http.server
import subprocess
import json
import os
import re
import shutil
import sys
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", 8080))

# Paths derived from the location of this script file
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
_RAW_MRI_DIR = os.path.join(PROJECT_ROOT, "raw", "mri")
_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "dcm2bids_helper")


def _find_executable(name):
    """Resolve an executable, checking PATH, the running Python's bin dir,
    and common conda/mamba installation prefixes."""
    # 1. Current process PATH
    path = shutil.which(name)
    if path:
        return path
    # 2. Same bin dir as the Python interpreter running this server
    candidate = os.path.join(os.path.dirname(os.path.realpath(sys.executable)), name)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    # 3. Common conda/mamba prefixes (system-wide and per-user)
    home = os.path.expanduser("~")
    conda_prefixes = [
        "/opt/anaconda3",
        "/opt/miniconda3",
        "/opt/conda",
        os.path.join(home, "anaconda3"),
        os.path.join(home, "miniconda3"),
        os.path.join(home, "mambaforge"),
        os.path.join(home, "miniforge3"),
    ]
    for prefix in conda_prefixes:
        candidate = os.path.join(prefix, "bin", name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


_DCM2BIDS_HELPER = _find_executable("dcm2bids_helper")


def _get_default_helper_path():
    """Return (rel_path, warning) for the first session in ../raw/mri."""
    if not os.path.isdir(_RAW_MRI_DIR):
        return None, "No MRI data found in path"

    entries = sorted(
        e for e in os.listdir(_RAW_MRI_DIR)
        if os.path.isdir(os.path.join(_RAW_MRI_DIR, e))
    )
    if not entries:
        return None, "No MRI data found in path"

    first = entries[0]
    first_abs = os.path.join(_RAW_MRI_DIR, first)

    # Pattern: raw session folder  e.g. 18416_20250316_152203
    if re.match(r"^\d+_\d{8}_\d{6}$", first):
        return os.path.relpath(first_abs, PROJECT_ROOT), None

    # Pattern: BIDS subject folder  e.g. sub-012
    if re.match(r"^sub-", first):
        ses_entries = sorted(
            s for s in os.listdir(first_abs)
            if s.startswith("ses-") and os.path.isdir(os.path.join(first_abs, s))
        )
        if ses_entries:
            ses_abs = os.path.join(first_abs, ses_entries[0])
            return os.path.relpath(ses_abs, PROJECT_ROOT), None

    # Fallback: use first folder as-is
    return os.path.relpath(first_abs, PROJECT_ROOT), None


class Handler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/run-ls":
            result = subprocess.run(
                ["ls", "-la"],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
            self._send_json({"output": result.stdout + result.stderr})

        elif self.path == "/get-config":
            default_path, warning = _get_default_helper_path()
            self._send_json({
                "project_root": PROJECT_ROOT,
                "default_path": default_path,
                "warning": warning,
            })

        elif self.path.startswith("/run-dcm2bids-helper"):
            self._handle_dcm2bids_helper()

        else:
            super().do_GET()

    def _handle_dcm2bids_helper(self):
        params = parse_qs(urlparse(self.path).query)
        rel_path = params.get("path", [None])[0]
        if not rel_path:
            self.send_error(400, "Missing path parameter")
            return

        # Strip accidental leading slash so os.path.join works correctly
        rel_path = rel_path.lstrip("/")

        # Security: resolve and confirm the path stays inside PROJECT_ROOT
        full_path = os.path.realpath(os.path.join(PROJECT_ROOT, rel_path))
        allowed = os.path.realpath(PROJECT_ROOT)
        if not (full_path == allowed or full_path.startswith(allowed + os.sep)):
            self.send_error(403, "Path outside project root")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def emit(obj):
            self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
            self.wfile.flush()

        if not os.path.isdir(full_path):
            emit({"error": "invalid_path"})
            return

        if not _DCM2BIDS_HELPER:
            emit({"line": "Error: dcm2bids_helper not found. Is it installed in this Python environment?"})
            emit({"done": True, "returncode": 1})
            return

        force = params.get("force", ["0"])[0] == "1"
        cmd = [_DCM2BIDS_HELPER, "-d", full_path, "-o", _OUTPUT_DIR]
        if force:
            cmd.append("--force")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                emit({"line": line.rstrip("\n")})
            proc.wait()
            rc = proc.returncode
        except FileNotFoundError:
            emit({"line": f"Error: could not launch {_DCM2BIDS_HELPER}"})
            rc = 1
        except Exception as exc:
            emit({"line": f"Error: {exc}"})
            rc = 1

        emit({"done": True, "returncode": rc})

    def _send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress request logs


if __name__ == "__main__":
    with http.server.HTTPServer(("localhost", PORT), Handler) as httpd:
        print(f"Serving at http://localhost:{PORT}  (Ctrl+C to stop)")
        httpd.serve_forever()
