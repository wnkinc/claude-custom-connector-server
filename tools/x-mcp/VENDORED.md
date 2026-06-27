# Vendored: xmcp (official X API MCP server)

Read-only X (Twitter) search/lookup as MCP tools. This file documents **what
upstream is and how we patched it**; for how the tool is deployed and secured (the
pattern is shared by every tool), see [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

- **Upstream:** https://github.com/xdevplatform/xmcp (X's official dev platform)
- **Vendored commit:** `63d34362d88ed9f94d54ccd5ecd5bb4d12e11759`
- **Vendored on:** 2026-06-23
- **License:** see upstream `LICENSE`

## What upstream is

A FastMCP server that fetches X's OpenAPI spec (`api.x.com/2/openapi.json`) at
startup and exposes its operations as MCP tools via `FastMCP.from_openapi`. The full
spec is **165 operations: 97 read (GET) and 68 write/mutate** (post/delete tweets,
DMs, follow, block, like, …).

## Our patches (`server.py::create_mcp`, marked `# PATCHED`)

Everything beyond `from_openapi` is ours:

- **App-only Bearer auth.** Upstream forces an interactive browser OAuth1 flow on
  every startup (`webbrowser.open`), so it can't run headless. When
  `X_OAUTH_CONSUMER_KEY`/`SECRET` are absent we skip OAuth1 and sign with a static
  `Authorization: Bearer <X_BEARER_TOKEN>` — read-only, no browser, no act-as-account.
  The original OAuth1 path is preserved when consumer keys are set.
- **Code-enforced read-only.** `X_API_TOOL_ALLOWLIST` defaults to a hardcoded 8-op
  read set (`DEFAULT_READ_ALLOWLIST`) when blank — a missing/typo'd `.env` fails
  **closed to read-only**, not open to all 165 ops. A write-guard drops every non-GET
  op unless `X_API_ALLOW_WRITES=1`, so write tools never exist regardless of the
  allowlist.
- **`grok_x_search` tool.** Custom tool (plain `httpx` → xAI Responses API with the
  `x_search` tool): Grok searches X and returns a cited natural-language summary, vs
  the raw post objects from `searchPostsRecent`. Credential `XAI_API_KEY`; model lever
  `XAI_MODEL` (default `grok-4-1-fast`).
- **Out-of-band approval gate** (`ApprovalMiddleware`). Every tool call is gated: the
  first call to a (tool, args) combo returns an approval link instead of running, and
  only a human clicking Approve — on the `/approve/{token}` page or via the optional
  Slack interactive message — flips it to approved; re-calling with the same args then
  runs. (In-chat approval can't be used: claude.ai tool-approval is sticky and
  elicitation dialogs don't render for custom connectors.) Exempt tools via
  `XMCP_APPROVAL_EXEMPT`.
- **`outputSchema` strip.** FastMCP 3.x derives an `outputSchema` per OpenAPI tool;
  Claude's connector then drops the per-tool approval toggle
  (anthropics/claude-code#25081). We strip it at build time. Escape hatch
  `XMCP_KEEP_OUTPUT_SCHEMA=1`.
- **Shared platform wiring** (not xmcp-specific — see central docs):
  - `GuardrailMiddleware` screens every tool result before it reaches the model — see
    [`security/guardrail`](../../security/guardrail).
  - Composition + serving via `security.serve.serve` (applies OAuth, plus the
    approval gate and guardrail screening this tool opts into) — see
    [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

> **Gotcha:** `main()` reads `MCP_PORT` from the process env **before** `.env` is
> loaded, so it's set in the systemd unit's `Environment=`, not `.env`.

## Deps (L5, OSV-vetted 2026-06-23)

fastmcp 3.4.2, mcp 1.28.0, httpx 0.28.1, python-dotenv 1.2.2, requests-oauthlib
2.0.0 (transitive: oauthlib 3.3.1, starlette 1.3.1, uvicorn 0.49.0) — all clean.
All direct deps are pinned; re-vet (OSV + deps.dev) on any bump.

## Run

```bash
uv pip install --python .venv/bin/python -r requirements.txt
# secrets live in .env (gitignored, 600): X_BEARER_TOKEN at minimum
.venv/bin/python server.py    # serves MCP on http://127.0.0.1:8061/mcp
```
</content>
