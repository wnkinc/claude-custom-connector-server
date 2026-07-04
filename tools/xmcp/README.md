# xmcp

Our own thin FastMCP server exposing X (Twitter) search/lookup, modeled on the example at https://github.com/xdevplatform/xmcp.git but written directly against `fastmcp` (see `server.py`).

- **Surface**: X's live OpenAPI spec filtered by a code-enforced grant. Default = 8 curated read ops; `X_API_TOOL_ALLOWLIST=all` exposes every read; writes additionally need `X_API_ALLOW_WRITES=1` (and user-context OAuth to actually succeed — the app-only bearer can't act as an account). Plus `grok_x_search` (xAI Grok's own X search, cited summary).
- **Annotations**: every tool carries MCP `ToolAnnotations` — `readOnlyHint` drives Claude's read-only vs write/delete permission categories, and the title prefix names the backing API (`X API: …` vs `xAI Grok: …`).
- **Approval**: the read surface is auto-exempted from the out-of-band approval gate (derived from the filtered spec, like telegram's `approval-exempt.txt` but computed); exposed writes block on a human.
