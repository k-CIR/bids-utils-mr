#!/usr/bin/env python3
"""Discover DICOM sessions and run dcm2bids in parallel worker processes."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import queue
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


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
    """Scan *dicom_root* and return a list of session dicts.

    Supported layouts:
    - ``sub-<ID>/ses-<SES>/``  → participant=ID, session=SES
    - ``sub-<ID>/``            → participant=ID, session=None
    - ``<NUM>_<DATE>_<TIME>/`` → participant=NUM, session=None

    Returns list of::

        {
            "folder":      str,   # absolute path to the DICOM folder
            "participant": str,   # participant label (no sub- prefix)
            "session":     str | None,
        }
    """
    if not os.path.isdir(dicom_root):
        return []

    sessions = []

    for entry in sorted(os.listdir(dicom_root)):
        abs_entry = os.path.join(dicom_root, entry)
        if not os.path.isdir(abs_entry):
            continue

        # Pattern: sub-<ID>
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
                        "folder":      os.path.join(abs_entry, ses_entry),
                        "participant": participant,
                        "session":     ses_m.group(1) if ses_m else None,
                        "label":       f"sub-{participant}/ses-{ses_m.group(1)}" if ses_m else f"sub-{participant}",
                    })
            else:
                sessions.append({
                    "folder":      abs_entry,
                    "participant": participant,
                    "session":     None,
                    "label":       f"sub-{participant}",
                })
            continue

        # Pattern: <NUM>_<YYYYMMDD>_<HHMMSS>
        m = re.match(r"^(\d+)_\d{8}_\d{6}$", entry)
        if m:
            sessions.append({
                "folder":      abs_entry,
                "participant": m.group(1),
                "session":     None,
                "label":       entry,
            })
            continue

    return sessions


# ── Active job tracking ────────────────────────────────────────────────────────

_jobs_lock = threading.Lock()
_active_jobs = {}   # job_id -> { "status": "running"|"done", "log": [...] }
_job_counter = 0


def _new_job_id():
    global _job_counter
    with _jobs_lock:
        _job_counter += 1
        return str(_job_counter)


def start_conversion(sessions, dicom_root, output_dir, config_file, max_workers=8, clobber=False):
    """Launch dcm2bids for each selected session in a thread pool.

    Returns a *job_id* string that can be used with :func:`stream_job`.
    """
    if not _DCM2BIDS:
        raise RuntimeError("dcm2bids not found. Is it installed in this Python environment?")

    job_id = _new_job_id()
    log_queue = queue.Queue()

    with _jobs_lock:
        _active_jobs[job_id] = {
            "status": "running",
            "log":    [],
            "queue":  log_queue,
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


def _remove_session_bids_files(sess, output_dir):
    """Pre-delete existing BIDS output files for *sess* to work around
    dcm2bids bug where --clobber logs the overwrite but never renames .nii.gz.
    Deletes files only; directory structure is preserved.
    """
    parts = ["sub-" + sess["participant"]]
    if sess.get("session"):
        parts.append("ses-" + sess["session"])
    target = os.path.join(output_dir, *parts)
    if not os.path.isdir(target):
        return
    for dirpath, _dirs, filenames in os.walk(target):
        for fname in filenames:
            try:
                os.remove(os.path.join(dirpath, fname))
            except OSError:
                pass


def _run_single(job_id, sess, output_dir, config_file, clobber):
    label = sess["label"]
    if clobber:
        _remove_session_bids_files(sess, output_dir)
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
        cmd += ["--clobber", "--force_dcm2bids"]

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
    """Generator yielding log entries for *job_id* as they arrive.

    Each entry is a dict; the final entry has ``{"type": "done"}``.
    """
    with _jobs_lock:
        job = _active_jobs.get(job_id)
        if not job:
            yield {"type": "error", "text": "Job not found"}
            return
        # Yield already-recorded entries first
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
