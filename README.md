# mcp-tools

Self-hosted [MCP](https://modelcontextprotocol.io) servers exposed to the Claude
apps (macOS desktop, claude.ai web, mobile) from a home Linux box, via Cloudflare
Tunnel, with each tool gated by Google OAuth (verified-email allowlist).

## Layout

```
mcp-tools/
  shared/                # generic plumbing imported by every tool server
    auth.py              #   Google OAuth provider (email allowlist, fail-closed)
  security/              # threat-model security layers
    guardrail/
      middleware.py      #   L4 detect: FastMCP middleware that screens tool output
      service/           #   standalone LlamaFirewall scan service (loopback :8071)
  tools/
    x-mcp/               # first tool: X (Twitter) read-only search/lookup + Grok x_search
      server.py          #   vendored+patched FastMCP server (see VENDORED.md) + OAuth wiring
      systemd/mcp-xmcp.service
      env.example
    data/                # market-data ingest into a canonical parquet data lake (:8062)
      server.py          #   FastMCP server (OAuth) + in-process run registry
      pipeline.py        #   deterministic fetch → normalize → enforce → store
      systemd/mcp-data.service
  scripts/
    new-tool.sh          # stamp a new tool (dir + server stub + unit)
    add-tunnel-route.sh  # add Cloudflare ingress + DNS for a tool
    templates/
      unit.template      # hardened --user service template (used by new-tool.sh)
  docs/
    SETUP.md             # step-by-step runbook (start here)
    ARCHITECTURE.md      # how it fits together + why it's built this way
```

## The model in one breath

One **hardened process per tool**, bound to **loopback**, each on its **own
subdomain** routed by a single **Cloudflare Tunnel** (transport only — no Access
policy). **Auth lives in the MCP server** (FastMCP Google OAuth), not in
Cloudflare, because that is the only way the claude.ai **web/mobile** custom
connectors work (see [docs/SETUP.md](docs/SETUP.md) for the Cloudflare-Access
bug this avoids). Each tool is added to Claude as a **custom connector** (no
directory review).

## Quick start

See **[docs/SETUP.md](docs/SETUP.md)**. New tool later: `scripts/new-tool.sh`.
