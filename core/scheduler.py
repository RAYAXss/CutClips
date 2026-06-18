# core/scheduler.py
"""
Background scheduler — runs in a daemon thread started at app launch.
Every 30 seconds it checks queue.json for jobs whose scheduled_at
time has passed and status is "pending", then dispatches uploads.
"""

import json
import time
import datetime
import threading
from pathlib import Path
from core.uploader import dispatch_upload, render_template


QUEUE_FILE = None   # Set by init_scheduler()
_log       = None   # Set by init_scheduler()
_lock      = threading.Lock()


# ══════════════════════════════════════════════════════
#  QUEUE I/O  (all protected by _lock)
# ══════════════════════════════════════════════════════

def load_queue() -> list:
    try:
        with open(QUEUE_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def save_queue(queue: list):
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)

def update_job(job_id: str, **kwargs):
    """Update fields on a specific job in-place."""
    with _lock:
        queue = load_queue()
        for job in queue:
            if job.get("id") == job_id:
                job.update(kwargs)
                break
        save_queue(queue)

def add_job(job: dict):
    with _lock:
        queue = load_queue()
        queue.append(job)
        save_queue(queue)

def delete_job(job_id: str):
    with _lock:
        queue = load_queue()
        queue = [j for j in queue if j.get("id") != job_id]
        save_queue(queue)

def get_pending_due_jobs() -> list:
    """Return jobs that are pending and whose scheduled_at <= now."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with _lock:
        queue = load_queue()
    return [
        j for j in queue
        if j.get("status") == "pending"
        and j.get("scheduled_at", "9999") <= now
    ]


# ══════════════════════════════════════════════════════
#  AUTO-SCHEDULE HELPER
# ══════════════════════════════════════════════════════

def auto_schedule(
    output_folder: str,
    platforms: list,
    videos_per_day: int,
    start_hour: int,
    title_template: str,
    description_template: str,
    subreddit: str = ""
) -> int:
    """
    Automatically assign unscheduled videos from output_folder to time slots.
    Returns the number of jobs created.
    """
    import os, uuid

    # Find MP4s not already in queue
    with _lock:
        queue = load_queue()
    queued_paths = {j.get("video_path") for j in queue}

    mp4s = sorted([
        str(Path(output_folder) / f)
        for f in os.listdir(output_folder)
        if f.lower().endswith(".mp4")
        and str(Path(output_folder) / f) not in queued_paths
    ])

    if not mp4s:
        return 0

    # Build time slots starting from tomorrow
    slots = []
    day   = datetime.datetime.now() + datetime.timedelta(days=1)
    day   = day.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    step  = datetime.timedelta(hours=max(1, 24 // videos_per_day))

    while len(slots) < len(mp4s) * len(platforms):
        for _ in range(videos_per_day):
            slots.append(day)
            day += step
        day = day.replace(hour=start_hour, minute=0) + datetime.timedelta(days=1)
        day = day.replace(hour=start_hour)

    created = 0
    slot_idx = 0
    for video_path in mp4s:
        for platform in platforms:
            if slot_idx >= len(slots):
                break
            scheduled_at = slots[slot_idx].strftime("%Y-%m-%d %H:%M")
            slot_idx += 1

            title = render_template(title_template, platform, video_path)
            desc  = render_template(description_template, platform, video_path)

            job = {
                "id":           str(uuid.uuid4())[:8],
                "video_path":   video_path,
                "platform":     platform,
                "title":        title,
                "description":  desc,
                "subreddit":    subreddit,
                "scheduled_at": scheduled_at,
                "status":       "pending",
                "created_at":   datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "result":       None
            }
            add_job(job)
            created += 1

    return created


# ══════════════════════════════════════════════════════
#  BACKGROUND WORKER
# ══════════════════════════════════════════════════════

def _worker_loop():
    """Main loop — checks for due jobs every 30 seconds."""
    _log("Scheduler started — checking queue every 30s", "info")

    while True:
        try:
            due_jobs = get_pending_due_jobs()
            for job in due_jobs:
                job_id   = job["id"]
                platform = job.get("platform", "?")
                video    = Path(job.get("video_path", "")).name

                _log(f"Scheduler: firing job {job_id} [{platform}] {video}", "info")
                update_job(job_id, status="running")

                try:
                    success = dispatch_upload(job, _log)
                    status  = "done" if success else "error"
                    result  = "Upload successful" if success else "Upload failed"
                except Exception as e:
                    status = "error"
                    result = str(e)
                    _log(f"Scheduler: job {job_id} crashed — {e}", "error")

                update_job(
                    job_id,
                    status=status,
                    result=result,
                    finished_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                )

        except Exception as e:
            _log(f"Scheduler loop error: {e}", "error")

        time.sleep(30)


def init_scheduler(queue_file: Path, log_fn):
    """Call once at app startup to start the background scheduler thread."""
    global QUEUE_FILE, _log
    QUEUE_FILE = queue_file
    _log       = log_fn

    if not queue_file.exists():
        with open(queue_file, "w") as f:
            json.dump([], f)

    t = threading.Thread(target=_worker_loop, daemon=True)
    t.start()