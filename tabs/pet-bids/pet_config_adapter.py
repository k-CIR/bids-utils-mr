#!/usr/bin/env python3
"""Adapter for Dcm2Bids-style PET configs.

This module keeps the PET config file in the same shape as the MR config
editor while providing helpers that translate the config into a normalized
conversion plan for the PET runner.
"""
import json
import os
import re
from pathlib import Path

_TAB_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = str(Path(_TAB_DIR).resolve().parents[1])
DEFAULT_CONFIG_FILE = os.path.join(_REPO_ROOT, "dcm2bids_config_pet.json")


def load_config(config_file=DEFAULT_CONFIG_FILE):
    if not os.path.isfile(config_file):
        return None
    with open(config_file, encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_text(value):
    return str(value or "").strip()


def _normalize_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_normalize_text(item) for item in value if _normalize_text(item)]
    text = _normalize_text(value)
    if not text:
        return []
    return [text]


def _normalize_dcm2niix_options(value):
    if isinstance(value, list):
        return [_normalize_text(item) for item in value if _normalize_text(item)]
    text = _normalize_text(value)
    if not text:
        return []
    return [token for token in text.split() if token]


def _match_scalar(expected, actual):
    actual_text = _normalize_text(actual)
    if isinstance(expected, (list, tuple, set)):
        return any(_match_scalar(item, actual_text) for item in expected)

    expected_text = _normalize_text(expected)
    if not expected_text:
        return actual_text == ""

    if expected_text.startswith("/") and expected_text.endswith("/") and len(expected_text) > 2:
        try:
            return re.search(expected_text[1:-1], actual_text, flags=re.IGNORECASE) is not None
        except re.error:
            return False

    return actual_text.casefold() == expected_text.casefold()


def _match_image_type(expected, actual):
    expected_items = _normalize_list(expected)
    if not expected_items:
        return True

    actual_items = [item.strip().casefold() for item in _normalize_list(actual)]
    if not actual_items:
        return False

    return all(item.casefold() in actual_items for item in expected_items)


def matches_criteria(criteria, row):
    if not isinstance(criteria, dict):
        return True

    row_map = {
        "SeriesDescription": row.get("series_description"),
        "SeriesNumber": row.get("series_number"),
        "ProtocolName": row.get("protocol_name"),
        "Modality": row.get("modality"),
        "ImageType": row.get("image_type"),
        "Radiopharmaceutical": row.get("radiopharmaceutical"),
    }

    for key, expected in criteria.items():
        actual = row_map.get(key)
        if key == "ImageType":
            if not _match_image_type(expected, actual):
                return False
            continue
        if not _match_scalar(expected, actual):
            return False
    return True


def _entity_token(entity):
    text = _normalize_text(entity)
    if not text:
        return ""
    if text.casefold().startswith("desc-"):
        return ""
    return text


def render_entities(description):
    tokens = []
    for entity in _normalize_list(description.get("custom_entities")):
        token = _entity_token(entity)
        if token:
            tokens.append(token)

    return tokens


def build_conversion_plan(config, session, recode=None):
    """Build a normalized plan for a single PET session.

    The plan stays close to the Dcm2Bids config model: descriptions remain
    intact, but the runner receives a compact structure that can be consumed by
    a container entrypoint or another backend implementation.
    """
    recode = recode or {}
    descriptions = config.get("descriptions") if isinstance(config, dict) else []
    if not isinstance(descriptions, list):
        descriptions = []

    recoded_participant = _normalize_text(session.get("participant"))
    recoded_session = _normalize_text(session.get("session"))

    return {
        "session": {
            "label": _normalize_text(session.get("label")),
            "folder": _normalize_text(session.get("folder")),
            "participant": recoded_participant,
            "session": recoded_session,
        },
        "recode": {
            "participant": _normalize_text(recode.get("recoded_participant")),
            "session": _normalize_text(recode.get("recoded_session")),
        },
        "descriptions": [
            {
                "index": index,
                "datatype": _normalize_text(desc.get("datatype")),
                "suffix": _normalize_text(desc.get("suffix")),
                "criteria": dict(desc.get("criteria") or {}),
                "custom_entities": [
                    token
                    for token in (
                        _entity_token(entity)
                        for entity in _normalize_list(desc.get("custom_entities"))
                    )
                    if token
                ],
                "sidecar_changes": dict(desc.get("sidecar_changes") or {}),
                "entities": render_entities(desc),
            }
            for index, desc in enumerate(descriptions)
            if isinstance(desc, dict)
        ],
        "config_file": _normalize_text(config.get("__config_file__")) if isinstance(config, dict) else "",
        "dcm2niix_options": _normalize_dcm2niix_options(config.get("dcm2niixOptions")) if isinstance(config, dict) else [],
    }


def match_descriptions(config, row):
    descriptions = config.get("descriptions") if isinstance(config, dict) else []
    if not isinstance(descriptions, list):
        return []

    matches = []
    for index, desc in enumerate(descriptions):
        if not isinstance(desc, dict):
            continue
        criteria = desc.get("criteria") or {}
        if matches_criteria(criteria, row):
            matches.append((index, desc))
    return matches
