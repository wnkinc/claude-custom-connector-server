"""In-process run registry for the ingest pipeline.

In ``secure-agentic-engineering`` this lived in a *separate* FastAPI runner service
(``tools/data/service/service.py``) because the MCP bridge there was httpx-only and
shared across tools. In ``mcp-tools`` every tool is already its own hardened process
with its own venv, so the runner collapses into this module: the same start/poll/
cancel lifecycle and thread-pool, just called directly instead of over HTTP.

Why keep the async lifecycle at all? A full-history download can take tens of
seconds — longer than a Claude tool call wants to block. ``data-ingest`` runs the
work on a thread and blocks only up to ``INLINE_BUDGET_S``; a slower run returns a
``PENDING`` marker with a ``run_id`` the model polls with ``data-ingest-poll``.

Run statuses: ``running`` → ``success`` | ``error`` | ``interrupted``.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import pipeline

# Ingest is mostly I/O-bound (HTTP download); a small pool is plenty.
MAX_WORKERS = int(os.getenv("DATA_MAX_WORKERS", "4"))
# How long ``data-ingest`` blocks waiting for a fast run before handing back a
# PENDING marker. Kept well under typical MCP/tool-call timeouts.
INLINE_BUDGET_S = float(os.getenv("DATA_INLINE_BUDGET_S", "20"))
_POLL_INTERVAL_S = 0.25

# Run lifecycle states.
RUNNING = "running"
SUCCESS = "success"
ERROR = "error"
INTERRUPTED = "interrupted"
TERMINAL = frozenset({SUCCESS, ERROR, INTERRUPTED})


@dataclass
class Job:
    status: str = RUNNING
    result: dict | None = None
    error: str | None = None
    cancel_requested: bool = False
    future: Any = None


_jobs: dict[str, Job] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="data-run")


def _run_ingest(
    symbol: str, interval: str, start: str | None, end: str | None, source: str, refresh: bool
) -> dict:
    """Run one ingest to completion. Blocking. Thin seam for monkeypatching in tests."""
    return pipeline.ingest(symbol, interval, start, end, source, refresh)


def _worker(
    run_id: str, symbol: str, interval: str, start: str | None, end: str | None,
    source: str, refresh: bool,
) -> None:
    job = _jobs[run_id]
    if job.cancel_requested:
        job.status = INTERRUPTED
        return
    try:
        result = _run_ingest(symbol, interval, start, end, source, refresh)
    except Exception as exc:  # noqa: BLE001 — surface any failure as run error
        with _lock:
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = ERROR
        return
    with _lock:
        if job.cancel_requested:
            job.status = INTERRUPTED
        else:
            job.result = result
            job.status = SUCCESS


def start(
    symbol: str, interval: str, start: str | None, end: str | None, source: str, refresh: bool
) -> str:
    """Submit an ingest run; return its ``run_id``."""
    run_id = uuid.uuid4().hex
    job = Job()
    with _lock:
        _jobs[run_id] = job
    job.future = _executor.submit(
        _worker, run_id, symbol, interval, start, end, source, refresh
    )
    return run_id


def status(run_id: str) -> str | None:
    """Run status, or None if the ``run_id`` is unknown."""
    job = _jobs.get(run_id)
    return job.status if job else None


def result(run_id: str) -> Job | None:
    """The Job (status/result/error), or None if the ``run_id`` is unknown."""
    return _jobs.get(run_id)


def cancel(run_id: str) -> tuple[bool, str | None]:
    """Best-effort cancel.

    A run that has not started yet is hard-cancelled (→ interrupted); one already
    downloading is flagged to land in ``interrupted`` when it returns. Returns
    ``(hard_cancelled, status)`` — status is None for an unknown run_id.
    """
    job = _jobs.get(run_id)
    if job is None:
        return False, None
    if job.status in TERMINAL:
        return False, job.status
    job.cancel_requested = True
    hard = bool(job.future and job.future.cancel())
    if hard:
        job.status = INTERRUPTED
    return hard, job.status


def wait(run_id: str, budget_s: float = INLINE_BUDGET_S) -> str:
    """Block up to ``budget_s`` for the run to finish; return its status.

    Returns the terminal status if it lands in time, else ``running`` (PENDING).
    """
    deadline = time.monotonic() + budget_s
    while True:
        st = status(run_id)
        if st is None or st in TERMINAL:
            return st or ERROR
        if time.monotonic() >= deadline:
            return RUNNING
        time.sleep(_POLL_INTERVAL_S)
