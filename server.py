#!/usr/bin/env python3
"""HTTP server: serves static files and handles BIDS utility endpoints."""
import http.server
import subprocess
import json
import os
import re
import errno
import sys
import csv
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", 8080))


def _debug_mode_enabled():
    return str(os.environ.get("BIDS_UTILS_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}

# Paths derived from the location of this script file
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UTILS_DIR = os.path.dirname(_SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(UTILS_DIR)
_RAW_PET_DIR = os.path.join(PROJECT_ROOT, "BIDS_pet")
_DERIVATIVES_DIR_CANDIDATES = [
    os.path.join(_RAW_PET_DIR, "derivatives"),
    os.path.join(PROJECT_ROOT, "derivatives"),
]


def _normalize_subject(value):
    s = str(value or "").strip()
    if not s:
        return ""
    if s.lower().startswith("sub-"):
        return s
    return f"sub-{s}"


def _normalize_session(value):
    s = str(value or "").strip()
    if not s:
        return ""
    if s.lower().startswith("ses-"):
        return s
    return f"ses-{s}"


def _detect_petprep_done(subject, session):
    """Detect whether PETPrep outputs exist for subject/session."""
    candidate_dirs = [
        os.path.join(_RAW_PET_DIR, "derivatives", "petprep", subject, session),
        os.path.join(PROJECT_ROOT, "derivatives", "petprep", subject, session),
        os.path.join(_RAW_PET_DIR, "derivatives", "petprep", subject, session, "pet"),
        os.path.join(PROJECT_ROOT, "derivatives", "petprep", subject, session, "pet"),
        os.path.join(_RAW_PET_DIR, "derivatives", subject, session, "pet"),
        os.path.join(PROJECT_ROOT, "derivatives", subject, session, "pet"),
    ]

    candidate_files = [
        f"{subject}_{session}_pet.nii.gz",
        f"{subject}_{session}_desc-preproc_pet.nii.gz",
        f"{subject}_{session}_space-T1w_desc-preproc_pet.nii.gz",
    ]

    for base_dir in candidate_dirs:
        for filename in candidate_files:
            if os.path.isfile(os.path.join(base_dir, filename)):
                return True

        if os.path.isdir(base_dir):
            try:
                for entry in os.listdir(base_dir):
                    if entry.lower().endswith(".nii.gz"):
                        return True
            except OSError:
                pass

    return False


def _auto_detect_statuses(headers, rows):
    """Return per-row status detection from filesystem for BIDS and PETPrep."""
    if not headers or not rows:
        return []

    header_idx = {str(h).strip().lower(): i for i, h in enumerate(headers)}
    subject_i = next((header_idx[k] for k in ("subject", "sub", "participant_id") if k in header_idx), None)
    session_i = next((header_idx[k] for k in ("session", "ses", "visit") if k in header_idx), None)

    if subject_i is None or session_i is None:
        return []

    out = []
    for row_index, row in enumerate(rows):
        subject_raw = row[subject_i] if subject_i < len(row) else ""
        session_raw = row[session_i] if session_i < len(row) else ""
        subject = _normalize_subject(subject_raw)
        session = _normalize_session(session_raw)

        if not subject or not session:
            out.append({"row_index": row_index, "bids_done": False, "petprep_done": False})
            continue

        bids_pet_path = os.path.join(
            _RAW_PET_DIR,
            subject,
            session,
            "pet",
            f"{subject}_{session}_pet.nii.gz",
        )
        bids_done = os.path.isfile(bids_pet_path)
        petprep_done = _detect_petprep_done(subject, session)

        out.append({
            "row_index": row_index,
            "bids_done": bids_done,
            "petprep_done": petprep_done,
        })

    return out


def _existing_derivatives_dirs():
    return [d for d in _DERIVATIVES_DIR_CANDIDATES if os.path.isdir(d)]


def _is_top_level_file_in_dir(file_path, parent_dir):
    return os.path.dirname(os.path.realpath(file_path)) == os.path.realpath(parent_dir)


def _resolve_top_level_derivatives_csv_path(rel_path):
    """Resolve a CSV path and ensure it is directly under a derivatives directory."""
    rel_path = rel_path.lstrip("/")
    full_path = os.path.realpath(os.path.join(PROJECT_ROOT, rel_path))
    allowed = os.path.realpath(PROJECT_ROOT)
    if not (full_path == allowed or full_path.startswith(allowed + os.sep)):
        return None
    if not full_path.lower().endswith(".csv"):
        return None

    for derivatives_dir in _existing_derivatives_dirs():
        if _is_top_level_file_in_dir(full_path, derivatives_dir):
            return full_path
    return None


def _get_default_raw_pet_path():
    """Return (rel_path, warning) for the first session in ../raw/pet."""
    if not os.path.isdir(_RAW_PET_DIR):
        return None, "No PET data found in path"

    entries = sorted(
        e for e in os.listdir(_RAW_PET_DIR)
        if os.path.isdir(os.path.join(_RAW_PET_DIR, e))
    )
    if not entries:
        return None, "No PET data found in path"

    first = entries[0]
    first_abs = os.path.join(_RAW_PET_DIR, first)

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


def _find_derivatives_csv_files(max_count=200):
    """Return sorted CSV paths under known derivatives dirs, relative to PROJECT_ROOT."""
    csv_paths = []
    seen = set()

    for derivatives_dir in _existing_derivatives_dirs():
        files = sorted(os.listdir(derivatives_dir))
        for filename in files:
            abs_path = os.path.join(derivatives_dir, filename)
            if not os.path.isfile(abs_path):
                continue
            if not filename.lower().endswith(".csv"):
                continue

            rel_path = os.path.relpath(abs_path, PROJECT_ROOT)
            if rel_path in seen:
                continue
            seen.add(rel_path)
            csv_paths.append(rel_path)
            if len(csv_paths) >= max_count:
                return csv_paths

    return csv_paths


def _find_completed_pet_sessions_files(max_count=200):
    """Return sorted CSV paths containing 'completed_pet_sessions' under derivatives dirs."""
    csv_paths = []
    seen = set()

    try:
        for derivatives_dir in _existing_derivatives_dirs():
            try:
                files = sorted(os.listdir(derivatives_dir))
            except OSError:
                continue

            for filename in files:
                abs_path = os.path.join(derivatives_dir, filename)
                if not os.path.isfile(abs_path):
                    continue
                if not filename.lower().endswith(".csv"):
                    continue
                if "completed_pet_sessions" not in filename.lower():
                    continue

                rel_path = os.path.relpath(abs_path, PROJECT_ROOT)
                if rel_path in seen:
                    continue
                seen.add(rel_path)
                csv_paths.append(rel_path)
                if len(csv_paths) >= max_count:
                    return csv_paths
    except Exception:
        pass

    return csv_paths


def _get_csv_browser_config():
    """Return default CSV and warning for derivatives CSV browser."""
    found_derivatives_dir = bool(_existing_derivatives_dirs())
    if not found_derivatives_dir:
        return {
            "default_csv": None,
            "warning": "No derivatives directory found in expected locations.",
            "csv_files": [],
        }

    csv_files = _find_derivatives_csv_files()
    if not csv_files:
        return {
            "default_csv": None,
            "warning": "No CSV files found directly inside derivatives.",
            "csv_files": [],
        }

    return {
        "default_csv": csv_files[0],
        "warning": None,
        "csv_files": csv_files,
    }


def _iter_raw_pet_overview_lines(base_dir):
    """Yield top-level HTML file metadata in base_dir."""
    files = sorted(
        name for name in os.listdir(base_dir)
        if os.path.isfile(os.path.join(base_dir, name))
    )

    found_any = False
    for filename in files:
        if not filename.lower().endswith(".html"):
            continue

        found_any = True
        file_path = os.path.join(base_dir, filename)
        rel_path = os.path.relpath(file_path, PROJECT_ROOT)
        try:
            size = os.path.getsize(file_path)
        except OSError:
            size = -1
        yield {
            "file": filename,
            "rel_path": rel_path,
            "size": size,
        }

    if not found_any:
        yield "No .html files found."


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
            default_path, warning = _get_default_raw_pet_path()
            self._send_json({
                "project_root": PROJECT_ROOT,
                "default_path": default_path,
                "warning": warning,
            })

        elif self.path.startswith("/raw-pet-overview"):
            self._handle_raw_pet_overview()

        elif self.path.startswith("/open-html"):
            self._handle_open_html()

        elif self.path == "/get-csv-config":
            cfg = _get_csv_browser_config()
            self._send_json({
                "project_root": PROJECT_ROOT,
                "default_csv": cfg["default_csv"],
                "warning": cfg["warning"],
                "csv_files": cfg["csv_files"],
            })

        elif self.path == "/get-completed-sessions-files":
            completed_files = _find_completed_pet_sessions_files()
            self._send_json({
                "completed_files": completed_files,
            })

        elif self.path.startswith("/get-csv"):
            self._handle_get_csv()

        elif self.path.startswith("/load-target-file"):
            self._handle_load_target_file()

        elif self.path.startswith("/save-target-file"):
            self.send_error(405, "Use POST for this endpoint")

        elif self.path.startswith("/merge-completed-sessions"):
            self.send_error(405, "Use POST for this endpoint")

        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/update-csv-cell":
            self._handle_update_csv_cell()
        elif self.path == "/save-target-file":
            self._handle_save_target_file()
        elif self.path == "/merge-completed-sessions":
            self._handle_merge_completed_sessions()
        else:
            self.send_error(404, "Not Found")

    def _resolve_project_path(self, rel_path):
        """Resolve user path and ensure it stays within PROJECT_ROOT."""
        rel_path = rel_path.lstrip("/")
        full_path = os.path.realpath(os.path.join(PROJECT_ROOT, rel_path))
        allowed = os.path.realpath(PROJECT_ROOT)
        if not (full_path == allowed or full_path.startswith(allowed + os.sep)):
            return None
        return full_path

    def _handle_raw_pet_overview(self):
        params = parse_qs(urlparse(self.path).query)
        rel_path = params.get("path", [None])[0] or os.path.relpath(_RAW_PET_DIR, PROJECT_ROOT)

        full_path = self._resolve_project_path(rel_path)
        if not full_path:
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

        emit({"line": f"Overview for: {full_path}"})
        try:
            for item in _iter_raw_pet_overview_lines(full_path):
                if isinstance(item, str):
                    emit({"line": item})
                    continue

                size = item.get("size", -1)
                size_text = f"{size} B" if size >= 0 else "size unknown"
                emit({
                    "file": item["file"],
                    "open_url": "/open-html?path=" + item["rel_path"],
                    "size_text": size_text,
                })
            rc = 0
        except Exception as exc:
            emit({"line": f"Error while generating overview: {exc}"})
            rc = 1

        emit({"done": True, "returncode": rc})

    def _handle_open_html(self):
        params = parse_qs(urlparse(self.path).query)
        rel_path = params.get("path", [None])[0]
        if not rel_path:
            self.send_error(400, "Missing path parameter")
            return

        full_path = self._resolve_project_path(rel_path)
        if not full_path:
            self.send_error(403, "Path outside project root")
            return
        if not full_path.lower().endswith(".html"):
            self.send_error(400, "Only .html files are allowed")
            return
        if not os.path.isfile(full_path):
            self.send_error(404, "File not found")
            return

        try:
            with open(full_path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(500, "Failed to read file")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_get_csv(self):
        params = parse_qs(urlparse(self.path).query)
        rel_path = params.get("path", [None])[0]
        if not rel_path:
            self.send_error(400, "Missing path parameter")
            return

        full_path = _resolve_top_level_derivatives_csv_path(rel_path)
        if not full_path:
            self.send_error(400, "Path must be a .csv directly in derivatives")
            return
        if not os.path.isfile(full_path):
            self.send_error(404, "File not found")
            return

        rows = []
        try:
            with open(full_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    rows.append(row)
        except UnicodeDecodeError:
            self.send_error(400, "CSV must be UTF-8 encoded")
            return
        except OSError:
            self.send_error(500, "Failed to read file")
            return

        if not rows:
            self._send_json({
                "path": os.path.relpath(full_path, PROJECT_ROOT),
                "headers": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "auto_detected": [],
            })
            return

        headers = rows[0]
        data_rows = rows[1:]

        selected_columns_raw = params.get("columns", [""])[0].strip()
        if selected_columns_raw:
            selected_columns = [c.strip() for c in selected_columns_raw.split(",") if c.strip()]
            selected_indices = [i for i, h in enumerate(headers) if h in selected_columns]
            headers = [headers[i] for i in selected_indices]
            data_rows = [[row[i] if i < len(row) else "" for i in selected_indices] for row in data_rows]

        max_rows = 1000
        truncated = len(data_rows) > max_rows
        if truncated:
            data_rows = data_rows[:max_rows]

        auto_detected = _auto_detect_statuses(headers, data_rows)

        self._send_json({
            "path": os.path.relpath(full_path, PROJECT_ROOT),
            "headers": headers,
            "rows": data_rows,
            "row_count": len(rows) - 1,
            "truncated": truncated,
            "auto_detected": auto_detected,
        })

    def _handle_update_csv_cell(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            self.send_error(400, "Missing request body")
            return

        try:
            raw = self.rfile.read(content_length)
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_error(400, "Invalid JSON body")
            return

        rel_path = str(payload.get("path", "")).strip()
        column = str(payload.get("column", "")).strip()
        value = str(payload.get("value", "")).strip()
        row_index = payload.get("row_index")

        if not rel_path or not column or row_index is None:
            self.send_error(400, "Missing required fields: path, row_index, column")
            return
        if not isinstance(row_index, int) or row_index < 0:
            self.send_error(400, "row_index must be a non-negative integer")
            return

        full_path = _resolve_top_level_derivatives_csv_path(rel_path)
        if not full_path:
            self.send_error(400, "Path must be a .csv directly in derivatives")
            return
        if not os.path.isfile(full_path):
            self.send_error(404, "File not found")
            return

        try:
            with open(full_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = [row for row in csv.reader(f)]
        except Exception:
            self.send_error(500, "Failed to read CSV")
            return

        if not rows:
            self.send_error(400, "CSV is empty")
            return

        headers = rows[0]
        if column not in headers:
            self.send_error(400, "Column not found")
            return

        data_row_offset = row_index + 1
        if data_row_offset >= len(rows):
            self.send_error(400, "row_index out of range")
            return

        col_index = headers.index(column)
        row = rows[data_row_offset]
        if len(row) <= col_index:
            row.extend([""] * (col_index + 1 - len(row)))
        row[col_index] = value

        try:
            with open(full_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerows(rows)
        except Exception:
            self.send_error(500, "Failed to write CSV")
            return

        self._send_json({"ok": True})

    def _handle_load_target_file(self):
        """Load an existing target CSV file."""
        params = parse_qs(urlparse(self.path).query)
        rel_path = params.get("path", [None])[0]
        if not rel_path:
            self.send_error(400, "Missing path parameter")
            return

        full_path = _resolve_top_level_derivatives_csv_path(rel_path)
        if not full_path:
            self.send_error(400, "Path must be a .csv directly in derivatives")
            return
        if not os.path.isfile(full_path):
            self.send_error(404, "File not found")
            return

        rows = []
        try:
            with open(full_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    rows.append(row)
        except UnicodeDecodeError:
            self.send_error(400, "CSV must be UTF-8 encoded")
            return
        except OSError:
            self.send_error(500, "Failed to read file")
            return

        if not rows:
            self._send_json({
                "path": os.path.relpath(full_path, PROJECT_ROOT),
                "headers": [],
                "rows": [],
                "row_count": 0,
                "auto_detected": [],
            })
            return

        headers = rows[0]
        data_rows = rows[1:]

        self._send_json({
            "path": os.path.relpath(full_path, PROJECT_ROOT),
            "headers": headers,
            "rows": data_rows,
            "row_count": len(data_rows),
            "auto_detected": _auto_detect_statuses(headers, data_rows),
        })

    def _handle_save_target_file(self):
        """Write the current CSV state to the target file (creating if needed)."""
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            self.send_error(400, "Missing request body")
            return

        try:
            raw = self.rfile.read(content_length)
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_error(400, "Invalid JSON body")
            return

        rel_path = str(payload.get("path", "")).strip()
        headers = payload.get("headers", [])
        rows = payload.get("rows", [])
        status_columns = payload.get("status_columns", [])

        if not rel_path:
            self.send_error(400, "Missing path")
            return

        full_path = _resolve_top_level_derivatives_csv_path(rel_path)
        if not full_path:
            self.send_error(400, "Path must be a .csv directly in derivatives")
            return

        # If headers is empty, try to keep original headers from file if it exists
        if not headers and os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8-sig", newline="") as f:
                    first_row = next(csv.reader(f), [])
                    headers = first_row if first_row else []
            except Exception:
                pass

        # Write file
        try:
            with open(full_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if headers:
                    writer.writerow(headers)
                writer.writerows(rows)
        except Exception:
            self.send_error(500, "Failed to write file")
            return

        self._send_json({"ok": True, "path": os.path.relpath(full_path, PROJECT_ROOT)})

    def _handle_merge_completed_sessions(self):
        """Merge rows from a completed sessions CSV into the target CSV."""
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            self.send_error(400, "Missing request body")
            return

        try:
            raw = self.rfile.read(content_length)
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_error(400, "Invalid JSON body")
            return

        target_rel = str(payload.get("target_path", "")).strip()
        completed_rel = str(payload.get("completed_path", "")).strip()

        if not target_rel or not completed_rel:
            self.send_error(400, "Missing target_path or completed_path")
            return

        target_full = _resolve_top_level_derivatives_csv_path(target_rel)
        completed_full = _resolve_top_level_derivatives_csv_path(completed_rel)

        if not target_full:
            self.send_error(400, "Target path must be a .csv directly in derivatives")
            return
        if not completed_full:
            self.send_error(400, "Completed path must be a .csv directly in derivatives")
            return
        if not os.path.isfile(completed_full):
            self.send_error(404, "Completed file not found")
            return

        # Read target file (allow missing target and create from completed schema)
        target_rows = []
        if os.path.isfile(target_full):
            try:
                with open(target_full, "r", encoding="utf-8-sig", newline="") as f:
                    target_rows = [row for row in csv.reader(f)]
            except Exception:
                self.send_error(500, "Failed to read target file")
                return

        # Read completed file
        completed_rows = []
        try:
            with open(completed_full, "r", encoding="utf-8-sig", newline="") as f:
                completed_rows = [row for row in csv.reader(f)]
        except Exception:
            self.send_error(500, "Failed to read completed file")
            return

        if not completed_rows:
            completed_headers = []
            completed_data = []
        else:
            completed_headers = completed_rows[0]
            completed_data = completed_rows[1:]

        if not target_rows:
            if completed_headers:
                target_headers = completed_headers[:]
            else:
                target_headers = ["subject", "session", "status"]
            target_data = []
        else:
            target_headers = target_rows[0]
            target_data = target_rows[1:]

        target_idx = {h: i for i, h in enumerate(target_headers)}
        completed_idx = {h: i for i, h in enumerate(completed_headers)}

        preferred_key_columns = ["subject", "session"]
        key_columns = [c for c in preferred_key_columns if c in target_idx and c in completed_idx]
        if not key_columns:
            key_columns = [c for c in target_headers if c in completed_idx][:2]

        def _key_from_row(row, index_map):
            if key_columns:
                return tuple(row[index_map[c]].strip() if index_map[c] < len(row) else "" for c in key_columns)
            # Fallback when no shared headers: use full row text.
            return tuple((v or "").strip() for v in row)

        existing_keys = {_key_from_row(row, target_idx) for row in target_data}
        merged_count = 0

        for completed_row in completed_data:
            key = _key_from_row(completed_row, completed_idx)
            if key in existing_keys:
                continue

            new_row = [""] * len(target_headers)
            for col_name, t_i in target_idx.items():
                c_i = completed_idx.get(col_name)
                if c_i is not None and c_i < len(completed_row):
                    new_row[t_i] = completed_row[c_i]

            target_data.append(new_row)
            existing_keys.add(key)
            merged_count += 1

        # Write merged result
        try:
            with open(target_full, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(target_headers)
                writer.writerows(target_data)
        except Exception:
            self.send_error(500, "Failed to write merged file")
            return

        self._send_json({
            "path": os.path.relpath(target_full, PROJECT_ROOT),
            "headers": target_headers,
            "rows": target_data,
            "row_count": len(target_data),
            "merged_count": merged_count,
        })

    def _send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress request logs


class ReusableHTTPServer(http.server.HTTPServer):
    # Allow quick restarts after Ctrl+C without waiting for socket TIME_WAIT.
    allow_reuse_address = True


if __name__ == "__main__":
    try:
        with ReusableHTTPServer(("localhost", PORT), Handler) as httpd:
            print(f"Serving at http://localhost:{PORT}  (Ctrl+C to stop)")
            httpd.serve_forever()
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise

        if _debug_mode_enabled():
            print(
                f"Error: Port {PORT} is already in use.\n"
                "This conflict is on the remote host (compute.kcir.se).\n"
                "If you already have a remote shell, run:\n"
                f"  lsof -i :{PORT}\n"
                f"  fuser -n tcp {PORT}\n"
                "Then stop the process (replace <PID>):\n"
                "  kill <PID>\n"
                "If needed: kill -9 <PID>\n"
                "\n"
                "From local Git Bash, run the same checks over SSH:\n"
                f"  ssh <username>@compute.kcir.se \"lsof -i :{PORT}\"\n"
                f"  ssh <username>@compute.kcir.se \"fuser -n tcp {PORT}\"\n"
                "Then stop by PID over SSH:\n"
                "  ssh <username>@compute.kcir.se \"kill <PID>\"",
                file=sys.stderr,
            )
        else:
            print(f"Error: Port {PORT} is already in use.", file=sys.stderr)
        raise SystemExit(1)
