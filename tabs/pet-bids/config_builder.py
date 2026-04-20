#!/usr/bin/env python3
"""PET config builder helpers based on dcm2bids helper output."""
import csv
import json
import os
import re
from pathlib import Path

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_HELPER_DIR = os.path.join(
    _SCRIPT_DIR, "dcm2bids_helper", "tmp_dcm2bids", "helper"
)
_REPO_ROOT = str(Path(_SCRIPT_DIR).resolve().parents[1])
CONFIG_FILE = os.path.join(_REPO_ROOT, "dcm2bids_config_pet.json")
_RECODE_CSV = os.path.join(_SCRIPT_DIR, "session_recode.csv")


def read_helper_jsons():
    """Return filtered, deduplicated rows from helper JSON files."""
    if not os.path.isdir(_HELPER_DIR):
        return []

    rows = []
    seen = {}

    files = sorted(
        f for f in os.listdir(_HELPER_DIR)
        if f.endswith(".json")
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

        series_num = data.get("SeriesNumber")
        protocol_name = data.get("ProtocolName", "")
        modality = data.get("Modality", "")
        modality_norm = str(modality or "").strip().upper()
        image_type = ", ".join(data.get("ImageType") or [])
        radiopharmaceutical = data.get("Radiopharmaceutical", "")
        if modality_norm not in {"PT", "CT", "PET"}:
            if radiopharmaceutical:
                modality_norm = "PT"
            else:
                continue
        key = (series_num, series_desc, protocol_name, modality_norm, image_type, radiopharmaceutical)

        if key in seen:
            rows[seen[key]]["duplicate_count"] += 1
        else:
            seen[key] = len(rows)
            rows.append({
                "series_number": series_num,
                "series_description": series_desc,
                "protocol_name": protocol_name,
                "modality": modality_norm,
                "image_type": image_type,
                "radiopharmaceutical": radiopharmaceutical,
                "duplicate_count": 0,
            })

    return rows


def load_config():
    if not os.path.isfile(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4)


def load_recode_table():
    """Return recode dict keyed by folder label."""
    result = {}
    if not os.path.isfile(_RECODE_CSV):
        return result
    with open(_RECODE_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = row.get("folder_label", "").strip()
            if label:
                result[label] = {
                    "recoded_participant": row.get("recoded_participant", "").strip(),
                    "recoded_session": row.get("recoded_session", "").strip(),
                }
    return result


def save_recode_table(recode_dict):
    """Write recode table to CSV sorted by folder label."""
    with open(_RECODE_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["folder_label", "recoded_participant", "recoded_session"],
        )
        writer.writeheader()
        for label, rec in sorted(recode_dict.items()):
            writer.writerow({
                "folder_label": label,
                "recoded_participant": rec.get("recoded_participant") or "",
                "recoded_session": rec.get("recoded_session") or "",
            })
