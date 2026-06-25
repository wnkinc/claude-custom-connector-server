#!/usr/bin/env bash
# mcp-tools: one-time ROOT bootstrap for the system-unit + egress-proxy deployment.
#
#   sudo scripts/install-system.sh
#
# Idempotent. Does the root-only steps the agent can't (no passwordless sudo):
#   1. apt-install squid (the egress proxy)
#   2. install our squid.conf + per-tool allowlists into /etc/squid, validate, start
#   3. install the mcp-tool SYSTEM units into /etc/systemd/system, enable at boot
#   4. install the scoped passwordless-sudoers drop-in (so restarts need no password)
#   5. add `wes` to systemd-journal (read service logs without sudo)
#
# PRE-STEP (run as yourself, NOT sudo, so port 8061 is free for the system unit):
#   systemctl --user disable --now mcp-xmcp.service
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run with sudo (root)." >&2; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OWNER=wes
echo "repo: $REPO"

echo "== 1. install squid =="
if ! command -v squid >/dev/null 2>&1; then
  apt-get update -y && apt-get install -y squid
fi
squid -v | head -1

echo "== 2. squid config + allowlists =="
install -d -m 0755 /etc/squid/allowlist
[ -f /etc/squid/squid.conf ] && [ ! -f /etc/squid/squid.conf.orig ] && \
  cp /etc/squid/squid.conf /etc/squid/squid.conf.orig
install -m 0644 "$REPO/security/egress-proxy/squid.conf" /etc/squid/squid.conf
install -m 0644 "$REPO"/security/egress-proxy/allowlist/*.txt /etc/squid/allowlist/
squid -k parse                         # fail the script if the config is invalid
systemctl enable --now squid.service
systemctl reload squid.service || systemctl restart squid.service

echo "== 3. mcp-tool system units =="
for unit in "$REPO"/tools/*/systemd/mcp-*.service; do
  [ -e "$unit" ] || continue
  install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
  echo "  installed $(basename "$unit")"
done
systemctl daemon-reload

echo "== 4. passwordless-sudoers drop-in =="
install -m 0440 "$REPO/scripts/system/mcp-tools.sudoers" /etc/sudoers.d/mcp-tools
visudo -cf /etc/sudoers.d/mcp-tools    # validate; set -e aborts on bad syntax

echo "== 5. journal read access for $OWNER =="
usermod -aG systemd-journal "$OWNER" || true

echo "== 5b. migrate OAuth state (--user ~/.local/state -> system /var/lib) =="
# Preserve FastMCP's OAuth client registrations + tokens so the Claude connector
# does NOT have to re-authorize after the --user -> system move.
OLD_STATE="/home/$OWNER/.local/state/mcp-xmcp"
NEW_STATE="/var/lib/mcp-xmcp"
if [ -d "$OLD_STATE" ] && [ ! -e "$NEW_STATE" ]; then
  install -d -m 0700 -o "$OWNER" -g "$OWNER" "$NEW_STATE"
  cp -a "$OLD_STATE"/. "$NEW_STATE"/ 2>/dev/null || true
  # FastMCP's OAuth filetree store BAKES its absolute root path into *-info.json
  # metadata. Without rewriting it, the store rejects the new location with
  # PathSecurityError and client registration (DCR) fails. Repoint old -> new so
  # the Claude connector keeps working without re-auth.
  OLD_ESC="${OLD_STATE//./\\.}"
  grep -rl "$OLD_STATE" "$NEW_STATE" 2>/dev/null \
    | xargs -r sed -i "s#${OLD_ESC}#${NEW_STATE}#g"
  chown -R "$OWNER":"$OWNER" "$NEW_STATE"
  echo "  copied + repointed $OLD_STATE -> $NEW_STATE"
else
  echo "  (skip: no old state, or /var/lib/mcp-xmcp already exists)"
fi

echo "== 6. enable + start tool units (boot-persistent) =="
for unit in "$REPO"/tools/*/systemd/mcp-*.service; do
  [ -e "$unit" ] || continue
  name="$(basename "$unit")"
  if ss -tlnp 2>/dev/null | grep -q '127.0.0.1:8061' && [ "$name" = "mcp-xmcp.service" ]; then
    echo "  WARNING: :8061 still in use (the --user mcp-xmcp may still be running)."
    echo "           run as yourself:  systemctl --user disable --now mcp-xmcp.service"
    echo "           then:  sudo systemctl enable --now mcp-xmcp.service"
    continue
  fi
  systemctl enable --now "$name"
  echo "  enabled+started $name"
done

echo
echo "DONE. Verify:"
echo "  systemctl status squid mcp-xmcp --no-pager"
echo "  curl -s localhost:8071/healthz   # guardrail still up"
echo "  journalctl -u mcp-xmcp -n 30     # (re-login once for journal group to apply)"
