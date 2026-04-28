#!/usr/bin/env python3
"""Helpers for reading dcm2bids helper output and managing the BIDS config file."""
import csv
import json
import os
import re
from pathlib import Path
import gzip
import struct

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_HELPER_DIR = os.path.join(
    _SCRIPT_DIR, "dcm2bids_helper", "tmp_dcm2bids", "helper"
)
_REPO_ROOT = str(Path(_SCRIPT_DIR).resolve().parents[1])
CONFIG_FILE = os.path.join(_REPO_ROOT, "dcm2bids_config_mr.json")
_RECODE_CSV = os.path.join(_SCRIPT_DIR, "sessions_recode_mr.csv")


def read_helper_jsons():
    """Return filtered, deduplicated rows from helper JSON files.

    Rules:
    - Only files whose names start with '0' are included.
    - Series whose SeriesDescription starts with 'Scout' (case-insensitive)
      are excluded.
    - Rows with identical (SeriesNumber, SeriesDescription) are collapsed into
      one representative row; ``duplicate_count`` records how many extras
      were discarded.

    Returns a list of dicts::

        {
            "series_number":      int | None,
            "series_description": str,
            "duplicate_count":    int,   # 0 = unique, >0 = duplicates present
        }
    """
    if not os.path.isdir(_HELPER_DIR):
        return []

    rows = []
    seen = {}  # (series_number, series_description) -> index in rows

    files = sorted(
        f for f in os.listdir(_HELPER_DIR)
        if f.endswith(".json") and f.startswith("0")
    )

    for fname in files:
        fpath = os.path.join(_HELPER_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue

        series_desc = data.get("SeriesDescription", "")
        if re.match(r"^Scout", series_desc, re.IGNORECASE):
            continue
        # Exclude dose information series which sometimes appear as CT-like
        # but only contain dose reports or per-slice dose metadata.
        if re.search(r"\bdose\b", series_desc, re.IGNORECASE):
            continue

        # Exclude series with ImageType containing DERIVED or SECONDARY,
        # which typically indicate processed/summary images (e.g., dose
        # reports, screenshots, QA exports) rather than raw acquisitions.
        image_type_list = data.get("ImageType") or []
        if any(t in ("DERIVED", "SECONDARY") for t in image_type_list):
            continue

        # If dcm2niix produced a .nii.gz alongside the helper JSON, read
        # its NIfTI header to detect single-slice conversions (e.g. dose
        # reports converted as 1-slice images). Skip series with nz <= 1.
        base = os.path.splitext(fname)[0]
        nii_path = os.path.join(_HELPER_DIR, base + ".nii.gz")
        try:
            if os.path.isfile(nii_path):
                with gzip.open(nii_path, "rb") as fh:
                    hdr = fh.read(352)
                if len(hdr) >= 352:
                    # dims are 16 short ints starting at byte offset 40
                    dims = struct.unpack_from('<16h', hdr, 40)
                    nz = int(dims[3]) if len(dims) >= 4 else 0
                    if nz <= 1:
                        continue
        except Exception:
            # If header read fails, fall back to keeping the row.
            pass

        series_num = data.get("SeriesNumber")
        pulse_seq  = data.get("PulseSequenceName", "")
        image_type = ", ".join(data.get("ImageType") or [])
        key = (series_num, series_desc, pulse_seq, image_type)
        if key in seen:
            rows[seen[key]]["duplicate_count"] += 1
        else:
            seen[key] = len(rows)
            rows.append({
                "series_number":       series_num,
                "series_description":  series_desc,
                "pulse_sequence_name": pulse_seq,
                "image_type":          image_type,
                "duplicate_count":     0,
            })

    return rows


def load_config():
    """Load ``dcm2bids_config_mr.json``.  Returns ``None`` if the file is absent."""
    if not os.path.isfile(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def save_config(data):
    """Overwrite ``dcm2bids_config_mr.json`` with *data* (pretty-printed JSON)."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4)


def load_recode_table():
    """Return recode dict keyed by folder_label.

    Returns::

        {
            "<folder_label>": {
                "recoded_participant": str,   # empty string = use original
                "recoded_session":     str,
            },
            ...
        }
    """
    result = {}
    if not os.path.isfile(_RECODE_CSV):
        return result
    with open(_RECODE_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = row.get("folder_label", "").strip()
            if label:
                result[label] = {
                    "recoded_participant": row.get("recoded_participant", "").strip(),
                    "recoded_session":     row.get("recoded_session", "").strip(),
                }
    return result


def save_recode_table(recode_dict):
    """Write *recode_dict* to sessions_recode_mr.csv (sorted by label)."""
    with open(_RECODE_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["folder_label", "recoded_participant", "recoded_session"],
        )
        writer.writeheader()
        for label, rec in sorted(recode_dict.items()):
            writer.writerow({
                "folder_label":        label,
                "recoded_participant": rec.get("recoded_participant") or "",
                "recoded_session":     rec.get("recoded_session") or "",
            })
