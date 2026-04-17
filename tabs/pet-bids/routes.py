#!/usr/bin/env python3
"""PET-BIDS tab: route registration and request handlers."""
import csv
import json
import mimetypes
import os
import re
from urllib.parse import quote, unquote

TAB_METADATA = {
    "id": "pet-bids",
    "label": "PET BIDS",
    "order": 1,
}

_TAB_DIR = os.path.dirname(os.path.abspath(__file__))
_BIDS_UTILS_DIR = os.path.dirname(os.path.dirname(_TAB_DIR))
PROJECT_ROOT = os.path.dirname(_BIDS_UTILS_DIR)

_CSV_RAW_PET_DIR = os.path.join(PROJECT_ROOT, "BIDS_pet")
_DEFAULT_PET_DATA_DIR = os.path.join(PROJECT_ROOT, "BIDS")


def _resolve_config_path(value, default_path):
    raw = str(value or "").strip()
    if not raw:
        return os.path.realpath(default_path)
    if os.path.isabs(raw):
        return os.path.realpath(raw)
    return os.path.realpath(os.path.join(PROJECT_ROOT, raw))


def _split_path_list(value):
    return [p.strip() for p in str(value or "").split(":") if p.strip()]


_RAW_PET_DIR = _resolve_config_path(os.environ.get("BIDS_UTILS_PET_DATA_DIR"), _DEFAULT_PET_DATA_DIR)
_CSV_DERIVATIVES_DIR_CANDIDATES = [
    os.path.join(_CSV_RAW_PET_DIR, "derivatives"),
    os.path.join(PROJECT_ROOT, "derivatives"),
]
_PET_DERIVATIVES_DIR_CANDIDATES = [
    _resolve_config_path(path, path)
    for path in _split_path_list(os.environ.get("BIDS_UTILS_PET_DERIVATIVES_DIRS"))
] or [
    os.path.join(_RAW_PET_DIR, "derivatives"),
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
    candidate_dirs = []
    for derivatives_dir in _PET_DERIVATIVES_DIR_CANDIDATES:
        candidate_dirs.extend([
            os.path.join(derivatives_dir, "petprep", subject, session),
            os.path.join(derivatives_dir, "petprep", subject, session, "pet"),
        ])

    candidate_files = [
        f"{subject}_{session}_pet.nii.gz",
        f"{subject}_{session}_desc-preproc_pet.nii.gz",
        f"{subject}_{session}_space-T1w_desc-preproc_pet.nii.gz",
    ]
    candidate_prefixes = [f"{subject}_{session}_"]
    candidate_suffixes = ["_pet.nii.gz", "_desc-preproc_pet.nii.gz"]

    for base_dir in candidate_dirs:
        for filename in candidate_files:
            if os.path.isfile(os.path.join(base_dir, filename)):
                return True

        if os.path.isdir(base_dir):
            try:
                for entry in os.listdir(base_dir):
                    lowered = entry.lower()
                    if not lowered.endswith(".nii.gz"):
                        continue
                    if not any(lowered.startswith(prefix.lower()) for prefix in candidate_prefixes):
                        continue
                    if any(lowered.endswith(suffix.lower()) for suffix in candidate_suffixes):
                        return True
            except OSError:
                pass

    return False


def _auto_detect_statuses(headers, rows):
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

        bids_pet_path = os.path.join(_RAW_PET_DIR, subject, session, "pet", f"{subject}_{session}_pet.nii.gz")
        bids_done = os.path.isfile(bids_pet_path)
        petprep_done = _detect_petprep_done(subject, session)

        out.append({"row_index": row_index, "bids_done": bids_done, "petprep_done": petprep_done})

    return out


def _existing_csv_derivatives_dirs():
    return [d for d in _CSV_DERIVATIVES_DIR_CANDIDATES if os.path.isdir(d)]


def _is_top_level_file_in_dir(file_path, parent_dir):
    return os.path.dirname(os.path.realpath(file_path)) == os.path.realpath(parent_dir)


def _resolve_top_level_derivatives_csv_path(rel_path):
    rel_path = rel_path.lstrip("/")
    full_path = os.path.realpath(os.path.join(PROJECT_ROOT, rel_path))
    allowed = os.path.realpath(PROJECT_ROOT)
    if not (full_path == allowed or full_path.startswith(allowed + os.sep)):
        return None
    if not full_path.lower().endswith(".csv"):
        return None

    for derivatives_dir in _existing_csv_derivatives_dirs():
        if _is_top_level_file_in_dir(full_path, derivatives_dir):
            return full_path
    return None


def _resolve_project_path(rel_path):
    rel_path = rel_path.lstrip("/")
    full_path = os.path.realpath(os.path.join(PROJECT_ROOT, rel_path))
    allowed = os.path.realpath(PROJECT_ROOT)
    if not (full_path == allowed or full_path.startswith(allowed + os.sep)):
        return None
    return full_path


def _get_default_raw_pet_path():
    if not os.path.isdir(_RAW_PET_DIR):
        return None, "No PET data found in path"

    entries = sorted(e for e in os.listdir(_RAW_PET_DIR) if os.path.isdir(os.path.join(_RAW_PET_DIR, e)))
    if not entries:
        return None, "No PET data found in path"

    first = entries[0]
    first_abs = os.path.join(_RAW_PET_DIR, first)

    if re.match(r"^\d+_\d{8}_\d{6}$", first):
        return os.path.relpath(first_abs, PROJECT_ROOT), None

    if re.match(r"^sub-", first):
        ses_entries = sorted(
            s for s in os.listdir(first_abs)
            if s.startswith("ses-") and os.path.isdir(os.path.join(first_abs, s))
        )
        if ses_entries:
            ses_abs = os.path.join(first_abs, ses_entries[0])
            return os.path.relpath(ses_abs, PROJECT_ROOT), None

    return os.path.relpath(first_abs, PROJECT_ROOT), None


def _find_derivatives_csv_files(max_count=200):
    csv_paths = []
    seen = set()

    for derivatives_dir in _existing_csv_derivatives_dirs():
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

            rel_path = os.path.relpath(abs_path, PROJECT_ROOT)
            if rel_path in seen:
                continue
            seen.add(rel_path)
            csv_paths.append(rel_path)
            if len(csv_paths) >= max_count:
                return csv_paths

    return csv_paths


def _find_completed_pet_sessions_files(max_count=200):
    csv_paths = []
    seen = set()

    for derivatives_dir in _existing_csv_derivatives_dirs():
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

    return csv_paths


def _get_csv_browser_config():
    found_derivatives_dir = bool(_existing_csv_derivatives_dirs())
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
    files = sorted(name for name in os.listdir(base_dir) if os.path.isfile(os.path.join(base_dir, name)))

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


def _emit_sse(h, obj):
    h.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
    h.wfile.flush()


def _handle_get_config(h, params):
    default_path, warning = _get_default_raw_pet_path()
    h._send_json({
        "project_root": PROJECT_ROOT,
        "default_path": default_path,
        "warning": warning,
    })


def _handle_raw_pet_overview(h, params):
    rel_path = params.get("path", [None])[0] or os.path.relpath(_RAW_PET_DIR, PROJECT_ROOT)

    full_path = _resolve_project_path(rel_path)
    if not full_path:
        h.send_error(403, "Path outside project root")
        return

    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Cache-Control", "no-cache")
    h.send_header("Connection", "keep-alive")
    h.end_headers()

    if not os.path.isdir(full_path):
        _emit_sse(h, {"error": "invalid_path"})
        return

    _emit_sse(h, {"line": f"Overview for: {full_path}"})
    try:
        for item in _iter_raw_pet_overview_lines(full_path):
            if isinstance(item, str):
                _emit_sse(h, {"line": item})
                continue

            size = item.get("size", -1)
            size_text = f"{size} B" if size >= 0 else "size unknown"
            open_rel = quote(item["rel_path"], safe="/")
            open_url = f"/open-file?path={open_rel}&token={quote(h.server._auth_token)}"
            _emit_sse(h, {
                "file": item["file"],
                "open_url": open_url,
                "size_text": size_text,
            })
        rc = 0
    except Exception as exc:
        _emit_sse(h, {"line": f"Error while generating overview: {exc}"})
        rc = 1

    _emit_sse(h, {"done": True, "returncode": rc})


def _handle_open_html(h, params):
    rel_path = params.get("path", [None])[0]
    if not rel_path:
        h.send_error(400, "Missing path parameter")
        return

    full_path = _resolve_project_path(rel_path)
    if not full_path:
        h.send_error(403, "Path outside project root")
        return
    if not full_path.lower().endswith(".html"):
        h.send_error(400, "Only .html files are allowed")
        return
    if not os.path.isfile(full_path):
        h.send_error(404, "File not found")
        return

    open_rel = quote(rel_path.lstrip("/"), safe="/")
    location = f"/open-file?path={open_rel}&token={quote(h.server._auth_token)}"
    h.send_response(302)
    h.send_header("Location", location)
    h.end_headers()


def _handle_open_file(h, params):
    rel_path = params.get("path", [None])[0]
    if not rel_path:
        h.send_error(400, "Missing file path")
        return

    rel_path = unquote(rel_path)
    full_path = _resolve_project_path(rel_path)
    if not full_path:
        h.send_error(403, "Path outside project root")
        return
    if not os.path.isfile(full_path):
        h.send_error(404, "File not found")
        return

    try:
        with open(full_path, "rb") as f:
            body = f.read()
    except OSError:
        h.send_error(500, "Failed to read file")
        return

    content_type, _ = mimetypes.guess_type(full_path)
    if not content_type:
        content_type = "application/octet-stream"
    if content_type.startswith("text/"):
        content_type += "; charset=utf-8"

    h.send_response(200)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _handle_get_csv_config(h, params):
    cfg = _get_csv_browser_config()
    h._send_json({
        "project_root": PROJECT_ROOT,
        "default_csv": cfg["default_csv"],
        "warning": cfg["warning"],
        "csv_files": cfg["csv_files"],
    })


def _handle_get_completed_sessions_files(h, params):
    completed_files = _find_completed_pet_sessions_files()
    h._send_json({"completed_files": completed_files})


def _handle_get_csv(h, params):
    rel_path = params.get("path", [None])[0]
    if not rel_path:
        h.send_error(400, "Missing path parameter")
        return

    full_path = _resolve_top_level_derivatives_csv_path(rel_path)
    if not full_path:
        h.send_error(400, "Path must be a .csv directly in derivatives")
        return
    if not os.path.isfile(full_path):
        h.send_error(404, "File not found")
        return

    rows = []
    try:
        with open(full_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                rows.append(row)
    except UnicodeDecodeError:
        h.send_error(400, "CSV must be UTF-8 encoded")
        return
    except OSError:
        h.send_error(500, "Failed to read file")
        return

    if not rows:
        h._send_json({
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
        selected_indices = [i for i, header in enumerate(headers) if header in selected_columns]
        headers = [headers[i] for i in selected_indices]
        data_rows = [[row[i] if i < len(row) else "" for i in selected_indices] for row in data_rows]

    max_rows = 1000
    truncated = len(data_rows) > max_rows
    if truncated:
        data_rows = data_rows[:max_rows]

    auto_detected = _auto_detect_statuses(headers, data_rows)

    h._send_json({
        "path": os.path.relpath(full_path, PROJECT_ROOT),
        "headers": headers,
        "rows": data_rows,
        "row_count": len(rows) - 1,
        "truncated": truncated,
        "auto_detected": auto_detected,
    })


def _handle_load_target_file(h, params):
    rel_path = params.get("path", [None])[0]
    if not rel_path:
        h.send_error(400, "Missing path parameter")
        return

    full_path = _resolve_top_level_derivatives_csv_path(rel_path)
    if not full_path:
        h.send_error(400, "Path must be a .csv directly in derivatives")
        return
    if not os.path.isfile(full_path):
        h.send_error(404, "File not found")
        return

    rows = []
    try:
        with open(full_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                rows.append(row)
    except UnicodeDecodeError:
        h.send_error(400, "CSV must be UTF-8 encoded")
        return
    except OSError:
        h.send_error(500, "Failed to read file")
        return

    if not rows:
        h._send_json({
            "path": os.path.relpath(full_path, PROJECT_ROOT),
            "headers": [],
            "rows": [],
            "row_count": 0,
            "auto_detected": [],
        })
        return

    headers = rows[0]
    data_rows = rows[1:]

    h._send_json({
        "path": os.path.relpath(full_path, PROJECT_ROOT),
        "headers": headers,
        "rows": data_rows,
        "row_count": len(data_rows),
        "auto_detected": _auto_detect_statuses(headers, data_rows),
    })


def _read_json_body(h):
    content_length = int(h.headers.get("Content-Length", "0"))
    if content_length <= 0:
        return None
    raw = h.rfile.read(content_length)
    return json.loads(raw.decode("utf-8"))


def _handle_update_csv_cell(h, body):
    rel_path = str(body.get("path", "")).strip()
    column = str(body.get("column", "")).strip()
    value = str(body.get("value", "")).strip()
    row_index = body.get("row_index")

    if not rel_path or not column or row_index is None:
        h.send_error(400, "Missing required fields: path, row_index, column")
        return
    if not isinstance(row_index, int) or row_index < 0:
        h.send_error(400, "row_index must be a non-negative integer")
        return

    full_path = _resolve_top_level_derivatives_csv_path(rel_path)
    if not full_path:
        h.send_error(400, "Path must be a .csv directly in derivatives")
        return
    if not os.path.isfile(full_path):
        h.send_error(404, "File not found")
        return

    try:
        with open(full_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = [row for row in csv.reader(f)]
    except Exception:
        h.send_error(500, "Failed to read CSV")
        return

    if not rows:
        h.send_error(400, "CSV is empty")
        return

    headers = rows[0]
    if column not in headers:
        h.send_error(400, "Column not found")
        return

    data_row_offset = row_index + 1
    if data_row_offset >= len(rows):
        h.send_error(400, "row_index out of range")
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
        h.send_error(500, "Failed to write CSV")
        return

    h._send_json({"ok": True})


def _handle_save_target_file(h, body):
    rel_path = str(body.get("path", "")).strip()
    headers = body.get("headers", [])
    rows = body.get("rows", [])

    if not rel_path:
        h.send_error(400, "Missing path")
        return

    full_path = _resolve_top_level_derivatives_csv_path(rel_path)
    if not full_path:
        h.send_error(400, "Path must be a .csv directly in derivatives")
        return

    if not headers and os.path.isfile(full_path):
        try:
            with open(full_path, "r", encoding="utf-8-sig", newline="") as f:
                first_row = next(csv.reader(f), [])
                headers = first_row if first_row else []
        except Exception:
            pass

    try:
        with open(full_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if headers:
                writer.writerow(headers)
            writer.writerows(rows)
    except Exception:
        h.send_error(500, "Failed to write file")
        return

    h._send_json({"ok": True, "path": os.path.relpath(full_path, PROJECT_ROOT)})


def _handle_reset_target_file(h, body):
    rel_path = str(body.get("path", "")).strip()
    headers = body.get("headers", [])
    rows = body.get("rows", [])

    if not rel_path:
        h.send_error(400, "Missing path")
        return

    full_path = _resolve_top_level_derivatives_csv_path(rel_path)
    if not full_path:
        h.send_error(400, "Path must be a .csv directly in derivatives")
        return

    if os.path.isfile(full_path):
        try:
            os.remove(full_path)
        except Exception:
            h.send_error(500, "Failed to delete existing target file")
            return

    try:
        with open(full_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if headers:
                writer.writerow(headers)
            writer.writerows(rows)
    except Exception:
        h.send_error(500, "Failed to recreate target file")
        return

    h._send_json({"ok": True, "path": os.path.relpath(full_path, PROJECT_ROOT)})


def _handle_merge_completed_sessions(h, body):
    target_rel = str(body.get("target_path", "")).strip()
    completed_rel = str(body.get("completed_path", "")).strip()

    if not target_rel or not completed_rel:
        h.send_error(400, "Missing target_path or completed_path")
        return

    target_full = _resolve_top_level_derivatives_csv_path(target_rel)
    completed_full = _resolve_top_level_derivatives_csv_path(completed_rel)

    if not target_full:
        h.send_error(400, "Target path must be a .csv directly in derivatives")
        return
    if not completed_full:
        h.send_error(400, "Completed path must be a .csv directly in derivatives")
        return
    if not os.path.isfile(completed_full):
        h.send_error(404, "Completed file not found")
        return

    target_rows = []
    if os.path.isfile(target_full):
        try:
            with open(target_full, "r", encoding="utf-8-sig", newline="") as f:
                target_rows = [row for row in csv.reader(f)]
        except Exception:
            h.send_error(500, "Failed to read target file")
            return

    try:
        with open(completed_full, "r", encoding="utf-8-sig", newline="") as f:
            completed_rows = [row for row in csv.reader(f)]
    except Exception:
        h.send_error(500, "Failed to read completed file")
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

    target_idx = {header: i for i, header in enumerate(target_headers)}
    completed_idx = {header: i for i, header in enumerate(completed_headers)}

    preferred_key_columns = ["subject", "session"]
    key_columns = [c for c in preferred_key_columns if c in target_idx and c in completed_idx]
    if not key_columns:
        key_columns = [c for c in target_headers if c in completed_idx][:2]

    def _key_from_row(row, index_map):
        if key_columns:
            return tuple(row[index_map[c]].strip() if index_map[c] < len(row) else "" for c in key_columns)
        return tuple((v or "").strip() for v in row)

    existing_keys = {_key_from_row(row, target_idx) for row in target_data}
    merged_count = 0

    for completed_row in completed_data:
        key = _key_from_row(completed_row, completed_idx)
        if key in existing_keys:
            continue

        new_row = [""] * len(target_headers)
        for col_name, target_i in target_idx.items():
            completed_i = completed_idx.get(col_name)
            if completed_i is not None and completed_i < len(completed_row):
                new_row[target_i] = completed_row[completed_i]

        target_data.append(new_row)
        existing_keys.add(key)
        merged_count += 1

    try:
        with open(target_full, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(target_headers)
            writer.writerows(target_data)
    except Exception:
        h.send_error(500, "Failed to write merged file")
        return

    h._send_json({
        "path": os.path.relpath(target_full, PROJECT_ROOT),
        "headers": target_headers,
        "rows": target_data,
        "row_count": len(target_data),
        "merged_count": merged_count,
    })


def _safe_get_json_body(h):
    try:
        body = _read_json_body(h)
    except Exception:
        h.send_error(400, "Invalid JSON body")
        return None
    if body is None:
        h.send_error(400, "Missing request body")
        return None
    if not isinstance(body, dict):
        h.send_error(400, "Invalid JSON body")
        return None
    return body


def _post_update_csv_cell(h, body):
    _handle_update_csv_cell(h, body)


def _post_save_target_file(h, body):
    _handle_save_target_file(h, body)


def _post_reset_target_file(h, body):
    _handle_reset_target_file(h, body)


def _post_merge_completed_sessions(h, body):
    _handle_merge_completed_sessions(h, body)


def _post_wrapper(fn):
    def _inner(h, body):
        if body is None:
            parsed = _safe_get_json_body(h)
            if parsed is None:
                return
            fn(h, parsed)
            return
        fn(h, body)
    return _inner


def register(get_routes, post_routes):
    get_routes["/get-config"] = _handle_get_config
    get_routes["/raw-pet-overview"] = _handle_raw_pet_overview
    get_routes["/open-html"] = _handle_open_html
    get_routes["/open-file"] = _handle_open_file
    get_routes["/get-csv-config"] = _handle_get_csv_config
    get_routes["/get-completed-sessions-files"] = _handle_get_completed_sessions_files
    get_routes["/get-csv"] = _handle_get_csv
    get_routes["/load-target-file"] = _handle_load_target_file

    post_routes["/update-csv-cell"] = _post_wrapper(_post_update_csv_cell)
    post_routes["/save-target-file"] = _post_wrapper(_post_save_target_file)
    post_routes["/reset-target-file"] = _post_wrapper(_post_reset_target_file)
    post_routes["/merge-completed-sessions"] = _post_wrapper(_post_merge_completed_sessions)
