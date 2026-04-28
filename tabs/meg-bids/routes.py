#!/usr/bin/env python3
"""MEG-BIDS tab: route registration and request handlers for main server integration.

TAB_METADATA is read by the server at startup to register this tab.
register() is called once to populate the shared GET/POST route dicts.
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Sibling modules live in the same directory
_TAB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _TAB_DIR)

# Import from refactored bidsify package
from bidsify.simple import load_minimal_config, get_default_config, bidsify_simple
from bidsify.conversion_table import load_conversion_table, update_conversion_table
from bidsify.pipeline import bidsify, update_bids_report, run_qa_analysis
from bidsify.templates import create_dataset_description, create_proc_description
from bidsify.sidecars import update_sidecars
from bidsify.utils import setLogPath


# ── Tab metadata ──────────────────────────────────────────────────────────────
TAB_METADATA = {
    "id": "meg-bids",
    "label": "MEG BIDS",
    "order": 2,
    "requires_path": "raw/meg",
}

# ── Paths ─────────────────────────────────────────────────────────────────────
def _detect_project_root(script_dir):
    """Return /data/projects/<project> for nested repo locations."""
    resolved = os.path.realpath(script_dir)
    match = re.match(r"^(/data/projects/[^/]+)(?:/|$)", resolved)
    if match:
        return match.group(1)
    return os.path.realpath(os.path.join(script_dir, "..", "..", ".."))

_PROJECT_ROOT = _detect_project_root(_TAB_DIR)
_RAW_MEG_DIR = os.path.join(_PROJECT_ROOT, "raw", "meg")
_LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")


def _resolve_project_path(rel_path):
    """Resolve a relative path to absolute within project root."""
    allowed = os.path.realpath(_PROJECT_ROOT)
    rel_path = str(rel_path or "").strip()
    if os.path.isabs(rel_path):
        full_path = os.path.realpath(rel_path)
    else:
        full_path = os.path.realpath(os.path.join(_PROJECT_ROOT, rel_path.lstrip("/")))
    if not (full_path == allowed or full_path.startswith(allowed + os.sep)):
        return None
    return full_path


# ── Handler functions ─────────────────────────────────────────────────────────

def _handle_get_config(h, params):
    """Return project configuration for the tab."""
    config_exists = os.path.exists(os.path.join(_PROJECT_ROOT, "meg_config.json"))
    h._send_json({
        "project_root": _PROJECT_ROOT,
        "raw_meg_dir": _RAW_MEG_DIR if os.path.isdir(_RAW_MEG_DIR) else None,
        "logs_dir": _LOGS_DIR,
        "config_exists": config_exists,
        "default_config": get_default_config(),
    })


def _handle_get_project_root(h, params):
    """Return the detected project root path."""
    h._send_json({
        "project_root": _PROJECT_ROOT,
    })


def _handle_validate_paths(h, body):
    """Validate that paths exist on the filesystem."""
    paths = body.get("paths", [])
    results = {}
    
    for path_info in paths:
        path_id = path_info.get("id", "unknown")
        rel_path = path_info.get("path", "")
        
        full_path = _resolve_project_path(rel_path)
        if full_path:
            exists = os.path.exists(full_path)
            is_dir = os.path.isdir(full_path) if exists else False
            is_file = os.path.isfile(full_path) if exists else False
            results[path_id] = {
                "exists": exists,
                "is_dir": is_dir,
                "is_file": is_file,
                "resolved": full_path,
                "rel_path": rel_path
            }
        else:
            results[path_id] = {
                "exists": False,
                "is_dir": False,
                "is_file": False,
                "resolved": None,
                "rel_path": rel_path,
                "error": "Path outside project root"
            }
    
    h._send_json({"results": results})


def _handle_load_config(h, params):
    """Load a minimal JSON configuration file."""
    config_path = params.get("path", [None])[0]
    if not config_path:
        h.send_error(400, "Missing config path")
        return

    full_path = _resolve_project_path(config_path)
    if not full_path:
        h.send_error(403, "Path outside project root")
        return

    if not os.path.isfile(full_path):
        h._send_json({"error": "Config file not found"})
        return

    try:
        config = load_minimal_config(full_path)
        h._send_json({"config": config, "path": config_path})
    except Exception as e:
        h._send_json({"error": str(e)})


def _handle_get_conversion_table(h, params):
    """Return the current conversion table."""
    config_path = params.get("config_path", [None])[0]

    if config_path:
        try:
            config = load_minimal_config(_resolve_project_path(config_path))
        except Exception as e:
            h._send_json({"error": f"Failed to load config: {e}"})
            return
    else:
        config = get_default_config()
        config['Raw'] = _RAW_MEG_DIR
        config['BIDS'] = os.path.join(_PROJECT_ROOT, "BIDS")
        config['Root'] = _PROJECT_ROOT

    try:
        conversion_table, conversion_file = load_conversion_table(config, refresh_status=True)

        # Convert DataFrame to dict for JSON serialization
        table_data = conversion_table.fillna('').to_dict('records') if not conversion_table.empty else []

        h._send_json({
            "table": table_data,
            "file": conversion_file,
            "row_count": len(table_data)
        })
    except Exception as e:
        h._send_json({"error": str(e)})


def _handle_save_conversion_table(h, body):
    """Save the conversion table."""
    import pandas as pd

    table_data = body.get("table", [])
    file_path = body.get("file")

    if not file_path:
        h.send_error(400, "Missing file path")
        return

    full_path = _resolve_project_path(file_path)
    if not full_path:
        h.send_error(403, "Path outside project root")
        return

    try:
        df = pd.DataFrame(table_data)
        df.to_csv(full_path, sep='\t', index=False)
        h._send_json({"ok": True, "path": file_path, "rows": len(table_data)})
    except Exception as e:
        h.send_error(500, f"Failed to save table: {e}")


def _handle_run_analysis(h, body):
    """Generate/update conversion table (analyze step)."""
    force_scan = body.get("force_scan", False)
    client_config = body.get("config")

    if client_config:
        # Build config from client data
        config = get_default_config()
        config.update({
            'Name': client_config.get('project_name', 'MEG Dataset'),
            'Root': _PROJECT_ROOT,
            'Raw': client_config.get('raw_dir', 'raw/meg'),
            'BIDS': client_config.get('bids_dir', 'BIDS'),
            'Tasks': client_config.get('tasks', []),
            'Conversion_file': client_config.get('conversion_file', 'logs/bids_conversion.tsv'),
            'config_file': client_config.get('config_file', 'meg_config.json'),
            'overwrite': client_config.get('overwrite', False),
        })
    else:
        config = get_default_config()
        config['Raw'] = _RAW_MEG_DIR
        config['BIDS'] = os.path.join(_PROJECT_ROOT, "BIDS")
        config['Root'] = _PROJECT_ROOT

    try:
        conversion_table, conversion_file, run_conversion = update_conversion_table(config, force_scan=force_scan)

        # Save the table
        conversion_table.to_csv(conversion_file, sep='\t', index=False)

        table_data = conversion_table.fillna('').to_dict('records') if not conversion_table.empty else []

        h._send_json({
            "ok": True,
            "table": table_data,
            "file": conversion_file,
            "run_conversion": run_conversion,
            "row_count": len(table_data)
        })
    except Exception as e:
        h._send_json({"error": str(e)})


def _handle_run_bidsify(h, body):
    """Execute BIDS conversion."""
    verbose = body.get("verbose", False)
    client_config = body.get("config")

    if client_config:
        # Build config from client data
        config = get_default_config()
        config.update({
            'Name': client_config.get('project_name', 'MEG Dataset'),
            'Root': _PROJECT_ROOT,
            'Raw': client_config.get('raw_dir', 'raw/meg'),
            'BIDS': client_config.get('bids_dir', 'BIDS'),
            'Tasks': client_config.get('tasks', []),
            'Conversion_file': client_config.get('conversion_file', 'logs/bids_conversion.tsv'),
            'config_file': client_config.get('config_file', 'meg_config.json'),
            'overwrite': client_config.get('overwrite', False),
        })
    else:
        config = get_default_config()
        config['Raw'] = _RAW_MEG_DIR
        config['BIDS'] = os.path.join(_PROJECT_ROOT, "BIDS")
        config['Root'] = _PROJECT_ROOT

    try:
        # Run conversion (this may take a while)
        create_dataset_description(config)
        create_proc_description(config)
        conversion_table, conversion_file = load_conversion_table(config, refresh_status=False)
        bidsify(config, conversion_table=conversion_table, conversion_file=conversion_file, verbose=verbose)
        update_sidecars(config)

        h._send_json({"ok": True, "message": "BIDS conversion completed"})
    except Exception as e:
        h._send_json({"error": str(e)})


def _handle_run_report(h, body):
    """Generate BIDS report and QA analysis."""
    client_config = body.get("config")

    if client_config:
        # Build config from client data
        config = get_default_config()
        config.update({
            'Name': client_config.get('project_name', 'MEG Dataset'),
            'Root': _PROJECT_ROOT,
            'Raw': client_config.get('raw_dir', 'raw/meg'),
            'BIDS': client_config.get('bids_dir', 'BIDS'),
            'Tasks': client_config.get('tasks', []),
            'Conversion_file': client_config.get('conversion_file', 'logs/bids_conversion.tsv'),
            'config_file': client_config.get('config_file', 'meg_config.json'),
            'overwrite': client_config.get('overwrite', False),
        })
    else:
        config = get_default_config()
        config['Raw'] = _RAW_MEG_DIR
        config['BIDS'] = os.path.join(_PROJECT_ROOT, "BIDS")
        config['Root'] = _PROJECT_ROOT

    try:
        conversion_table, _ = load_conversion_table(config, refresh_status=False)
        update_bids_report(conversion_table, config)

        # Run QA analysis
        qa_results = run_qa_analysis(config, conversion_table)

        logPath = setLogPath(config)
        results_file = os.path.join(logPath, 'bids_results.json')

        if 'error' not in qa_results and os.path.exists(results_file):
            with open(results_file, 'r') as f:
                report = json.load(f)
            report['QA Analysis'] = {
                'timestamp': qa_results.get('timestamp', datetime.now().isoformat()),
                'summary': qa_results.get('summary', {}),
                'findings': qa_results.get('findings', []),
            }
            with open(results_file, 'w') as f:
                json.dump(report, f, indent=4, allow_nan=False)

        h._send_json({
            "ok": True,
            "report_file": results_file if os.path.exists(results_file) else None,
            "qa_summary": qa_results.get('summary', {}) if 'error' not in qa_results else None
        })
    except Exception as e:
        h._send_json({"error": str(e)})


def _handle_get_report(h, body):
    """Get the BIDS report data."""
    client_config = body.get("config")

    if client_config:
        # Build config from client data
        config = get_default_config()
        config.update({
            'Name': client_config.get('project_name', 'MEG Dataset'),
            'Root': _PROJECT_ROOT,
            'Raw': client_config.get('raw_dir', 'raw/meg'),
            'BIDS': client_config.get('bids_dir', 'BIDS'),
            'Tasks': client_config.get('tasks', []),
            'Conversion_file': client_config.get('conversion_file', 'logs/bids_conversion.tsv'),
            'config_file': client_config.get('config_file', 'meg_config.json'),
            'overwrite': client_config.get('overwrite', False),
        })
    else:
        config = get_default_config()
        config['Root'] = _PROJECT_ROOT

    logPath = setLogPath(config)
    report_file = os.path.join(logPath, 'bids_results.json')

    if not os.path.exists(report_file):
        h._send_json({"error": "No report found"})
        return

    try:
        with open(report_file, 'r') as f:
            report_data = json.load(f)
        h._send_json({"report": report_data})
    except Exception as e:
        h._send_json({"error": f"Failed to read report: {e}"})


# ── Registration ───────────────────────────────────────────────────────────────

def register(get_routes, post_routes):
    """Populate get_routes and post_routes with this tab's endpoints."""
    get_routes["/meg-get-config"] = _handle_get_config
    get_routes["/meg-get-project-root"] = _handle_get_project_root
    get_routes["/meg-load-config"] = _handle_load_config
    get_routes["/meg-get-conversion-table"] = _handle_get_conversion_table

    post_routes["/meg-save-conversion-table"] = _handle_save_conversion_table
    post_routes["/meg-run-analysis"] = _handle_run_analysis
    post_routes["/meg-run-bidsify"] = _handle_run_bidsify
    post_routes["/meg-run-report"] = _handle_run_report
    post_routes["/meg-get-report"] = _handle_get_report
    post_routes["/meg-validate-paths"] = _handle_validate_paths
