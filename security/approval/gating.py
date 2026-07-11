"""Runtime gating decision: what mode is a (source, tool) in, right now?

Every tool is in one of three MODES:

  - "free":   runs with no approval.
  - "gated":  needs an out-of-band human approval per call.
  - "hidden": disabled by the operator -- refuses calls outright AND is filtered
              from tools/list (the model stops seeing it once the connector
              refreshes its cached list; the refusal is the actual gate).

The baseline is each server's static exempt list (MCP_APPROVAL_EXEMPT, from
approval-exempt.txt) -- writes gated, vetted reads free. On top of that, the approval
sidecar holds per-(source, tool) mode OVERRIDES that the gatekeeper tool edits at
runtime, so a tool can be flipped free/gated/hidden with no restart. The override wins.

Servers fetch the overrides from the sidecar with a short TTL cache, so a change takes
effect within a few seconds on the gate (the in-chat card and the visible tool list
follow on the next tools/list, which the connector caches until refreshed).
"""

from __future__ import annotations

import contextlib
import time

import httpx

MODES = ("free", "gated", "hidden")

_TTL = 15.0
_cache: dict[str, tuple[float, dict[str, str]]] = {}  # source -> (fetched_at, overrides)


def _as_mode(value) -> str:  # type: ignore[no-untyped-def]
    """Normalize a wire value to a mode. Older sidecars stored requires_approval
    bools; anything unrecognized becomes "gated" -- never fail OPEN on bad data."""
    if isinstance(value, str) and value in MODES:
        return value
    if isinstance(value, bool):
        return "gated" if value else "free"
    return "gated"


async def fetch_overrides(source: str, approval_url: str, timeout: float = 5.0) -> dict[str, str]:
    """Mode overrides for `source` from the sidecar, cached for _TTL seconds. On any
    error returns the last known value (or empty), so gating never fails open on a blip."""
    now = time.monotonic()
    hit = _cache.get(source)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    data: dict[str, str] = hit[1] if hit else {}
    # A fetch blip must never change gating -> keep last-known (or empty) on any error.
    with contextlib.suppress(Exception):
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{approval_url.rstrip('/')}/gating", params={"source": source})
        data = {k: _as_mode(v) for k, v in (resp.json().get("overrides") or {}).items()}
    _cache[source] = (now, data)
    return data


def mode_for(tool: str, baseline_exempt: set[str], overrides: dict[str, str]) -> str:
    """The mode `tool` is in. A runtime override wins; else the baseline (gated
    unless the tool is on the exempt allowlist)."""
    if tool in overrides:
        return overrides[tool]
    return "free" if tool in baseline_exempt else "gated"
