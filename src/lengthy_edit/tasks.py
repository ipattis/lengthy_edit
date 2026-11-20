"""Tiny in-process background task registry (generation, renders, Seedance polling)."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="lengthy-edit")
_tasks: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _update(tid: str, **kw: Any) -> None:
    with _lock:
        _tasks[tid].update(kw, updated=time.time())


def submit(kind: str, project: str, fn: Callable[[Callable[[str, float], None]], Any]) -> dict:
    tid = uuid.uuid4().hex[:12]
    with _lock:
        _tasks[tid] = {
            "id": tid, "kind": kind, "project": project, "status": "running",
            "message": "Queued", "progress": 0.0, "error": None, "created": time.time(), "updated": time.time(),
        }

    def progress(msg: str, frac: float) -> None:
        _update(tid, message=msg, progress=round(frac, 3))

    def run() -> None:
        try:
            fn(progress)
            _update(tid, status="done", message="Done", progress=1.0)
        except Exception as e:  # surfaced to the UI
            traceback.print_exc()
            _update(tid, status="error", error=str(e), message="Failed")

    _pool.submit(run)
    return get(tid)


def get(tid: str) -> dict:
    with _lock:
        return dict(_tasks[tid])


def for_project(project: str) -> list[dict]:
    cutoff = time.time() - 3600
    with _lock:
        return [dict(t) for t in _tasks.values() if t["project"] == project and t["updated"] > cutoff]


def running(project: str, kind: str) -> bool:
    with _lock:
        return any(t["project"] == project and t["kind"] == kind and t["status"] == "running" for t in _tasks.values())
