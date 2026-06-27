"""Momentum-family pieces. Math from pandas-ta; kind/family declared via @tag."""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta
from hamilton.function_modifiers import tag


@tag(layer="indicator", family="momentum")
def rsi_14(close: pd.Series) -> pd.Series:
    """Relative Strength Index, 14-period."""
    return ta.rsi(close, length=14)


@tag(layer="alpha", family="mean_reversion")
def mr_signal(rsi_14: pd.Series) -> pd.Series:
    """Mean-reversion score in [-1, 1]: long when RSI is low, short when high."""
    return (50.0 - rsi_14) / 50.0
