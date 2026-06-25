# Egress proxy (THREAT-MODEL L2 — kill the exfiltration leg)

A loopback **allowlist proxy** (squid) that every mcp-tool is forced through, so a
tool's process can only reach its expected hosts. This is the threat model's
*strongest single control*: even a hostile/compromised dependency **cannot
exfiltrate** if it can only talk where it's supposed to.

## How it's enforced (two layers, both mature)

1. **squid** does domain allowlisting at the `CONNECT` level (no TLS interception),
   **default-deny**, **per-tool** (each tool gets its own loopback port mapped to its
   own allowlist). Its `access.log` is the central egress audit trail.
2. **systemd** makes the proxy the *only* route off-box: each tool unit sets
   `HTTPS_PROXY` at the proxy **and** `IPAddressDeny=any` / `IPAddressAllow=127.0.0.1/8`.
   A dep that ignores the proxy env and dials out directly hits the kernel drop.
   (This is why the tools are **system** units — `IPAddress*` silently no-ops in
   `--user` units on this box.)

```
x-mcp (IPAddressAllow=loopback) ──HTTPS_PROXY──▶ squid :8073 ──allowlist──▶ api.x.com / api.x.ai / Google OAuth
        every other destination ──▶ kernel DROP                 (anything else ──▶ 403 + logged)
```

## Files (installed by `scripts/install-system.sh`)
- `squid.conf` → `/etc/squid/squid.conf` — per-tool listeners, default-deny.
- `allowlist/<tool>.txt` → `/etc/squid/allowlist/` — that tool's allowed domains.

## Adding a tool
Add one `http_port 127.0.0.1:<port> name=<tool>` + an `acl`/`http_access` pair in
`squid.conf`, drop an `allowlist/<tool>.txt`, point the tool unit's `HTTPS_PROXY` at
its port, `sudo systemctl reload squid`.

## Verify (the "test that it blocks" gate)
```bash
# NEGATIVE — exfil is actually blocked:
sudo systemctl show mcp-xmcp -p IPAddressDeny      # = any
curl -s -x http://127.0.0.1:8073 https://example.com   # -> 403 (not allowlisted)
# POSITIVE — real hosts still work end to end:
#   X search, Grok, and Google login succeed through the proxy.
# Watch denials while validating:
sudo tail -f /var/log/squid/access.log | grep TCP_DENIED
```

## Scope / limits
- CONNECT-domain level only — not payload inspection.
- v1 allowlist per tool is the union of that tool's needs (tighten later via squid
  proxy-auth if a tool needs intra-tool separation).
- The guardrail service (`:8071`) is loopback-only today; route it through here when
  its HuggingFace model pull needs egress.
