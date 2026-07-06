# Ingress (Cloudflare tunnel)

The tunnel's **routing** lives in the `configs:` block of `docker-compose.tunnel.yml`
at the repo root -- compose interpolates your `MCP_DOMAIN` / `TUNNEL_ID` from the root
`.env` and mounts the rendered config into the cloudflared sidecar, so the committed
routing stays generic while nothing public runs that isn't in the repo.

This directory holds only the **secret half**: the tunnel's credentials JSON, staged at

    security/ingress/secrets/creds.json     # gitignored, never commit

Get it from `cloudflared tunnel create <name>` (it lands in
`~/.cloudflared/<TUNNEL_ID>.json`) and copy it here -- see docs/deploy/local.md
(the AWS path stages it from SSM instead -- see deploy/aws/).

Note: only one connector may run per tunnel. If a host `cloudflared` service already
serves this tunnel, stop it before bringing up the overlay -- two connectors with
different configs would split-route the same hostnames.
