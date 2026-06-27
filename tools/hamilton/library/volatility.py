"""Volatility-family pieces. Math from pandas-ta; kind/family declared via @tag."""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta
from hamilton.function_modifiers import tag


@tag(layer="indicator", family="volatility")
def atr_14(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Average True Range, 14-period."""
    return ta.atr(high=high, low=low, close=close, length=14)
