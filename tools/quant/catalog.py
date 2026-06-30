"""Catalog: thin wrapper over the Hamilton Driver.

Holds no pieces — it only answers questions *about* the pieces in library/.
Hamilton does the work: it reads the library modules, builds the DAG, and exposes
introspection (available variables + up/downstream lineage). We just shape the
output for the MCP surface.
"""
from __future__ import annotations

import importlib
import inspect
import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from hamilton import driver

from library import momentum, strategies as strategy_mod, volatility

# Add new library modules here as the catalog grows.
_MODULES = [momentum, volatility, strategy_mod]

# Canonical OHLCV column names the DAG accepts as external inputs (close, high, ...).
_OHLCV = ("open", "high", "low", "close", "volume")

# Variant registry (data-only): {indicator: [param values]}. Read by the library modules
# at import; written here by add_variant. Same file the wrappers read.
_VARIANTS_PATH = Path(__file__).resolve().parent / "library" / "variants.json"

# Serialize registry mutation + module reload so a request never sees a half-rebuilt DAG.
_MUTATE_LOCK = threading.Lock()

# The mintable menu (first cut: RSI only). Each entry says which module owns the wrapper,
# which OHLCV inputs it needs, and the param schema add_variant validates against.
_MINTABLE: dict[str, dict] = {
    "rsi": {
        "family": "momentum",
        "module": momentum,
        "inputs": ["close"],
        "params": {"length": {"type": "int", "min": 2, "max": 5000, "default": 14}},
        "doc": "Relative Strength Index over `length` bars (pandas-ta).",
    },
}


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


# --------------------------------------------------------------------------------------
# Variant minting: data-only. A "variant" is a known indicator + params (e.g. rsi/34).
# Dedup is exact and free here — the system owns the canonical node name, so the same
# (indicator, params) always maps to the same node and minting is idempotent. No
# behavioral fingerprinting needed at this layer (that's reserved for authored code).
# --------------------------------------------------------------------------------------

def _read_registry() -> dict:
    if not _VARIANTS_PATH.exists():
        return {}
    return json.loads(_VARIANTS_PATH.read_text())


def _write_registry(reg: dict) -> None:
    _VARIANTS_PATH.write_text(json.dumps(reg, indent=2) + "\n")


def _canonical_name(indicator: str, params: dict) -> str:
    """The one true node name for (indicator, params). First cut: single `length` param."""
    return f"{indicator}_{params['length']}"


def _fixture(n: int) -> pd.DataFrame:
    """Deterministic OHLCV used to prove a freshly-minted node actually computes."""
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    spread = np.abs(rng.normal(0, 0.5, n))
    return pd.DataFrame({
        "open": close + rng.normal(0, 0.3, n),
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": rng.integers(100_000, 1_000_000, n).astype(float),
    })


def _validate_node(name: str, length: int) -> tuple[bool, str]:
    """Run the (reloaded) node on a fixture sized to the window; confirm a usable Series."""
    df = _fixture(max(300, length * 3))
    try:
        out = materialize(name, df)[name]
    except Exception as e:  # noqa: BLE001 — surface any DAG/runtime failure to the caller
        return False, f"node failed on fixture: {type(e).__name__}: {e}"
    if not isinstance(out, pd.Series):
        return False, f"expected a Series, got {type(out).__name__}"
    if out.dropna().empty:
        return False, "all-NaN on fixture (window too large for any sane data?)"
    return True, ""


def indicators_available() -> list[dict]:
    """The mintable indicator menu: what add_variant can create, plus what's already minted.

    Pick an ``indicator`` and fill its ``params`` (schema given here), then call
    ``add_variant``. ``minted`` lists the canonical node names that already exist for it.
    """
    reg = _read_registry()
    live = {v.name for v in _driver().list_available_variables()}
    out = []
    for ind, spec in _MINTABLE.items():
        names = sorted(f"{ind}_{n}" for n in reg.get(ind, []))
        out.append({
            "indicator": ind,
            "family": spec["family"],
            "inputs": spec["inputs"],
            "params": spec["params"],
            "doc": spec["doc"],
            "minted": [n for n in names if n in live],
        })
    return out


def add_variant(indicator: str, params: dict | None = None) -> dict:
    """Mint a variant of a known indicator (e.g. rsi/length=34) as a live Hamilton node.

    Idempotent: a given (indicator, params) maps to one canonical node name, so re-minting
    is a no-op. Validates the param schema, persists the row to the registry, hot-reloads
    the owning module, and proves the node computes on a fixture before returning. The node
    is usable by ``backtest`` / ``library_list`` in the same session — no server restart.
    """
    params = dict(params or {})
    spec = _MINTABLE.get(indicator)
    if spec is None:
        return {"ok": False, "error": f"unknown indicator '{indicator}'; see indicators_available()"}

    schema = spec["params"]["length"]
    length = params.get("length", schema["default"])
    if isinstance(length, bool) or not isinstance(length, int):
        return {"ok": False, "error": "param 'length' must be an integer"}
    if not (schema["min"] <= length <= schema["max"]):
        return {"ok": False, "error": f"length must be in [{schema['min']}, {schema['max']}]"}

    name = _canonical_name(indicator, {"length": length})

    with _MUTATE_LOCK:
        if name in {v.name for v in _driver().list_available_variables()}:
            return {"ok": True, "name": name, "created": False, "note": "already exists"}

        reg = _read_registry()
        lengths = reg.setdefault(indicator, [])
        lengths.append(length)
        reg[indicator] = sorted(set(lengths))
        _write_registry(reg)

        importlib.reload(spec["module"])
        _driver.cache_clear()

        ok, err = _validate_node(name, length)
        if not ok:  # roll the registry back and rebuild to the prior good state
            reg[indicator] = [n for n in reg[indicator] if n != length]
            _write_registry(reg)
            importlib.reload(spec["module"])
            _driver.cache_clear()
            return {"ok": False, "error": err}

    return {
        "ok": True, "name": name, "created": True,
        "family": spec["family"], "params": {"length": length},
    }
