# Setup runbook — x-mcp as the first Claude-connected tool

Goal: `https://xmcp.secure-agentic-engineering.com/mcp` reachable from Claude
desktop, web, and mobile, gated by "Sign in with Google" locked to your account.

Prereqs already in place on this box: Cloudflare Tunnel `cloudflared-openclaw`
(tunnel id in `~/.cloudflared/config.yml`), domain `secure-agentic-engineering.com`
on Cloudflare, systemd `--user` with linger, `uv`/Python.

---

## 1. Google OAuth client  (you — ~5–10 min)

In the [Google Cloud Console](https://console.cloud.google.com/):

1. **Create / pick a project** (e.g. `mcp-tools`).
2. **APIs & Services → OAuth consent screen:**
   - User type **External**.
   - App name (e.g. `SAE MCP Tools`), your support email, developer email.
   - Scopes: the defaults (`openid`, `.../auth/userinfo.email`) are enough — no
     sensitive scopes, so **no Google verification review** is required.
   - **Test users:** add every email that should have access (this is Google's
     native allowlist while the app stays in "Testing"). Leave publishing status
     as **Testing**.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID:**
   - Application type **Web application**.
   - **Authorized redirect URIs:** `https://xmcp.secure-agentic-engineering.com/auth/callback`
   - Create → copy **Client ID** and **Client secret**.

(One OAuth client covers all future tools; each new subdomain just adds another
redirect URI here.)

## 2. Fill secrets  (you)

```bash
cp tools/x-mcp/env.example tools/x-mcp/.env   # .env is gitignored
```
Edit `tools/x-mcp/.env`:
- `X_BEARER_TOKEN=` — your X app bearer (read-only).
- `X_API_TOOL_ALLOWLIST=` — e.g. `getUsersByUsername,searchPostsRecent,getUsersPosts`.
- `XAI_API_KEY=` — only if you want the `grok_x_search` tool.
- `MCP_AUTH_ENABLED=1`
- `GOOGLE_CLIENT_ID=` / `GOOGLE_CLIENT_SECRET=` — from step 1.
- `MCP_ALLOWED_GOOGLE_EMAILS=` — your Google email (comma-separated for more);
  must also be a "test user" on the consent screen while it's in Testing.

(`.venv` is already created and deps installed. To redo:
`uv venv tools/x-mcp/.venv && uv pip install --python tools/x-mcp/.venv/bin/python -r tools/x-mcp/requirements.txt`)

## 3. Install + start the service

```bash
ln -s "$PWD/tools/x-mcp/systemd/mcp-xmcp.service" ~/.config/systemd/user/mcp-xmcp.service
systemctl --user daemon-reload
systemctl --user enable --now mcp-xmcp
systemctl --user status mcp-xmcp --no-pager
loginctl enable-linger "$USER"   # reboot-proof (likely already on)
```
Local sanity check (loopback):
```bash
curl -s http://127.0.0.1:8061/.well-known/oauth-authorization-server | head -c 400; echo
```

## 4. Cloudflare route

```bash
scripts/add-tunnel-route.sh xmcp.secure-agentic-engineering.com 8061
```
This adds the ingress rule (above the 404 catch-all), creates the proxied DNS
record, and restarts the tunnel. **Do not** add a Cloudflare Access policy to
`xmcp.*` — confirm no wildcard Access app covers it (Zero Trust → Access →
Applications). If one does, add a bypass for this hostname.

## 5. Verify the public endpoint (the #410 check)

```bash
# OAuth server metadata present:
curl -s https://xmcp.secure-agentic-engineering.com/.well-known/oauth-authorization-server | python -m json.tool | head
# Protected-resource metadata present:
curl -s https://xmcp.secure-agentic-engineering.com/.well-known/oauth-protected-resource/mcp
# 401 MUST carry WWW-Authenticate with resource_metadata=... :
curl -sD - -o /dev/null -X POST https://xmcp.secure-agentic-engineering.com/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | grep -i www-authenticate
```
The last command must print a `WWW-Authenticate: Bearer ... resource_metadata=...`
line. (Confirmed working against the local app during the build.)

## 6. Add the custom connector in Claude

- **Desktop (macOS):** Settings → Connectors → Add custom connector →
  `https://xmcp.secure-agentic-engineering.com/mcp` → Connect → Google login.
- **claude.ai web:** Settings → Connectors → Add custom connector → same URL →
  Connect → Google login. **Mobile** inherits it from your account.

Then test from each surface, e.g. ask Claude to run `searchPostsRecent` or
`grok_x_search`.

---

## Troubleshooting

- **"Authorization with the MCP server failed" on web/mobile, before any login** —
  the `WWW-Authenticate` header is missing. Re-run step 5; ensure no Cloudflare
  Access policy sits in front (that reintroduces the #410 behaviour).
- **Google login succeeds but Claude is rejected** — your email isn't in
  `MCP_ALLOWED_GOOGLE_EMAILS`; check `journalctl --user -u mcp-xmcp` for
  "Rejected Google login".
- **"Access blocked: app not verified" / can't reach consent** — add the email as
  a **test user** on the OAuth consent screen (Testing mode allows only those).
- **Service won't start, storage errors** — OAuth state writes to
  `~/.local/state/mcp-xmcp` via `StateDirectory` + `FASTMCP_HOME`; confirm both
  are present in the unit.
- **Logs:** `journalctl --user -u mcp-xmcp -f`.
