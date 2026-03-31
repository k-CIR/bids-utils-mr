#!/usr/bin/env python3
"""HTTP server: serves static files and handles BIDS utility endpoints."""
import http.server
import subprocess
import json
import os
import re
import shutil
import sys
import socket
import time
from urllib.parse import urlparse, parse_qs
import base64
import hashlib
import hmac
import secrets
import signal
import atexit
import config_builder
import bids_runner

PORT = int(os.environ.get("PORT", 8080))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN") or secrets.token_urlsafe(16)

# Global server reference for cleanup
_httpd = None

def cleanup_server():
    """Clean up server on exit."""
    global _httpd
    if _httpd:
        try:
            _httpd.server_close()
            # Only print if not already shutting down via signal
            if not getattr(cleanup_server, '_called_by_signal', False):
                print(f"\nServer stopped cleanly on port {PORT}")
        except Exception:
            pass

def signal_handler(signum, frame):
    """Handle termination signals."""
    print(f"\nRemote received signal {signum}, shutting down...")
    print()
    # Mark that we're shutting down via signal to avoid duplicate messages
    cleanup_server._called_by_signal = True
    global _httpd
    if _httpd:
        try:
            _httpd.server_close()
            print(f"Server stopped cleanly on port {PORT}")
            print()
        except Exception:
            pass
    sys.exit(0)

# Register cleanup handlers
atexit.register(cleanup_server)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Simple rate limiting
_request_times = {}
_RATE_LIMIT = 75  # max requests per minute per IP

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


