"""Catalog: thin wrapper over the Hamilton Driver.

Holds no pieces — it only answers questions *about* the pieces in library/.
Hamilton does the work: it reads the library modules, builds the DAG, and exposes
introspection (available variables + up/downstream lineage). We just shape the
output for the MCP surface.
"""
from __future__ import annotations

import inspect
from functools import lru_cache
from typing import Any

import pandas as pd
from hamilton import driver

from library import momentum, strategies as strategy_mod, volatility

# Add new library modules here as the catalog grows.
_MODULES = [momentum, volatility, strategy_mod]

# Canonical OHLCV column names the DAG accepts as external inputs (close, high, ...).
_OHLCV = ("open", "high", "low", "close", "volume")


@lru_cache(maxsize=1)
def _driver() -> driver.Driver:
    return driver.Builder().with_modules(*_MODULES).build()


def _is_piece(var: Any) -> bool:
    """A 'piece' is a defined node (has tags), not a raw external input (close, high...)."""
    return bool(getattr(var, "tags", None))


def list_pieces(layer: str | None = None, family: str | None = None) -> list[dict]:
    """Every catalogued library piece with its name, return type, and tags — optionally filtered.

    Strategy nodes share the DAG (so lineage crosses into them) but are a separate surface
    cataloged by ``strategies()``; they're left out here unless asked for by ``layer``.
    """
    out: list[dict] = []
    for var in _driver().list_available_variables():
        if not _is_piece(var):
            continue
        tags = dict(var.tags)
        if layer is None and tags.get("layer") == "strategy":
            continue
        if layer is not None and tags.get("layer") != layer:
            continue
        if family is not None and tags.get("family") != family:
            continue
        out.append({"name": var.name, "type": str(var.type), "tags": tags})
    return sorted(out, key=lambda d: d["name"])


def strategies() -> list[dict]:
    """Every strategy node: its tunable ``params`` (name → default), ``uses``, and one-line doc.

    ``params`` are the scalar arguments with defaults (a signal dependency is a Series
    parameter with no default, so the two are told apart by "has a default"). ``uses`` is
    the upstream library pieces the strategy rests on — read from the DAG via lineage, so
    it can never drift from the code. Tags drive what counts as a strategy; the function
    signature/doc supply params and prose.
    """
    dr = _driver()
    out: list[dict] = []
    for var in dr.list_available_variables():
        if not _is_piece(var) or dict(var.tags).get("layer") != "strategy":
            continue
        fn = getattr(strategy_mod, var.name, None)
        if fn is None:  # a strategy node with no matching function (shouldn't happen)
            continue
        params = {
            p.name: p.default
            for p in inspect.signature(fn).parameters.values()
            if p.default is not inspect.Parameter.empty
        }
        uses = sorted(
            v.name for v in dr.what_is_upstream_of(var.name)
            if _is_piece(v) and v.name != var.name
        )
        doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
        out.append({"name": var.name, "params": params, "uses": uses, "doc": doc})
    return sorted(out, key=lambda d: d["name"])


def lineage(name: str) -> dict:
    """What a piece depends on (upstream) and what depends on it (downstream)."""
    dr = _driver()
    upstream = [v.name for v in dr.what_is_upstream_of(name) if v.name != name]
    downstream = [v.name for v in dr.what_is_downstream_of(name) if v.name != name]
    return {"name": name, "upstream": sorted(upstream), "downstream": sorted(downstream)}


def materialize(
    outputs: list[str] | str,
    ohlcv: pd.DataFrame,
    params: dict | None = None,
) -> dict[str, pd.Series]:
    """Run the DAG to compute named pieces from an OHLCV frame — the library→engine bridge.

    Feeds the frame's OHLCV columns in as the DAG's external inputs (open/high/low/
    close/volume) and requests one or more piece names; Hamilton wires the dependency
    graph and returns ``{name: Series}``. Each Series shares the frame's index, so an
    engine can convert it to a position/entries/exits directly. ``params`` overrides any
    tunable strategy inputs (e.g. ``mr_entry``); inputs the requested subDAG doesn't need
    are ignored. Raising on an unknown name is Hamilton's job (it lists the available vars).
    """
    names = [outputs] if isinstance(outputs, str) else list(outputs)
    inputs = {c: ohlcv[c] for c in _OHLCV if c in ohlcv.columns}
    if params:
        inputs.update(params)
    return _driver().execute(names, inputs=inputs)
