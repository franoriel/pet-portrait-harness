"""
Redis-backed job queue for async portrait generation.

Jobs go through these states:
    queued → processing → complete | failed

The queue is a Redis list (FIFO). Job metadata is stored in Redis hashes
with a 24-hour TTL so completed jobs auto-expire.

If REDIS_URL is not set, falls back to an in-memory dict + deque
so local development works without Redis.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import deque
from typing import Optional

log = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "")
QUEUE_KEY = "pp:job_queue"
JOB_PREFIX = "pp:job:"
JOB_TTL = 86400  # 24 hours

# ── Redis or in-memory backend ────────────────────────────────────────────────

_redis = None
_memory_store: dict[str, dict] = {}
_memory_queue: deque[str] = deque()


def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    if not REDIS_URL:
        return None
    try:
        import redis
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
        _redis.ping()
        log.info("Redis connected: %s", REDIS_URL[:30] + "...")
        return _redis
    except Exception as exc:
        log.warning("Redis unavailable, using in-memory fallback: %s", exc)
        return None


# ── Job operations ────────────────────────────────────────────────────────────

def create_job(
    pet_name: str,
    style: str,
    upload_path: str,
    terms_accepted_at: str = "",
    client_ip: str = "",
    background_mode: str = "auto",
) -> dict:
    """Create a job, enqueue it, and return the job dict."""
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    job = {
        "job_id": job_id,
        "status": "queued",
        "pet_name": pet_name,
        "style": style,
        "background_mode": background_mode or "auto",
        "upload_path": upload_path,
        "created_at": now,
        "updated_at": now,
        "position": 0,  # filled on read
        "raw": "",
        "composited": "",
        "download": "",
        "filename": "",
        "cdn": False,
        "error": "",
        # Photo-licence audit trail — when the customer ticked the checkbox
        # and from which IP. Persisted with the job so it survives into
        # Printful fulfillment logs.
        "terms_accepted_at": terms_accepted_at or "",
        "accept_ip": client_ip or "",
    }

    r = _get_redis()
    if r:
        r.hset(f"{JOB_PREFIX}{job_id}", mapping={k: _serialize(v) for k, v in job.items()})
        r.expire(f"{JOB_PREFIX}{job_id}", JOB_TTL)
        r.rpush(QUEUE_KEY, job_id)
    else:
        _memory_store[job_id] = dict(job)
        _memory_queue.append(job_id)

    log.info("Job %s created (style=%s, pet=%s)", job_id, style, pet_name)
    return job


def get_job(job_id: str) -> Optional[dict]:
    """Fetch job status. Returns None if not found."""
    r = _get_redis()
    if r:
        raw = r.hgetall(f"{JOB_PREFIX}{job_id}")
        if not raw:
            return None
        job = {k: _deserialize(k, v) for k, v in raw.items()}
        # Calculate queue position for queued jobs
        if job.get("status") == "queued":
            try:
                queue = r.lrange(QUEUE_KEY, 0, -1)
                job["position"] = queue.index(job_id) + 1 if job_id in queue else 0
            except (ValueError, Exception):
                job["position"] = 0
        return job
    else:
        job = _memory_store.get(job_id)
        if not job:
            return None
        job = dict(job)
        if job["status"] == "queued":
            try:
                queue_list = list(_memory_queue)
                job["position"] = queue_list.index(job_id) + 1
            except ValueError:
                job["position"] = 0
        return job


def dequeue_job() -> Optional[dict]:
    """Pop the next job from the queue. Returns None if empty."""
    r = _get_redis()
    if r:
        job_id = r.lpop(QUEUE_KEY)
        if not job_id:
            return None
        job = get_job(job_id)
        if job:
            update_job(job_id, status="processing")
        return job
    else:
        if not _memory_queue:
            return None
        job_id = _memory_queue.popleft()
        job = _memory_store.get(job_id)
        if job:
            job["status"] = "processing"
            job["updated_at"] = time.time()
        return dict(job) if job else None


def update_job(job_id: str, **fields):
    """Update specific fields on a job."""
    fields["updated_at"] = time.time()
    r = _get_redis()
    if r:
        mapping = {k: _serialize(v) for k, v in fields.items()}
        r.hset(f"{JOB_PREFIX}{job_id}", mapping=mapping)
    else:
        if job_id in _memory_store:
            _memory_store[job_id].update(fields)


def queue_depth() -> int:
    """Number of jobs waiting in the queue."""
    r = _get_redis()
    if r:
        return r.llen(QUEUE_KEY)
    return len(_memory_queue)


# ── Serialization helpers (Redis stores strings) ──────────────────────────────

def _serialize(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _deserialize(key: str, v: str):
    if key in ("created_at", "updated_at", "position"):
        try:
            return float(v)
        except ValueError:
            return 0
    if key == "cdn":
        return v == "1"
    return v