def _check_port_available(port):
    """Check if a port is available."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("localhost", port))
        sock.close()
        return True
    except OSError:
        return False


def _find_available_port(start_port=8080, max_attempts=10):
    """Find an available port starting from start_port."""
    for i in range(max_attempts):
        port = start_port + i
        if _check_port_available(port):
            return port
    return None


def _rate_limit_check(client_ip):
    """Simple rate limiting: max 10 requests per minute per IP."""
    now = time.time()
    if client_ip in _request_times:
        _request_times[client_ip] = [t for t in _request_times[client_ip] if now - t < 60]
        if len(_request_times[client_ip]) >= _RATE_LIMIT:
            return False
        _request_times[client_ip].append(now)
    else:
        _request_times[client_ip] = [now]
    return True


def _check_auth(request_path, query_params):
    """Check if request is authenticated with valid token."""
    # Allow access to main page without token for login
    if request_path == '/' or request_path == '/index.html':
        return True
    
    # Check for token in query parameters
    token = query_params.get('token', [None])[0]
    if token and hmac.compare_digest(token, AUTH_TOKEN):
        return True
    
    return False


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


class ReuseAddrHTTPServer(http.server.HTTPServer):
    """HTTPServer that allows socket reuse to avoid TIME_WAIT issues."""
    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()


class Handler(http.server.SimpleHTTPRequestHandler):
    
    def do_GET(self):
        # Rate limiting
        client_ip = self.client_address[0]
        if not _rate_limit_check(client_ip):
            self.send_error(429, "Too many requests")
            return
        
        # Parse URL and query parameters
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        # Check authentication for protected endpoints
        if not _check_auth(parsed_url.path, query_params):
            self._send_auth_error()
            return
            
        if parsed_url.path == "/run-ls":
            # Restrict ls to current directory only for security
            try:
                result = subprocess.run(
                    ["ls", "-la", "."],  # Explicitly list current dir only
                    capture_output=True,
                    text=True,
                    cwd=os.getcwd(),
                    timeout=10,  # Prevent hanging
                )
                self._send_json({"output": result.stdout + result.stderr})
            except subprocess.TimeoutExpired:
                self.send_error(408, "Request timeout")
            except Exception as e:
                self.send_error(500, f"Command failed: {str(e)}")

        elif parsed_url.path == "/get-config":
            default_path, warning = _get_default_helper_path()
            self._send_json({
                "project_root": PROJECT_ROOT,
                "default_path": default_path,
                "warning": warning,
                "auth_token": AUTH_TOKEN,  # Send token to authenticated client
            })

        elif parsed_url.path == "/run-dcm2bids-helper":
            self._handle_dcm2bids_helper(query_params)

        elif parsed_url.path == "/get-helper-summary":
            rows = config_builder.read_helper_jsons()
            self._send_json({"rows": rows})

        elif parsed_url.path == "/get-bids-config":
            cfg = config_builder.load_config()
            self._send_json({"config": cfg})

        elif parsed_url.path == "/discover-sessions":
            dicom_root = query_params.get("dicom_root", [None])[0]
            if not dicom_root:
                self.send_error(400, "Missing dicom_root parameter")
                return
            dicom_root = dicom_root.lstrip("/")
            full_root = os.path.realpath(os.path.join(PROJECT_ROOT, dicom_root))
            allowed = os.path.realpath(PROJECT_ROOT)
            if not (full_root == allowed or full_root.startswith(allowed + os.sep)):
                self.send_error(403, "Path outside project root")
                return
            sessions = bids_runner.discover_sessions(full_root)
            self._send_json({"sessions": sessions})

        elif parsed_url.path == "/get-recode-table":
            self._send_json({"recode": config_builder.load_recode_table()})

        elif parsed_url.path == "/stream-dcm2bids-job":
            self._handle_stream_dcm2bids_job(query_params)

        elif parsed_url.path in ("/", "/index.html"):
            super().do_GET()

        else:
            self.send_error(404, "Not found")

    def _send_auth_error(self):
        """Send authentication error response."""
        body = """
        <!DOCTYPE html>
        <html><head><title>Authentication Required</title></head>
        <body style="font-family: monospace; text-align: center; margin-top: 100px;">
        <h2>Authentication Required</h2>
        <p>Please provide a valid authentication token.</p>
        <form method="get" action="/">
            <input type="password" name="token" placeholder="Enter token" style="padding: 8px; width: 200px;" />
            <button type="submit" style="padding: 8px 16px;">Access</button>
        </form>
        </body></html>
        """
        self.send_response(401)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def _handle_dcm2bids_helper(self, params):
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
            # Add timeout to prevent hanging
            try:
                for line in proc.stdout:
                    emit({"line": line.rstrip("\n")})
                proc.wait(timeout=300)  # 5 minute timeout
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                emit({"line": "Error: Process timed out (5 minutes)"})
                rc = 1
        except FileNotFoundError:
            emit({"line": f"Error: could not launch {_DCM2BIDS_HELPER}"})
            rc = 1
        except Exception as exc:
            emit({"line": f"Error: {exc}"})
            rc = 1

        emit({"done": True, "returncode": rc})

    def _handle_run_dcm2bids(self, params):
        """Start a dcm2bids batch job; return the job_id immediately.
        params is a plain dict parsed from the POST JSON body.
        """
        try:
            dicom_root_rel = params.get("dicom_root")
            output_rel     = params.get("output_dir")
            config_rel     = params.get("config_file")
            selected       = params.get("sessions")   # list of label strings or None
            max_workers    = int(params.get("max_workers", 8))
            clobber        = bool(params.get("clobber", False))
        except (TypeError, ValueError):
            self.send_error(400, "Invalid parameters")
            return

        if not all([dicom_root_rel, output_rel, config_rel]):
            self.send_error(400, "Missing required parameters")
            return

        # Clamp workers
        max_workers = max(1, min(max_workers, 24))

        # Resolve and validate all paths stay within PROJECT_ROOT
        allowed = os.path.realpath(PROJECT_ROOT)

        def _resolve(rel):
            p = os.path.realpath(os.path.join(PROJECT_ROOT, str(rel).lstrip("/")))
            if not (p == allowed or p.startswith(allowed + os.sep)):
                raise ValueError(f"Path outside project root: {rel}")
            return p

        try:
            dicom_root  = _resolve(dicom_root_rel)
            output_dir  = _resolve(output_rel)
            config_file = _resolve(config_rel)
        except ValueError as exc:
            self.send_error(403, str(exc))
            return

        if not os.path.isfile(config_file):
            self._send_json({"error": "config_not_found"})
            return

        # Filter to requested labels (run all if none specified)
        all_sessions = bids_runner.discover_sessions(dicom_root)
        if selected:
            sel_set  = set(selected)
            sessions = [s for s in all_sessions if s["label"] in sel_set]
        else:
            sessions = all_sessions

        # Apply participant/session recodes (digits-only; empty = use original)
        recode = params.get("recode") or {}
        if isinstance(recode, dict) and recode:
            recoded = []
            for s in sessions:
                rec = recode.get(s["label"], {})
                rp  = str(rec.get("recoded_participant") or "").strip()
                rs  = str(rec.get("recoded_session")     or "").strip()
                if (rp and not re.match(r"^\d+$", rp)) or (rs and not re.match(r"^\d+$", rs)):
                    self._send_json({"error": f"Invalid recode values for {s['label']}"})
                    return
                ns = dict(s)
                if rp:
                    ns["participant"] = rp
                if rs:
                    ns["session"] = rs
                recoded.append(ns)
            sessions = recoded

        if not sessions:
            self._send_json({"error": "no_sessions"})
            return

        try:
            job_id = bids_runner.start_conversion(
                sessions, dicom_root, output_dir, config_file, max_workers, clobber
            )
            self._send_json({"job_id": job_id})
        except RuntimeError as exc:
            self._send_json({"error": str(exc)})

    def _handle_stream_dcm2bids_job(self, params):
        """SSE stream for a running dcm2bids job."""
        job_id = params.get("job_id", [None])[0]
        if not job_id:
            self.send_error(400, "Missing job_id")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def emit(obj):
            self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
            self.wfile.flush()

        try:
            for entry in bids_runner.stream_job(job_id):
                emit(entry)
                if entry.get("type") == "done":
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        client_ip = self.client_address[0]
        if not _rate_limit_check(client_ip):
            self.send_error(429, "Too many requests")
            return

        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)

        if not _check_auth(parsed_url.path, query_params):
            self._send_auth_error()
            return

        if parsed_url.path == "/save-bids-config":
            self._handle_save_config()
        elif parsed_url.path == "/save-recode-table":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = self.rfile.read(length).decode("utf-8")
                data   = json.loads(body)
            except Exception:
                self.send_error(400, "Invalid JSON body")
                return
            recode = data.get("recode") if isinstance(data, dict) else None
            if not isinstance(recode, dict):
                self.send_error(400, "Missing recode dict")
                return
            cleaned = {}
            for label, rec in recode.items():
                if not isinstance(rec, dict):
                    continue
                rp = str(rec.get("recoded_participant") or "").strip()
                rs = str(rec.get("recoded_session")     or "").strip()
                if rp and not re.match(r"^\d+$", rp):
                    self.send_error(400, f"Invalid recoded_participant: {rp}")
                    return
                if rs and not re.match(r"^\d+$", rs):
                    self.send_error(400, f"Invalid recoded_session: {rs}")
                    return
                cleaned[label] = {"recoded_participant": rp, "recoded_session": rs}
            config_builder.save_recode_table(cleaned)
            self._send_json({"ok": True})
        elif parsed_url.path == "/run-dcm2bids":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                params = json.loads(body)
            except Exception:
                self.send_error(400, "Invalid JSON body")
                return
            self._handle_run_dcm2bids(params)
        else:
            self.send_error(404, "Not found")

    def _handle_save_config(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body)
        except Exception:
            self.send_error(400, "Invalid request body")
            return

        if not isinstance(data, dict) or "descriptions" not in data:
            self.send_error(400, "Invalid config: missing 'descriptions'")
            return

        try:
            config_builder.save_config(data)
            self._send_json({"ok": True})
        except Exception as exc:
            self.send_error(500, f"Failed to save config: {exc}")

    def log_message(self, fmt, *args):
        pass  # suppress request logs


if __name__ == "__main__":
    # Check if requested port is available, find alternative if not
    if not _check_port_available(PORT):
        print(f"Warning: Port {PORT} is already in use")
        alternative_port = _find_available_port(PORT)
        if alternative_port:
            print(f"Using alternative port {alternative_port}")
            PORT = alternative_port
        else:
            print("Error: No available ports found")
            print("Try: sudo lsof -i :8080 to see what's using the port")
            sys.exit(1)
    
    try:
        _httpd = ReuseAddrHTTPServer(("localhost", PORT), Handler)
        # Clean, colored output to match desired format
        print(f"Process ID: {os.getpid()}")
        print(f"Authentication token: {AUTH_TOKEN}")
        print()
        print(f"\033[1;32mAccess URL: http://localhost:{PORT}/?token={AUTH_TOKEN}\033[0m")
        print()
        print("Ctrl+C to stop process and close tunnel")
        print()
        _httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped by user")
    except Exception as e:
        print(f"Server error: {e}")
        sys.exit(1)
