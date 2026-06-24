#!/usr/bin/env bash
# Stamp out a new mcp-tools server from the shared template.
#
#   scripts/new-tool.sh <name> <port> [subdomain]
#   scripts/new-tool.sh weather 8062 weather.secure-agentic-engineering.com
#
# Creates tools/<name>/ with a minimal FastMCP server pre-wired to the shared
# Google OAuth (shared/auth.py), an env.example, and a hardened systemd unit
# rendered from shared/systemd/unit.template. It does NOT touch Cloudflare or
# systemd -- it prints the exact follow-up commands.
set -euo pipefail

NAME="${1:?usage: new-tool.sh <name> <port> [subdomain]}"
PORT="${2:?usage: new-tool.sh <name> <port> [subdomain]}"
SUBDOMAIN="${3:-${NAME}.secure-agentic-engineering.com}"

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

# Make repo-root shared/ importable regardless of CWD, then load shared OAuth.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.auth import build_oauth_provider  # noqa: E402

mcp = FastMCP(name="${NAME}")


@mcp.tool
def ping() -> str:
    """Health check; replace with real tools."""
    return "pong"


def main() -> None:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "${PORT}"))
    auth = build_oauth_provider()
    if auth is not None:
        mcp.auth = auth
    mcp.run(transport="http", host=host, port=port)


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
    "$ROOT/shared/systemd/unit.template" > "$DIR/systemd/${UNIT_NAME}.service"

cat <<DONE
Created tools/${NAME}/ (port ${PORT}, subdomain ${SUBDOMAIN}).

Next steps:
  1. python -m venv "$DIR/.venv" && "$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"
  2. cp "$DIR/env.example" "$DIR/.env"   # fill Google creds + email allowlist; set MCP_AUTH_ENABLED=1
  3. Add https://${SUBDOMAIN}/auth/callback to your Google OAuth client's Authorized redirect URIs.
  4. ln -s "$DIR/systemd/${UNIT_NAME}.service" ~/.config/systemd/user/${UNIT_NAME}.service
     systemctl --user daemon-reload && systemctl --user enable --now ${UNIT_NAME}
  5. scripts/add-tunnel-route.sh ${SUBDOMAIN} ${PORT}
  6. Add the custom connector https://${SUBDOMAIN}/mcp in Claude (desktop + web).
DONE
