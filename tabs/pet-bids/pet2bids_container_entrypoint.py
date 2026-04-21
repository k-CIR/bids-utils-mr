#!/usr/bin/env python3
"""Container entrypoint for PET BIDS conversion.

The host-side runner passes a normalized conversion plan to this script once
the PET container image exists. The actual PET execution API still needs to be
filled in against the pinned pypet2bids version inside that image.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import site


def _read_plan(plan_file):
    with open(plan_file, encoding="utf-8") as fh:
        return json.load(fh)


def _ensure_runtime_packages():
    marker = os.path.join(os.path.expanduser("~"), ".cache", "pet2bids_runtime_ready")
    local_bin = os.path.join(site.USER_BASE, "bin")
    os.environ["PATH"] = local_bin + os.pathsep + os.environ.get("PATH", "")

    if os.path.isfile(marker):
        return

    os.makedirs(os.path.dirname(marker), exist_ok=True)
    cmd = [
        "python",
        "-m",
        "pip",
        "install",
        "--user",
        "--no-input",
        "pypet2bids",
        "pydicom",
        "pandas",
        "json-maj",
        "dcm2niix",
    ]
    print("Bootstrapping PET runtime packages...")
    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0:
        raise RuntimeError("Failed to install runtime PET packages inside container")

    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("ok\n")


def _norm_text(value):
    return str(value or "").strip()


def _matches_text(expected, actual):
    exp = _norm_text(expected)
    act = _norm_text(actual)
    if not exp:
        return False
    if exp.startswith("/") and exp.endswith("/") and len(exp) > 2:
        try:
            return re.search(exp[1:-1], act, flags=re.IGNORECASE) is not None
        except re.error:
            return False
    return exp.casefold() == act.casefold()


def _first_expected(criteria, key):
    value = (criteria or {}).get(key)
    if isinstance(value, list):
        for item in value:
            text = _norm_text(item)
            if text:
                return text
        return ""
    return _norm_text(value)


def _resolve_pet_source_dir(src, matched_desc=None):
    src = os.path.realpath(str(src or "").strip())
    if not src or not os.path.isdir(src):
        raise ValueError(f"Source folder does not exist: {src}")

    criteria = (matched_desc or {}).get("criteria") or {}
    expected_sd = _first_expected(criteria, "SeriesDescription")
    expected_pn = _first_expected(criteria, "ProtocolName")

    try:
        import pydicom  # type: ignore
    except Exception:
        pydicom = None

    dir_stats = {}
    for root, _, files in os.walk(src):
        if not files:
            continue
        stat = dir_stats.setdefault(root, {
            "dicom_count": 0,
            "sd_match": 0,
            "pn_match": 0,
            "pt_modality": 0,
            "pt_named_dir": os.path.basename(root).lower() in {"pt", "pet"},
        })
        for fname in files:
            fpath = os.path.join(root, fname)
            if not os.path.isfile(fpath):
                continue
            if pydicom is None:
                # Without pydicom, keep a simple file-count based fallback.
                stat["dicom_count"] += 1
                continue
            try:
                ds = pydicom.dcmread(
                    fpath,
                    stop_before_pixels=True,
                    force=True,
                    specific_tags=["SeriesDescription", "ProtocolName", "Modality"],
                )
            except Exception:
                continue

            stat["dicom_count"] += 1
            if _matches_text(expected_sd, getattr(ds, "SeriesDescription", "")):
                stat["sd_match"] += 1
            if _matches_text(expected_pn, getattr(ds, "ProtocolName", "")):
                stat["pn_match"] += 1
            modality = _norm_text(getattr(ds, "Modality", "")).upper()
            if modality in {"PT", "PET"}:
                stat["pt_modality"] += 1

    if not dir_stats:
        return src

    def _score(item):
        path, st = item
        matches = st["sd_match"] + st["pn_match"]
        has_criteria = bool(expected_sd or expected_pn)
        criteria_hit = matches > 0 if has_criteria else False
        return (
            1 if criteria_hit else 0,
            matches,
            st["pt_modality"],
            1 if st["pt_named_dir"] else 0,
            st["dicom_count"],
            -len(path),
        )

    best_dir, best_stat = max(dir_stats.items(), key=_score)

    # If criteria were provided but no folder matched at all, prefer PET-like modality/dir with max files.
    if (expected_sd or expected_pn) and (best_stat["sd_match"] + best_stat["pn_match"] == 0):
        best_dir, _ = max(
            dir_stats.items(),
            key=lambda item: (
                item[1]["pt_modality"],
                1 if item[1]["pt_named_dir"] else 0,
                item[1]["dicom_count"],
                -len(item[0]),
            ),
        )

    print(f"Resolved PET source folder: {best_dir}")
    if expected_sd or expected_pn:
        print(
            "Selection criteria:",
            f"SeriesDescription={expected_sd or '<none>'}",
            f"ProtocolName={expected_pn or '<none>'}",
        )
    return best_dir


def _entity_value(entities, key):
    prefix = f"{key}-"
    for item in entities or []:
        item = str(item or "").strip()
        if item.startswith(prefix):
            return item[len(prefix):]
    return ""


def _format_subject(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return value if value.startswith("sub-") else f"sub-{value}"


def _format_session(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return value if value.startswith("ses-") else f"ses-{value}"


def _build_destination(output_dir, session):
    subject = _format_subject(session.get("participant"))
    ses = _format_session(session.get("session"))
    if not subject:
        raise ValueError("Session participant is missing in conversion plan")

    dest = os.path.join(output_dir, subject)
    if ses:
        dest = os.path.join(dest, ses)
    return os.path.join(dest, "pet")


def _extract_kwargs(matched_desc):
    sidecar = matched_desc.get("sidecar_changes") or {}
    out = []
    for key, value in sidecar.items():
        out.append(f"{key}={json.dumps(value, ensure_ascii=True)}")
    return out


def _expected_output_prefix(session, matched_desc):
    subject = _format_subject(session.get("participant"))
    ses = _format_session(session.get("session"))
    parts = [subject]
    if ses:
        parts.append(ses)

    entities = (matched_desc or {}).get("entities") or []
    for item in entities:
        token = _norm_text(item)
        if token:
            parts.append(token)

    return "_".join(parts) + "_"


def _find_existing_pet_output(dest_dir, session, matched_desc):
    if not os.path.isdir(dest_dir):
        return None

    prefix = _expected_output_prefix(session, matched_desc)

    candidates = []
    try:
        for name in os.listdir(dest_dir):
            full = os.path.join(dest_dir, name)
            if not os.path.isfile(full):
                continue
            if prefix and not name.startswith(prefix):
                continue
            if name.endswith("_pet.nii") or name.endswith("_pet.nii.gz"):
                candidates.append(name)
            elif name.endswith("_pet.json"):
                candidates.append(name)
    except OSError:
        return None

    if not candidates:
        return None

    # Prefer nifti artifacts over json when both exist.
    candidates.sort(key=lambda n: (0 if n.endswith(".nii.gz") else 1 if n.endswith(".nii") else 2, n))
    return candidates[0]


def _build_command(plan):
    session = plan.get("session") or {}
    launch = plan.get("launch") or {}
    selected = plan.get("matched_descriptions") or []
    matched = selected[0] if selected else {}
    entities = matched.get("entities") or []

    src = str(session.get("folder") or "").strip()
    out_root = str(launch.get("output_dir") or "").strip()
    if not src:
        raise ValueError("Session folder missing in conversion plan")
    if not out_root:
        raise ValueError("Output directory missing in conversion plan")

    src = _resolve_pet_source_dir(src, matched)

    destination = _build_destination(out_root, session)

    cmd = [
        "python",
        "-m",
        "pypet2bids.dcm2niix4pet",
        src,
        "--destination-path",
        destination,
    ]

    trc = _entity_value(entities, "trc")
    run = _entity_value(entities, "run")
    rec = _entity_value(entities, "rec")
    if trc:
        cmd.extend(["--trc", trc])
    if run:
        cmd.extend(["--run", run])
    if rec:
        cmd.extend(["--rec", rec])

    kwargs_args = _extract_kwargs(matched)
    if kwargs_args:
        cmd.append("--kwargs")
        cmd.extend(kwargs_args)

    dcm2niix_options = plan.get("dcm2niix_options") or []
    if dcm2niix_options:
        cmd.append("--dcm2niix-options")
        cmd.extend([str(item) for item in dcm2niix_options])

    return cmd, destination, matched


def main(argv=None):
    parser = argparse.ArgumentParser(description="PET BIDS conversion entrypoint")
    parser.add_argument("--plan-file", required=True, help="Path to a JSON conversion plan")
    args = parser.parse_args(argv)

    plan = _read_plan(args.plan_file)
    session = plan.get("session", {}) if isinstance(plan, dict) else {}
    selected = plan.get("matched_descriptions", []) if isinstance(plan, dict) else []

    print(f"PET plan loaded for {session.get('label', 'unknown session')}")
    print(f"Matched descriptions: {len(selected)}")

    if os.environ.get("PET2BIDS_DRY_RUN", "").strip():
        print("PET2BIDS_DRY_RUN is set; skipping execution.")
        return 0

    if not os.environ.get("DCM2NIIX_PATH"):
        dcm2niix_path = shutil.which("dcm2niix")
        if dcm2niix_path:
            os.environ["DCM2NIIX_PATH"] = dcm2niix_path
            print(f"Using dcm2niix at {dcm2niix_path}")

    try:
        import pypet2bids  # type: ignore  # noqa: F401
    except Exception:
        allow_bootstrap = os.environ.get("PET2BIDS_ALLOW_BOOTSTRAP", "0").strip().lower() in {"1", "true", "yes"}
        if not allow_bootstrap:
            print(
                "pypet2bids is not available inside the container. "
                "Use a fully built image or set PET2BIDS_ALLOW_BOOTSTRAP=1 for temporary bootstrapping.",
                file=sys.stderr,
            )
            return 1
        try:
            _ensure_runtime_packages()
            import pypet2bids  # type: ignore  # noqa: F401
        except Exception as exc:
            print(f"pypet2bids is not available inside the container: {exc}", file=sys.stderr)
            return 1

    try:
        cmd, destination, matched = _build_command(plan)
    except Exception as exc:
        print(f"Failed to build PET command from plan: {exc}", file=sys.stderr)
        return 1

    clobber = bool((plan.get("launch") or {}).get("clobber", False))
    existing_output = _find_existing_pet_output(destination, session, matched)
    if not clobber and existing_output:
        print(f"skipped because output {existing_output} already exists")
        return 0

    print("Executing:", " ".join(cmd))
    proc = subprocess.run(cmd, text=True)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
