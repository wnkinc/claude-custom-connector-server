"""Strategies — the engine-agnostic decision layer (layer 4) over library signals.

A strategy is a Hamilton node like any other library piece, so the signal it rests on
is just a parameter: ``mean_reversion(mr_signal, ...)`` makes the dependency
``mean_reversion -> mr_signal -> rsi_14 -> close`` part of the DAG, so it shows up in
``library_lineage`` for free — the strategy->piece link is *derived*, never hand-declared.

A strategy outputs a **target-position series** (engine-agnostic): the desired exposure
per bar (1 = long, 0 = flat). Turning that into one engine's order primitives
(vectorbt's boolean entries/exits, etc.) is the engine's job and stays out of here, so
the decision is identical across engines.

Conventions for a strategy node:
  * Tag it ``layer="strategy"`` — that's what ``backtest_strategies`` lists and what
    keeps it out of the ``library_list`` (math) surface.
  * A signal dependency is a ``pd.Series`` parameter with no default; a tunable param is
    a scalar with a default. The two are told apart by "has a default".
  * Prefix tunable params per-strategy (``mr_*``) — Hamilton flattens every module into
    one input namespace, so a bare ``entry`` would be shared across strategies.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from hamilton.function_modifiers import tag


@tag(layer="strategy", family="mean_reversion")
def mean_reversion(mr_signal: pd.Series, mr_entry: float = 0.4, mr_exit: float = 0.0) -> pd.Series:
    """Long when the mean-reversion score is oversold (>= mr_entry); flat once it reverts (<= mr_exit).

    Hysteresis: go long when the score first crosses ``mr_entry`` and hold until it falls
    to ``mr_exit``, so the position persists between the two thresholds. Returns a target
    position (1.0 = long, 0.0 = flat) carried forward between threshold crossings.
    """
    raw = pd.Series(
        np.where(mr_signal >= mr_entry, 1.0, np.where(mr_signal <= mr_exit, 0.0, np.nan)),
        index=mr_signal.index,
    )
    return raw.ffill().fillna(0.0)
