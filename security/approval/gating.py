"""Runtime gating decision: is a (source, tool) call gated, right now?

The baseline is each server's static exempt list (MCP_APPROVAL_EXEMPT, from
approval-exempt.txt) -- writes gated, vetted reads free. On top of that, the approval
sidecar holds per-(source, tool) OVERRIDES that the gatekeeper tool edits at runtime,
so a tool can be flipped gated<->free with no restart. The override wins.

Servers fetch the overrides from the sidecar with a short TTL cache, so a change takes
effect within a few seconds on the gate (the in-chat card follows on the next
tools/list, which the connector caches until refreshed).
"""

from __future__ import annotations

import contextlib
import time

import httpx

_TTL = 15.0
_cache: dict[str, tuple[float, dict[str, bool]]] = {}  # source -> (fetched_at, overrides)


async def fetch_overrides(source: str, approval_url: str, timeout: float = 5.0) -> dict[str, bool]:
    """Overrides for `source` from the sidecar, cached for _TTL seconds. On any error
    returns the last known value (or empty), so gating never fails open on a blip."""
    now = time.monotonic()
    hit = _cache.get(source)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    data: dict[str, bool] = hit[1] if hit else {}
    # A fetch blip must never change gating -> keep last-known (or empty) on any error.
    with contextlib.suppress(Exception):
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{approval_url.rstrip('/')}/gating", params={"source": source})
        data = {k: bool(v) for k, v in (resp.json().get("overrides") or {}).items()}
    _cache[source] = (now, data)
    return data


def is_gated(tool: str, baseline_exempt: set[str], overrides: dict[str, bool]) -> bool:
    """True if `tool` needs approval. A runtime override wins; else the baseline
    (gated unless the tool is on the exempt allowlist)."""
    if tool in overrides:
        return overrides[tool]
    return tool not in baseline_exempt
