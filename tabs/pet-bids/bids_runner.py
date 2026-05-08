#!/usr/bin/env python3
"""Discover DICOM sessions and run dcm2bids in parallel worker processes."""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading

try:
    import pydicom
except ImportError:
    pydicom = None


def _find_executable(name):
    path = shutil.which(name)
    if path:
        return path
    candidate = os.path.join(os.path.dirname(os.path.realpath(sys.executable)), name)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    home = os.path.expanduser("~")
    for prefix in [
        "/opt/anaconda3", "/opt/miniconda3", "/opt/conda",
        os.path.join(home, "anaconda3"), os.path.join(home, "miniconda3"),
        os.path.join(home, "mambaforge"), os.path.join(home, "miniforge3"),
    ]:
        c = os.path.join(prefix, "bin", name)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


_DCM2BIDS = _find_executable("dcm2bids")


def discover_sessions(dicom_root):
    """Scan *dicom_root* and return a list of session dicts."""
    if not os.path.isdir(dicom_root):
        return []

    sessions = []

    for entry in sorted(os.listdir(dicom_root)):
        abs_entry = os.path.join(dicom_root, entry)
        if not os.path.isdir(abs_entry):
            continue

        m = re.match(r"^sub-(.+)$", entry)
        if m:
            participant = m.group(1)
            ses_dirs = sorted(
                s for s in os.listdir(abs_entry)
                if re.match(r"^ses-(.+)$", s)
                and os.path.isdir(os.path.join(abs_entry, s))
            )
            if ses_dirs:
                for ses_entry in ses_dirs:
                    ses_m = re.match(r"^ses-(.+)$", ses_entry)
                    sessions.append({
                        "folder": os.path.join(abs_entry, ses_entry),
                        "participant": participant,
                        "session": ses_m.group(1) if ses_m else None,
                        "label": f"sub-{participant}/ses-{ses_m.group(1)}" if ses_m else f"sub-{participant}",
                    })
            else:
                sessions.append({
                    "folder": abs_entry,
                    "participant": participant,
                    "session": None,
                    "label": f"sub-{participant}",
                })
            continue

        m = re.match(r"^(\d+)_\d{8}_\d{6}$", entry)
        if m:
            sessions.append({
                "folder": abs_entry,
                "participant": m.group(1),
                "session": None,
                "label": entry,
            })
            continue

    return sessions


def _read_dicom_metadata_from_folder(folder):
    """Read DICOM metadata from first available DICOM file in folder.
    
    Returns dict with keys: series_description, series_number, protocol_name, 
    modality, image_type, radiopharmaceutical. Returns empty dict if no DICOM found.
    """
    if not os.path.isdir(folder):
        return {}
    
    if not pydicom:
        # pydicom not available, return empty dict but don't fail
        return {}
    
    for root, _, files in os.walk(folder):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                ds = pydicom.dcmread(
                    fpath,
                    stop_before_pixels=True,
                    force=True,
                    specific_tags=["SeriesDescription", "SeriesNumber", "ProtocolName", 
                                   "Modality", "ImageType", "Radiopharmaceutical"],
                )
                image_type_list = getattr(ds, "ImageType", None) or []
                metadata = {
                    "series_description": str(getattr(ds, "SeriesDescription", "") or "").strip(),
                    "series_number": getattr(ds, "SeriesNumber", None),
                    "protocol_name": str(getattr(ds, "ProtocolName", "") or "").strip(),
                    "modality": str(getattr(ds, "Modality", "") or "").strip(),
                    "image_type": ", ".join(str(x) for x in image_type_list),
                    "radiopharmaceutical": str(getattr(ds, "Radiopharmaceutical", "") or "").strip(),
                }
                # Only return if we got at least the series description
                if metadata.get("series_description"):
                    return metadata
            except Exception:
                continue
    
    return {}


def enrich_sessions_with_metadata(sessions):
    """Augment session dicts with DICOM metadata from the folder.
    
    Returns new list of session dicts with added metadata fields.
    """
    enriched = []
    for sess in sessions:
        folder = sess.get("folder")
        if folder and os.path.isdir(folder):
            metadata = _read_dicom_metadata_from_folder(folder)
            s = dict(sess)
            s.update(metadata)
            enriched.append(s)
        else:
            enriched.append(sess)
    return enriched
_jobs_lock = threading.Lock()
_active_jobs = {}
_job_counter = 0


def _new_job_id():
    global _job_counter
    with _jobs_lock:
        _job_counter += 1
        return str(_job_counter)


def start_conversion(sessions, dicom_root, output_dir, config_file, max_workers=8, clobber=False):
    """Launch dcm2bids for each selected session in a thread pool."""
    if not _DCM2BIDS:
        raise RuntimeError("dcm2bids not found. Is it installed in this Python environment?")

    job_id = _new_job_id()
    log_queue = queue.Queue()

    with _jobs_lock:
        _active_jobs[job_id] = {
            "status": "running",
            "log": [],
            "queue": log_queue,
        }

    thread = threading.Thread(
        target=_run_pool,
        args=(job_id, sessions, dicom_root, output_dir, config_file, max_workers, clobber),
        daemon=True,
    )
    thread.start()
    return job_id


def _run_pool(job_id, sessions, dicom_root, output_dir, config_file, max_workers, clobber):
    sem = threading.Semaphore(max_workers)
    threads = []

    def _worker(sess):
        with sem:
            _run_single(job_id, sess, output_dir, config_file, clobber)

    for sess in sessions:
        t = threading.Thread(target=_worker, args=(sess,), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    _append_log(job_id, {"type": "done"})
    with _jobs_lock:
        _active_jobs[job_id]["status"] = "done"


def _run_single(job_id, sess, output_dir, config_file, clobber):
    label = sess["label"]
    cmd = [
        _DCM2BIDS,
        "-d", sess["folder"],
        "-p", sess["participant"],
        "-c", config_file,
        "-o", output_dir,
    ]
    if sess.get("session"):
        cmd += ["-s", sess["session"]]
    if clobber:
        cmd.append("--clobber")

    _append_log(job_id, {"type": "start", "label": label, "cmd": " ".join(cmd)})
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            for line in proc.stdout:
                _append_log(job_id, {"type": "line", "label": label, "text": line.rstrip("\n")})
            proc.wait(timeout=600)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            _append_log(job_id, {"type": "line", "label": label, "text": "ERROR: timed out after 10 minutes"})
            rc = 1
    except Exception as exc:
        _append_log(job_id, {"type": "line", "label": label, "text": f"ERROR: {exc}"})
        rc = 1

    _append_log(job_id, {"type": "exit", "label": label, "returncode": rc})


def _append_log(job_id, entry):
    with _jobs_lock:
        job = _active_jobs.get(job_id)
        if job:
            job["log"].append(entry)
            if "queue" in job:
                job["queue"].put(entry)


def stream_job(job_id):
    """Generator yielding log entries for *job_id* as they arrive."""
    with _jobs_lock:
        job = _active_jobs.get(job_id)
        if not job:
            yield {"type": "error", "text": "Job not found"}
            return
        backlog = list(job["log"])
        q = job.get("queue")

    for entry in backlog:
        yield entry
        if entry.get("type") == "done":
            return

    if q is None:
        return

    while True:
        try:
            entry = q.get(timeout=30)
            yield entry
            if entry.get("type") == "done":
                break
        except queue.Empty:
            yield {"type": "keepalive"}
