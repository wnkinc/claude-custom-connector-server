# Architecture

## Request path (the deployed container stack)

```
Claude desktop / claude.ai web / mobile     (custom connector, private to your account)
        │  HTTPS
        ▼
xmcp.example.com (your MCP_DOMAIN)           Cloudflare edge — TLS, hides home IP, WAF
        │  Cloudflare Tunnel (cloudflared sidecar, outbound-only transport)
        ▼
xmcp container :8061                          FastMCP server on an internal Docker network
        │  FastMCP owns OAuth: "Sign in with Google", locked to an email allowlist
        │  sealed from the internet; output screened by the guardrail sidecar (:8071)
        ▼
egress sidecar (squid) :3128                  per-tool domain allowlist, default-deny, audit log
        ▼
api.x.com (read-only bearer, allowlisted ops) + api.x.ai (grok_x_search) + Google OAuth verify
```

The same image runs locally (`docker compose up`) and in the cloud — transport
(`http`/`stdio`) and the security posture (auth / approval / guardrail) are read from
**env** at startup, so one image serves every environment.

## Why each choice

- **Auth in the MCP server.** Each server runs its own Google OAuth (`FastMCP`
  `OAuthProxy`) with a verified-email allowlist. Keeping auth in the server rather than
  at the edge means it travels with the image — the same container authenticates the
  same way locally or in any cloud — and it works uniformly across Claude desktop, web,
  and mobile.

- **Tunnel = transport only.** The Cloudflare Tunnel provides TLS, hides the home IP,
  and dials outbound only, so the box exposes zero inbound ports. The MCP hostname serves
  its own OAuth; an Access layer on top would just double-auth.

- **One tool per container, own subdomain, isolated.** Each tool is its own image on an
  `internal` network, so each tool's credentials and egress stay isolated — a bug or bad
  dep in one stays contained to that tool.

- **Tools are opt-in (compose profiles).** Every tool service carries a profile named
  after itself; `COMPOSE_PROFILES` in the root `.env` picks which ones a deployment
  builds and runs. The egress wall and guardrail carry no profile — they're the shared
  substrate and always run. This is what keeps 3 tools or 100 tools the same repo: a
  deployer never pulls the image of a tool they didn't ask for.

- **Tools never call each other; cooperation is an artifact plane.** When two tools need
  to cooperate (data produces the lake, lean backtests it), they share a named volume
  carrying artifacts in a documented format — exactly one writer, and the format is the
  contract (here Lean's own on-disk data format, not something we invented). The
  dependency stays soft: lean without data simply reports no data, data without lean
  just exports to a volume nobody reads. No tool ever holds another tool's credentials
  or network access.

- **All internet access flows through the egress allowlist (the strongest single
  control).** Each tool sits on an `internal` Docker network whose only route off-box is
  the squid sidecar; squid enforces a per-tool domain allowlist (default-deny) and is the
  central egress audit log. Verified: allowlisted hosts succeed through the proxy, others
  get `TCP_DENIED/403`, and a proxy-bypass attempt is dropped.

- **Google OAuth with a verified-email allowlist, fail-closed.** `GoogleProvider`
  authenticates *any* Google account; `security/auth.py` wraps its token verifier to
  accept only logins whose verified email is in `MCP_ALLOWED_GOOGLE_EMAILS`, and requires
  an allowlist + credentials before it will start. While the Google consent screen is in
  "Testing", only added test-user emails can complete the login.

## Adding a tool

`scripts/new-tool.sh <name> <port>` stamps `tools/<name>/` (server stub wired to
`security/serve.py`, `env.example`) + its egress allowlist. Then add a `Dockerfile`
(copy an existing tool's) + a hashed `requirements.lock`, a service in
`docker-compose.yml` (with its opt-in `profiles:` entry), a route in the cloudflared
`configs:` block of `docker-compose.tunnel.yml`, the tool's name in `COMPOSE_PROFILES`
(root `.env` + `env.example`'s list), one redirect URI on the shared Google OAuth
client, and the custom connector in Claude.
