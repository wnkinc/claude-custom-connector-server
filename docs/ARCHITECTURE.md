# Architecture

## Request path

```
Claude desktop / claude.ai web / mobile     (custom connector, private to your account)
        │  HTTPS
        ▼
xmcp.secure-agentic-engineering.com          Cloudflare edge — TLS, hides home IP, WAF
        │  Cloudflare Tunnel (cloudflared, outbound-only; NO Access policy on this host)
        ▼
127.0.0.1:8061                               x-mcp FastMCP server (this Linux box)
        │  FastMCP owns OAuth: "Sign in with Google", locked to an email allowlist
        ▼
api.x.com (read-only bearer, allowlisted ops) + api.x.ai (grok_x_search)
```

## Why each choice

- **Auth in the MCP server, not Cloudflare Access.** claude.ai web/mobile
  connectors require a spec-compliant OAuth 2.1 flow whose `401` carries a
  `WWW-Authenticate: Bearer resource_metadata="..."` header (RFC 9728 / MCP auth
  spec). Cloudflare Access's Managed-OAuth MCP portal omits that header
  ([anthropics/claude-ai-mcp#410](https://github.com/anthropics/claude-ai-mcp/issues/410),
  closed "not planned"), so web/mobile fail there while Claude Code tolerates it.
  FastMCP's `OAuthProxy` emits the header + discovery metadata + DCR, so all
  surfaces work. Verified locally (see docs/SETUP.md "Verify").

- **Tunnel = transport only.** The Cloudflare Tunnel provides TLS, hides the home
  IP, and exposes no inbound ports. The MCP hostname has **no Access policy** —
  stacking Access OAuth on top of MCP OAuth double-auths and breaks the connector.

- **One hardened process per tool, loopback-bound, own subdomain.** Matches the
  isolation posture from `secure-agentic-engineering` (per-service systemd
  hardening, code-scoped egress, tool allowlists). A bug or bad dep in one tool
  can't reach another's credentials. The obvious "one endpoint, all tools"
  alternative is Cloudflare's MCP Portal — which is the thing broken by #410.

- **Google OAuth with a verified-email allowlist, fail-closed.** `GoogleProvider`
  authenticates *any* Google account; `shared/auth.py` (`GoogleAllowlistProvider`)
  wraps its token verifier to reject any login whose verified email is not in
  `MCP_ALLOWED_GOOGLE_EMAILS`, and refuses to start if auth is enabled without an
  allowlist/credentials. Bonus native gate: while the Google consent screen is in
  "Testing" status, only added test-user emails can complete the upstream login.

## Two instances of x-mcp (intentional)

| Instance | Location | Port | Auth | Consumer |
|---|---|---|---|---|
| loopback | `secure-agentic-engineering/tools/x-mcp` | `:8051` | none | DeerFlow (local) |
| **public** | `mcp-tools/tools/x-mcp` (this repo) | `:8061` | Google OAuth | Claude desktop/web/mobile |

Same code, different env. The DeerFlow instance is left untouched. `server.py`'s
`MCP_AUTH_ENABLED` flag toggles between the two modes, so the two deployments can
be **consolidated later** (one code home, two units) without a rewrite.

## Adding a tool

`scripts/new-tool.sh <name> <port> [subdomain]` stamps `tools/<name>/` (server
stub pre-wired to `shared/auth.py`, env.example, hardened unit). Then enable the
unit, `scripts/add-tunnel-route.sh`, add one redirect URI to the shared Google
OAuth client, and add the custom connector in Claude.
