# core/jobs.py
"""
Single-worker job queue. Every heavy task (generation, edit export, upload)
goes through here so only one runs at a time and the UI can show its state.
"""

import queue
import threading
import datetime
import uuid

_queue = queue.Queue()
_jobs = {}            # id -> job dict (public state only)
_order = []           # ids, oldest first
_lock = threading.Lock()
_emit = None          # callable(event, payload)
_log = None
_started = False

MAX_KEPT = 50


def _public(job):
    return {k: job[k] for k in ("id", "kind", "label", "status", "progress", "error", "result", "created")}


def _push(job):
    if _emit:
        try:
            _emit("job_update", _public(job))
        except Exception:
            pass


def init(emit, log):
    """Start the worker thread once."""
    global _emit, _log, _started
    _emit, _log = emit, log
    if not _started:
        _started = True
        threading.Thread(target=_worker, daemon=True).start()


def submit(kind: str, label: str, fn) -> str:
    """Queue fn(progress) where progress(pct: float) reports 0-100. Returns job id."""
    job_id = uuid.uuid4().hex[:8]
    job = {
        "id": job_id, "kind": kind, "label": label, "status": "queued",
        "progress": 0, "error": None, "result": None,
        "created": datetime.datetime.now().strftime("%H:%M:%S"),
    }
    with _lock:
        _jobs[job_id] = job
        _order.append(job_id)
        while len(_order) > MAX_KEPT:
            old = _order[0]
            if _jobs[old]["status"] in ("queued", "running"):
                break
            _order.pop(0)
            _jobs.pop(old, None)
    _queue.put((job_id, fn))
    _push(job)
    return job_id


def list_jobs():
    with _lock:
        return [_public(_jobs[i]) for i in reversed(_order)]


def _worker():
    while True:
        job_id, fn = _queue.get()
        job = _jobs.get(job_id)
        if job is None:
            continue
        job["status"] = "running"
        _push(job)
        last = [-1]

        def progress(pct):
            pct = max(0, min(100, int(pct)))
            if pct != last[0]:
                last[0] = pct
                job["progress"] = pct
                _push(job)

        try:
            result = fn(progress)
            if result is False or result is None:
                job["status"] = "error"
                job["error"] = job["error"] or "Échec (voir les logs)"
            else:
                job["status"] = "done"
                job["progress"] = 100
                job["result"] = result if isinstance(result, str) else None
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
            if _log:
                _log(f"Job {job['label']} : {e}", "error")
        _push(job)
