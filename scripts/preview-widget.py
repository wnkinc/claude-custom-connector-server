#!/usr/bin/env python3
"""Preview the manage widget in a plain browser -- no claude.ai, no sidecar.

Serves security/approval/widgets/manage.html at http://127.0.0.1:8123 with the
claude.ai host bridge stubbed (theme + tool-result delivery) and the sidecar's
/manage API replaced by an in-memory stub over a canned catalog -- so Save is
sandboxed and nothing real changes. The page is RE-BAKED on every request:
edit manage.html and just reload the browser. Append ?theme=dark for dark mode.

  python3 scripts/preview-widget.py [port]
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

WIDGETS = Path(__file__).resolve().parents[1] / "security" / "approval" / "widgets"

# A representative catalog: every group, plus a pinned tool to preview that state.
CATALOG = {
    "ok": True,
    "source": "telegram",
    "pinned": ["set_gating"],
    "tools": {
        "get_me": {"description": "", "read_only": True, "mode": "always_allow"},
        "get_chats": {"description": "", "read_only": True, "mode": "always_allow"},
        "list_contacts": {"description": "", "read_only": True, "mode": "always_allow"},
        "search_messages": {"description": "", "read_only": True, "mode": "needs_approval"},
        "send_message": {"description": "", "read_only": False, "mode": "needs_approval"},
        "delete_message": {"description": "", "read_only": False, "mode": "blocked"},
        "create_group": {"description": "", "read_only": False, "mode": "always_allow"},
        "edit_message": {"description": "", "read_only": False, "mode": "always_allow"},
        "set_gating": {"description": "", "read_only": False, "mode": "needs_approval"},
        "approval_probe": {"description": "", "read_only": None, "mode": "always_allow"},
    },
}

# The pieces claude.ai normally provides: the ExtApps bridge (theme + tool result)
# and the network. The chat-surface background is painted here too, since the real
# widget is transparent on purpose.
STUB = """
globalThis.ExtApps = {
  applyDocumentTheme: (t) => {
    document.documentElement.dataset.theme = t;
    document.documentElement.style.colorScheme = t;
    document.body.style.background = t === "dark" ? "#262624" : "#faf9f5";
  },
  App: class {
    constructor() {}
    async connect() { Promise.resolve().then(() => this.ontoolresult?.(window.__TOOLRESULT)); }
    getHostContext() { return { theme: new URLSearchParams(location.search).get("theme") || "light" }; }
  },
};
const REAL_FETCH = window.fetch.bind(window);
window.fetch = async (url, opts = {}) => {
  if (!String(url).includes("/manage/")) return REAL_FETCH(url, opts);
  if ((opts.method || "GET") === "POST") {
    const changes = JSON.parse(opts.body).changes;
    const refused = Object.keys(changes).filter((t) => window.__CATALOG.pinned.includes(t));
    for (const [t, m] of Object.entries(changes)) {
      if (!refused.includes(t)) window.__CATALOG.tools[t].mode = m;
    }
    return new Response(JSON.stringify({ ok: true, applied: Object.keys(changes).length - refused.length, refused, modes: {} }), { status: 200 });
  }
  return new Response(JSON.stringify(window.__CATALOG), { status: 200 });
};
"""


def bake() -> bytes:
    html = (WIDGETS / "manage.html").read_text()
    marker = json.dumps({"token": "preview", "source": "telegram"})
    boot = (
        f"{STUB}\nwindow.__CATALOG = {json.dumps(CATALOG)};\n"
        f"window.__TOOLRESULT = {{ content: [{{ text: `<!--MANAGE {marker}-->` }}] }};"
    )
    return (
        html.replace("/*__EXT_APPS_BUNDLE__*/", boot)
        .replace("__APPROVAL_PUBLIC_BASE__", "")
        .encode()
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        body = bake() if self.path.split("?")[0] == "/" else b""
        self.send_response(200 if body else 404)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    print(
        f"widget preview: http://127.0.0.1:{port}/   (dark: ?theme=dark; edit manage.html, reload)"
    )
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
