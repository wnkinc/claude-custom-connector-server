# data — market-data ingest into a canonical parquet lake

A self-hosted MCP server that ingests market data into a **canonical, engine-agnostic
parquet data lake** and exposes it to the Claude apps. Ported from the
`secure-agentic-engineering` data-ingest runner.

```
Claude app ──HTTPS──► Cloudflare Tunnel ──► this server (loopback :8062, OAuth-gated) ──► yfinance
                                                  │
                                                  ▼
                            <DATA_ROOT>/bars/yfinance/<SYMBOL>/<interval>.parquet
```

## What changed in the port

In `secure-agentic-engineering` the ingest was **two processes**: a thin httpx MCP
*bridge* fronting a separate FastAPI *runner service* on `:8021`. That split existed
because the bridge there was httpx-only and shared across every tool, so heavy deps
(pandas/pyarrow/yfinance) and blocking downloads had to live elsewhere.

In `mcp-tools` **every tool is already its own hardened, single-venv process**, so the
split collapses: the runner moves *into* this server as an in-process run registry
(`runs.py`). The deterministic pipeline (`pipeline.py`) and the canonical contract
(`schema.py`) port over unchanged. The async start/poll/cancel lifecycle is kept — a
full-history download can outlast a Claude tool-call timeout, so a slow run returns a
`PENDING` run_id the model polls instead of blocking.

## What it does

The agent makes **one** coarse call (`data-ingest`); a deterministic pipeline runs
behind it:

```
fetch (source) → normalize (raw → canonical names) → enforce_canonical (UTC, float, sort, dedupe) → store (parquet)
```

A request whose range is already stored returns a **cache hit** without re-downloading
(`refresh=true` forces a re-fetch). Coverage compares *requested* windows (recorded in
the parquet's own metadata), so market holidays/weekends and yfinance's exclusive `end`
don't defeat the cache.

## The store

Plain parquet, readable by any pandas/pyarrow consumer. Self-describing layout (the
path is the metadata):

```
<DATA_ROOT>/<kind>/<source>/<symbol>/<interval>.parquet
e.g.  var/data/bars/yfinance/AAPL/1d.parquet
```

Canonical bars schema: `timestamp` (UTC), `open`, `high`, `low`, `close`, `volume`.

## Code layout (separated by lifecycle stage)

| File | Role |
|---|---|
| `schema.py` | canonical contract + `enforce_canonical`; the `kinds` vocabulary |
| `sources/yfinance.py` | fetch raw bars from one origin (add `ccxt.py` etc. later) |
| `normalize.py` | raw (source-shaped) → canonical column names |
| `store.py` | the only reader/writer; paths, parquet I/O, request-window cache check |
| `pipeline.py` | the deterministic ingest act (fetch → normalize → enforce → store) |
| `runs.py` | in-process run registry + thread pool (was the standalone FastAPI runner) |
| `server.py` | FastMCP server: OAuth wiring + the four MCP tools |

## MCP tools

| Tool | Purpose |
|---|---|
| `data-ingest` | start an ingest → summary, or a `PENDING` run_id for a slow run |
| `data-ingest-poll` | retrieve a slow run's summary by run_id |
| `data-ingest-cancel` | best-effort cancel a run |
| `data-read` | read canonical bars back out of the lake (read-only) |

`data-ingest` args: `symbol`, `interval?="1d"` (1m/5m/15m/30m/1h/1d/1wk/1mo),
`start?`/`end?` (ISO `YYYY-MM-DD`; omit both = full history), `source?="yfinance"`,
`refresh?=false`.

## Setup & run

```bash
cd tools/data
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp env.example .env            # fill Google creds + email allowlist; set MCP_AUTH_ENABLED=1 for public
.venv/bin/python server.py     # serves on http://127.0.0.1:8062 (MCP_PORT)
.venv/bin/python -m pytest     # tests (yfinance mocked; tmp data lake)
```

To publish to the Claude apps, follow the standard mcp-tools wiring: hosts in
`security/egress-proxy/allowlist/data.txt`, the squid `http_port`/`acl`/`http_access`
lines for `:8074`, `sudo scripts/install-system.sh`, then
`scripts/add-tunnel-route.sh data.secure-agentic-engineering.com 8062`.

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `MCP_PORT` | `8062` | loopback MCP port |
| `MCP_AUTH_ENABLED` | `0` | `1` = require Google OAuth (public serving) |
| `DATA_ROOT` | `<tool>/var/data` | data-lake root |
| `DATA_MAX_WORKERS` | `4` | ingest thread-pool size |
| `DATA_INLINE_BUDGET_S` | `20` | seconds `data-ingest` blocks before returning a `PENDING` run_id |

## Egress

yfinance reaches Yahoo Finance over HTTPS; under the L2 egress wall those hosts must be
in `security/egress-proxy/allowlist/data.txt` (`.finance.yahoo.com`, `fc.yahoo.com`)
or downloads fail closed. Discover any misses from `TCP_DENIED` in
`/var/log/squid/access.log`.
