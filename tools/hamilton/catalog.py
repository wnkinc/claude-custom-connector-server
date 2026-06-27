"""Catalog: thin wrapper over the Hamilton Driver.

Holds no pieces — it only answers questions *about* the pieces in library/.
Hamilton does the work: it reads the library modules, builds the DAG, and exposes
introspection (available variables + up/downstream lineage). We just shape the
output for the MCP surface.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from hamilton import driver

from library import momentum, volatility

# Add new library modules here as the catalog grows.
_MODULES = [momentum, volatility]


@lru_cache(maxsize=1)
def _driver() -> driver.Driver:
    return driver.Builder().with_modules(*_MODULES).build()


def _is_piece(var: Any) -> bool:
    """A 'piece' is a defined node (has tags), not a raw external input (close, high...)."""
    return bool(getattr(var, "tags", None))


def list_pieces(layer: str | None = None, family: str | None = None) -> list[dict]:
    """Every catalogued piece with its name, return type, and tags — optionally filtered."""
    out: list[dict] = []
    for var in _driver().list_available_variables():
        if not _is_piece(var):
            continue
        tags = dict(var.tags)
        if layer is not None and tags.get("layer") != layer:
            continue
        if family is not None and tags.get("family") != family:
            continue
        out.append({"name": var.name, "type": str(var.type), "tags": tags})
    return sorted(out, key=lambda d: d["name"])


def lineage(name: str) -> dict:
    """What a piece depends on (upstream) and what depends on it (downstream)."""
    dr = _driver()
    upstream = [v.name for v in dr.what_is_upstream_of(name) if v.name != name]
    downstream = [v.name for v in dr.what_is_downstream_of(name) if v.name != name]
    return {"name": name, "upstream": sorted(upstream), "downstream": sorted(downstream)}
