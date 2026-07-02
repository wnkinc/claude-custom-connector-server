# data — the lean tool's data feeder (crypto: Tiingo via OpenBB → parquet lake → Lean)

Self-hosted MCP server (`:8062`). Fetches crypto OHLCV **bars** from **Tiingo** via
[OpenBB](https://openbb.co), persists them to a plain parquet lake (de-duplicated on
timestamp so repeated ingests accumulate history), and **exports them into the Lean
engine's data folder** — the shared `lean-data` volume the lean tool backtests against.
That export is this tool's purpose; the lake is its staging store.

Crypto-only on purpose: crypto has no corporate actions, so the lake's bars are valid
Lean inputs as-is. Equities need factor/map files (splits/dividends) — that fork
(QC Security Master vs adjusted-price shortcut) is deferred, and the equity/FX feeds
that used to live here were removed with the lean refit (recover from git history).

## MCP tools

| Tool | Purpose |
|---|---|
| `crypto-ingest` | fetch crypto OHLCV bars (Tiingo) → merge into the lake |
| `data-catalog` | list what's stored — the lake inventory (read-only) |
| `data-read` | read one stored series back out of the lake (read-only) |
| `lean-export` | write a stored series into the Lean data folder (the bridge) |

The agent's pipeline: `crypto-ingest` → `lean-export` → backtest (lean tool). What the
engine can actually backtest is reported by the **lean** tool's `available_data`, not
`data-catalog` (lake ≠ exported).

**`crypto-ingest` args:** `symbol` (Tiingo-style, hyphen-less: `BTCUSD`), `interval?="1d"`,
`start?`/`end?` (ISO `YYYY-MM-DD`), `refresh?=false` (replace instead of merge). Deep
**intraday** pulls page Tiingo's per-request cap automatically; on the free tier a deep
pull can return a **PARTIAL** result — re-run later (keep `refresh=false`) and the merge
extends coverage. **`lean-export` args:** `symbol`, `interval?="1d"` (`1d`→daily,
`1h`→hour, `1m`→minute), `source?="tiingo"`, `market?="coinbase"` (the Lean market the
files register under; the backtest must subscribe with the same one).

## Design

Four layers, so the owned surface stays constant as capabilities grow:

| File | Job |
|---|---|
| `server.py` | thin `@mcp.tool` per capability (wires feed → lake → text) |
| `feeds.py` | thin OpenBB fetch fns; pages Tiingo's intraday cap |
| `lake.py` | generic parquet persist/merge/read, keyed by path segments |
| `lean_export.py` | lake → Lean on-disk format (atomic, world-readable zips) |

The export format is **Lean's contract, not ours** — verified byte-identical against the
samples bundled in the engine image (see `test_data.py`'s golden tests). Writes are
atomic (tmp + rename) so the engine never reads a half-written zip, and `0644` because
the lean container reads as a different uid (the shared `leandata` group, gid 1500,
handles the directories).

## The stores

```
<DATA_ROOT>/<asset>/<source>/<symbol>/<interval>.parquet        # the lake (staging)
<LEAN_DATA_ROOT>/crypto/<market>/<res>/<symbol>_trade.zip       # the Lean export (consumed)
```

`DATA_ROOT` is this tool's state volume; `LEAN_DATA_ROOT` is the shared `lean-data`
volume (the lean container mounts it as its `data-folder`, seeded with only the engine's
two metadata databases — zero bundled price data).

## Setup & run

```bash
cd tools/data
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -c "import openbb"   # PREBUILD the obb accessor into the venv (one-time)
cp env.example .env                   # set TIINGO_API_KEY (+ Google creds for public serving)
.venv/bin/python server.py            # serves on http://127.0.0.1:8062
.venv/bin/python -m pytest            # tests (no network)
```

OpenBB code-gens its `obb` accessor at import; the image prebuilds it at build time and
freezes it (`OPENBB_AUTO_BUILD=false`) so it never rebuilds on the read-only rootfs.
Rerun the prebuild only after adding/removing an extension.

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `TIINGO_API_KEY` | _(empty)_ | **required** — the ingest provider; empty → ingest fails |
| `MCP_PORT` | `8062` | MCP port |
| `MCP_AUTH_ENABLED` | `0` | `1` = require Google OAuth (public serving) |
| `DATA_ROOT` | `/app/state/data` | parquet lake root (writable state volume) |
| `LEAN_DATA_ROOT` | `/lean-data` | Lean export target (the shared volume) |
| `OPENBB_AUTO_BUILD` | `false` | freeze the prebuilt accessor; never rebuild at import |
| `HOME` | `/app/state` | OpenBB writes `$HOME/.openbb_platform`; must be writable |

## Egress

Behind the egress wall, allowed hosts live in `security/egress-proxy/allowlist/data.txt`:
`api.tiingo.com` + the Google OAuth hosts (token/JWKS when `MCP_AUTH_ENABLED=1`).
Nothing else — the export writes to a local volume. Find misses via `TCP_DENIED` in the
egress log.
