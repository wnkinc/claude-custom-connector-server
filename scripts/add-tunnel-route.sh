#!/usr/bin/env bash
# Add a Cloudflare Tunnel ingress route + DNS for an mcp-tools server.
#
#   scripts/add-tunnel-route.sh <hostname> <local-port>
#   scripts/add-tunnel-route.sh xmcp.secure-agentic-engineering.com 8061
#
# Inserts an ingress rule into ~/.cloudflared/config.yml (above the catch-all
# 404), creates the proxied DNS record, and restarts the tunnel service.
# IMPORTANT: do NOT attach a Cloudflare Access policy to this hostname -- the MCP
# server does its own OAuth; stacking Access OAuth on top breaks the connector.
set -euo pipefail

HOSTNAME="${1:?usage: add-tunnel-route.sh <hostname> <local-port>}"
PORT="${2:?usage: add-tunnel-route.sh <hostname> <local-port>}"
CONFIG="${CLOUDFLARED_CONFIG:-$HOME/.cloudflared/config.yml}"
SERVICE="${CLOUDFLARED_SERVICE:-cloudflared-openclaw.service}"

[ -f "$CONFIG" ] || { echo "ERROR: $CONFIG not found" >&2; exit 1; }

TUNNEL="$(awk '/^tunnel:/{print $2; exit}' "$CONFIG")"
[ -n "$TUNNEL" ] || { echo "ERROR: could not read tunnel id from $CONFIG" >&2; exit 1; }

if grep -qE "hostname:[[:space:]]*${HOSTNAME//./\\.}([[:space:]]|$)" "$CONFIG"; then
  echo "Ingress for ${HOSTNAME} already present in ${CONFIG}; leaving it."
else
  BACKUP="${CONFIG}.bak.$(date +%s)"
  cp "$CONFIG" "$BACKUP"
  TMP="$(mktemp)"
  # Insert the new rule immediately before the catch-all 404 service line.
  awk -v host="$HOSTNAME" -v port="$PORT" '
    /^[[:space:]]*-[[:space:]]*service:[[:space:]]*http_status:404/ && !done {
      print "  # mcp-tools: " host " (MCP server does its own OAuth; no Access policy here)";
      print "  - hostname: " host;
      print "    service: http://localhost:" port;
      print "";
      done=1
    }
    { print }
  ' "$BACKUP" > "$TMP"
  if ! grep -qE "hostname:[[:space:]]*${HOSTNAME//./\\.}([[:space:]]|$)" "$TMP"; then
    echo "ERROR: could not find the 'http_status:404' catch-all to anchor the insert." >&2
    echo "       Add the ingress rule manually; original left untouched ($BACKUP)." >&2
    rm -f "$TMP"; exit 1
  fi
  mv "$TMP" "$CONFIG"
  echo "Added ingress rule for ${HOSTNAME} -> http://localhost:${PORT} (backup: $BACKUP)"
fi

echo "Creating DNS route (tunnel ${TUNNEL})..."
cloudflared tunnel route dns "$TUNNEL" "$HOSTNAME" || \
  echo "  (route may already exist -- continuing)"

echo "Restarting ${SERVICE}..."
systemctl --user restart "$SERVICE" 2>/dev/null || sudo systemctl restart "$SERVICE"

echo "Done. Verify: curl -sI https://${HOSTNAME}/mcp"
