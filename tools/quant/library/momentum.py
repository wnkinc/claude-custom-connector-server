"""Momentum-family pieces. Math from pandas-ta; kind/family declared via @tag.

Variant windows are *data*, not code: they live in ``variants.json`` and are read here
at import (and re-read on ``importlib.reload``) to generate one first-class Hamilton node
per window — ``rsi_14``, ``rsi_21``, ... Mint a new window with the ``add_variant`` tool;
it appends a row and reloads this module. Never hand-edit the math to add a window — the
single ``rsi`` implementation below is the only RSI code there is.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pandas_ta as ta
from hamilton.function_modifiers import parameterize_values, tag

_VARIANTS_PATH = Path(__file__).resolve().parent / "variants.json"


def _rsi_variants() -> dict:
    """Registry rows -> @parameterize_values' assigned_output map: {(node, doc): length}."""
    lengths = json.loads(_VARIANTS_PATH.read_text()).get("rsi", []) if _VARIANTS_PATH.exists() else []
    return {
        (f"rsi_{n}", f"Relative Strength Index, {n}-period"): n
        for n in sorted(set(lengths))
    }


@tag(layer="indicator", family="momentum")
@parameterize_values(parameter="length", assigned_output=_rsi_variants())
def rsi(close: pd.Series, length: int) -> pd.Series:
    """Relative Strength Index over `length` bars (pandas-ta)."""
    return ta.rsi(close, length=length)


@tag(layer="alpha", family="mean_reversion")
def mr_signal(rsi_14: pd.Series) -> pd.Series:
    """Mean-reversion score in [-1, 1]: long when RSI is low, short when high."""
    return (50.0 - rsi_14) / 50.0
