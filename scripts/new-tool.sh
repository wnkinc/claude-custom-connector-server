#!/usr/bin/env bash
# Stamp out a new mcp-tools server from the shared template.
#
#   scripts/new-tool.sh <name> <port> [subdomain] [proxy_port]
#   scripts/new-tool.sh weather 8062 weather.secure-agentic-engineering.com
#
# Creates tools/<name>/ with a minimal FastMCP server pre-wired to the shared
# serve() helper (security/serve.py: OAuth + optional guardrail/approval), an
# env.example, a hardened SYSTEM systemd unit
# (egress-walled), and a per-tool egress allowlist stub. It does NOT touch
# Cloudflare, systemd, or /etc -- it prints the exact follow-up commands.
set -euo pipefail

NAME="${1:?usage: new-tool.sh <name> <port> [subdomain] [proxy_port]}"
PORT="${2:?usage: new-tool.sh <name> <port> [subdomain] [proxy_port]}"
SUBDOMAIN="${3:-${NAME}.secure-agentic-engineering.com}"
# Per-tool egress-proxy listener. Convention: MCP port (:806x) + 12 -> proxy (:807x).
PROXY_PORT="${4:-$((PORT + 12))}"
RUN_USER="$(id -un)"
ACL="${NAME//-/_}"   # squid acl names: hyphens -> underscores

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/tools/$NAME"
UNIT_NAME="mcp-$NAME"

[ -e "$DIR" ] && { echo "ERROR: $DIR already exists" >&2; exit 1; }
mkdir -p "$DIR/systemd"

cat > "$DIR/server.py" <<PY
import os
import sys
from pathlib import Path

from fastmcp import FastMCP

# Make the repo root importable regardless of CWD, then load the shared serve()
# helper (applies OAuth + optional guardrail/approval, then runs).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from security.serve import serve  # noqa: E402

mcp = FastMCP(name="${NAME}")


@mcp.tool
def ping() -> str:
    """Health check; replace with real tools."""
    return "pong"


def main() -> None:
    port = int(os.getenv("MCP_PORT", "${PORT}"))
    # Trusted internal tool by default. If this tool returns UNTRUSTED external
    # content, add: untrusted_output=True (guardrail) and/or require_approval=True.
    serve(mcp, port=port)


if __name__ == "__main__":
    main()
PY

cat > "$DIR/requirements.txt" <<'TXT'
fastmcp
TXT

cat > "$DIR/env.example" <<ENV
MCP_HOST=127.0.0.1
MCP_PORT=${PORT}

MCP_AUTH_ENABLED=0
MCP_PUBLIC_URL=https://${SUBDOMAIN}
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
MCP_ALLOWED_GOOGLE_EMAILS=
ENV

sed -e "s|__NAME__|${UNIT_NAME}|g" \
    -e "s|__PORT__|${PORT}|g" \
    -e "s|__DIR__|${DIR}|g" \
    -e "s|__PROXY_PORT__|${PROXY_PORT}|g" \
    -e "s|__USER__|${RUN_USER}|g" \
    "$ROOT/scripts/templates/unit.template" > "$DIR/systemd/${UNIT_NAME}.service"

# Per-tool egress allowlist stub (locked down by default -- add only what it needs).
ALLOW="$ROOT/security/egress-proxy/allowlist/${NAME}.txt"
mkdir -p "$(dirname "$ALLOW")"
cat > "$ALLOW" <<TXT
# ${NAME} egress allowlist (squid dstdomain). One host per line; leading dot = subdomains.
# Add ONLY hosts this tool must reach (plus the Google OAuth hosts if it serves Claude:
# accounts.google.com, oauth2.googleapis.com, www.googleapis.com). Discover misses from
# squid TCP_DENIED in /var/log/squid/access.log.
TXT

cat <<DONE
Created tools/${NAME}/ (MCP :${PORT}, proxy :${PROXY_PORT}, subdomain ${SUBDOMAIN}).

Next steps:
  1. python -m venv "$DIR/.venv" && "$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"
  2. cp "$DIR/env.example" "$DIR/.env"   # fill Google creds + email allowlist; set MCP_AUTH_ENABLED=1
  3. Add https://${SUBDOMAIN}/auth/callback to your Google OAuth client's Authorized redirect URIs.
  4. Egress: put this tool's allowed hosts in security/egress-proxy/allowlist/${NAME}.txt,
     then add to security/egress-proxy/squid.conf (the http_access line BEFORE 'deny all'):
         http_port 127.0.0.1:${PROXY_PORT} name=${NAME}
         acl port_${ACL} myportname ${NAME}
         acl dom_${ACL}  dstdomain "/etc/squid/allowlist/${NAME}.txt"
         http_access allow port_${ACL} CONNECT dom_${ACL}
  5. Install (one sudo -- system unit + allowlist + squid reload + enable at boot):
     sudo scripts/install-system.sh
  6. scripts/add-tunnel-route.sh ${SUBDOMAIN} ${PORT}
  7. Add the custom connector https://${SUBDOMAIN}/mcp in Claude (desktop + web).
DONE
