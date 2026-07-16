#!/usr/bin/env bash
# Browser tool runtime: a virtual display for headed Chromium, an optional
# noVNC live view onto it, then the MCP server (which spawns the engine child).
#
# The view chain is deliberately tiny and fully owned: Xvfb renders the display,
# x11vnc shares it (password-gated, fail-closed: no password -> no VNC at all),
# websockify/noVNC makes it a browser page on :6080. The tunnel overlay routes
# browser-view.<domain> there; locally the internal network is the only path.
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
WIDTH="${BROWSER_WIDTH:-1280}"
HEIGHT="${BROWSER_HEIGHT:-800}"

# Headless deploys skip the display stack entirely.
if [ "${BROWSER_HEADLESS:-0}" != "1" ]; then
  Xvfb "$DISPLAY" -screen 0 "${WIDTH}x${HEIGHT}x24" -ac +extension RANDR >/dev/null 2>&1 &
  # A window manager so Chromium gets normal focus/raise behavior under VNC.
  fluxbox >/dev/null 2>&1 &

  if [ -n "${BROWSER_VIEW_PASSWORD:-}" ]; then
    # -passwdfile, not -passwd: the password must not show up in /proc cmdlines.
    PASSFILE="$(mktemp)"
    printf '%s' "$BROWSER_VIEW_PASSWORD" > "$PASSFILE"
    x11vnc -display "$DISPLAY" -forever -shared -rfbport 5900 \
      -passwdfile "$PASSFILE" -xkb -quiet >/dev/null 2>&1 &
    websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
  else
    echo "browser: BROWSER_VIEW_PASSWORD unset -- live view disabled (headed display only)" >&2
  fi
fi

exec python server.py
