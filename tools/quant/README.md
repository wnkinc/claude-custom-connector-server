# tools/quant

Two halves in one hardened tool: a **research library** — a catalog of
engine-agnostic indicators, features, and alpha/signals defined once as plain
Python functions — and the **backtest engines** that consume them. They share one
process/venv because they're tightly coupled (the engines import the library); the
data lake stays a separate tool (`tools/data`) because that boundary is just files
on disk.

[Hamilton](https://github.com/apache/hamilton) is the engine that powers the
library half (it reads the functions and wires them into a DAG).

## The model in one breath

You write small Python functions. Hamilton **reads** them, wires them into a DAG
by matching parameter names to function names, and lets you **query** that graph
(list / lineage / dedup). Hamilton stores nothing — your functions are the source
of truth; Hamilton is the thing that connects and inspects them.

```
tools/quant/     = the tool root (server + readers + engines + systemd unit)
  library/       = the pieces (your code, the only thing that grows)
  engines/       = backtest engines that consume the pieces (vectorbt first)
Hamilton         = reads the pieces, connects them, answers questions about them
server.py        = exposes the library + engine surfaces to Claude over MCP
```

> Note: `library/` holds **every** layer-1–3 piece — indicators, derived
> features, and alpha/signals alike. The *kind* of a piece is a tag (see below),
> not a separate folder.

## What lives here

The tool spans the whole stack, but with a hard internal seam. Layers 1–3 are the
**engine-agnostic library** — a "piece" is just a column in a time-indexed table.
Layers 4+ are the **engines** (`engines/`), where logic becomes engine-specific.

| Layer | Where | Why |
|---|---|---|
| 1 Data | references it | consumes the `tools/data` parquet lake |
| 2 Indicators / Features | `library/` | a function `bars -> column` (math from `pandas-ta`) |
| 3 Alpha / Signal | `library/` | a function `features -> score column` |
| 4 Strategy | `engines/<engine>/` | entry/exit logic, expressed in the engine's native form |
| 5 Portfolio / Sizing | `engines/<engine>/` | engine-specific |
| 6 Risk | `engines/<engine>/` | engine-specific |
| 7 Backtest | `engines/<engine>/` | the execution engine (vectorbt first) |
| 8 Execution / Live | `engines/<engine>/` | the execution engine |

The **signal column is the seam**: the library tops out there, and each engine
consumes it. The library never runs a strategy; strategies live per-engine so the
same signal can drive vectorbt, Nautilus, etc. without duplicating indicator math.

## Indicator vs feature vs alpha = a tag, not a folder

To Hamilton they are the same thing: a function that produces a column. The
*layer* is metadata you filter on, not a directory.

```python
from hamilton.function_modifiers import tag

@tag(layer="indicator", family="momentum")
def rsi_14(close):
    ...

@tag(layer="alpha", family="mean_reversion")
def mr_signal(rsi_14):          # depends on rsi_14 — Hamilton wires this automatically
    ...
```

So there is **no** `indicators/` + `features/` + `alphas/` split.

**Folders and files mean nothing to Hamilton.** It flattens every function across
every module into one global namespace (node names must be unique repo-wide), and
you query by **tag**, never by path. So splitting `library/` into `momentum.py`,
`volatility.py`, etc. is *purely a human editing convenience* — start with a
single file and split only when one gets unwieldy. Family and layer are tags, not
directories.

## "Do I already have this?" — three tiers of dedup

The reason this tool exists. Before adding a piece, it answers whether something
equivalent is already in the library:

| Tier | Catches | Backed by |
|---|---|---|
| **code-hash** | renamed copy-paste (identical source) | Hamilton `node.version` (`hash_source_code`) |
| **math-hash** | different code, same math | run on golden fixtures → hash output (Hamilton `hash_value`) |
| **embeddings** | "looks similar, review me" | bge-m3 (`:8008`) over name + tags + docstring |

Code-hash and the output-hashing primitive are Hamilton's; math-hash is a thin
helper (golden fixtures + round + hash) on top.



`catalog.py` / `fingerprint.py` / `search.py` hold **no pieces** — they only
answer questions *about* the piece functions in `library/`.

## MCP surface (planned)

- `library_list(layer?, family?, tag?)` — what exists, filtered
- `library_search(query)` — 3-tier: code-hash → math-hash → embeddings
- `library_check(code)` — "before I add this, do I already have it?"
- `library_lineage(name)` — upstream / downstream of a piece
- `feature_compute(names, dataset)` — run the DAG, write columns to the data lake


## Notes

- Math comes from **pandas-ta** (pure pip, batch/vectorized). Good for research +
  backtest-feed; live engines use their own incremental indicators, with the
  pandas-ta function as the reference spec.
- Reference source is vendored read-only at `vendor/hamilton/` in the
  `Documents/death_by_prayer` repo.
