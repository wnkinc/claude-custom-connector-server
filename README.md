# mcp-tools

Self-hosted [MCP](https://modelcontextprotocol.io) servers exposed to the Claude
apps (macOS desktop, claude.ai web, mobile) from a home Linux box, via Cloudflare
Tunnel, with each tool gated by Google OAuth (verified-email allowlist).

## The model in one breath

One **portable tool per container**: a FastMCP server that reads its transport and
security posture from **env**, so one image runs locally and in the cloud unchanged.
Each tool sits on an **internal Docker network sealed from the internet** — all egress
goes through a **squid allowlist sidecar**, so a bad dep stays confined to its allowlist.
A **Cloudflare Tunnel** sidecar fronts them, each on its own subdomain (transport only;
the server owns auth). **Auth lives in the MCP server** (FastMCP Google OAuth with a
verified-email allowlist), so it travels with the image and works uniformly across Claude
desktop, web, and mobile.

## Quick start

```
docker compose up --build                                               # local (auth off)

cp env.example .env   # public only: set MCP_DOMAIN + TUNNEL_ID (see docs/SETUP.md)
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d  # public (auth on)
```

New tool: `scripts/new-tool.sh`. How it fits together:
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
