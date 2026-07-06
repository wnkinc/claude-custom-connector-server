"""Local dev server for the chart widget — no MCP host, no container, no deps.

Serves chart.html with a fake ``ExtApps`` shim in place of the vendored bundle, so
the widget runs in a plain browser tab with ordinary devtools. The HTML is re-read
on every request: edit chart.html, refresh the tab, done.

    python3 tools/data/widgets/preview.py            # http://127.0.0.1:8090/

Query params:
    ?n=250            bars in the synthetic series (seeded random walk)
    ?symbol=BTCUSD    header symbol
    ?interval=1d      1d spacing vs intraday timestamps (e.g. 1h, 5m)
    ?theme=dark       start dark (press "d" in the page to toggle live)
    ?payload={...}    render an explicit payload instead (e.g. {"error":"..."})

Routes:
    /       chart.html with a fake ExtApps shim (fastest loop; no handshake)
    /real   chart.html with the REAL vendored bundle inlined (what production serves)
    /host   a minimal MCP-apps HOST page that iframes /real and speaks the actual
            postMessage handshake (ui/initialize -> initialized -> tool-result),
            logging the message flow — full-fidelity check of bundle + widget

This previews the WIDGET only — the real ``_meta.ui`` wiring is exercised against
the live server (see server.py: data-chart + the ui:// resource).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WIDGETS = Path(__file__).resolve().parent

# Mirrors the real App surface the widget touches (see chart.html). connect() pulls
# the payload from ?payload= or /payload.json; "d" toggles theme like a host would.
SHIM = """
globalThis.ExtApps = { App: class {
  constructor() { this._q = new URLSearchParams(location.search); }
  async connect() {
    const explicit = this._q.get("payload");
    const text = explicit ?? await (await fetch("/payload.json" + location.search)).text();
    this.ontoolresult?.({ content: [{ type: "text", text }] });
    document.addEventListener("keydown", (e) => {
      if (e.key === "d") {
        this._dark = !this._dark;
        this.onhostcontextchanged?.({ theme: this._dark ? "dark" : "light" });
      }
    });
  }
  getHostContext() {
    this._dark ??= this._q.get("theme") === "dark";
    return { theme: this._dark ? "dark" : "light" };
  }
  sendMessage(m) { console.log("sendMessage", m); }
  updateModelContext(m) { console.log("updateModelContext", m); }
  callServerTool(r) { console.log("callServerTool", r); return Promise.resolve({ content: [] }); }
  openLink(l) { console.log("openLink", l); }
} };
"""


def synth_payload(q: dict[str, list[str]]) -> dict:
    """Deterministic random-walk OHLCV shaped exactly like server.py's data-chart JSON."""
    n = min(2000, max(2, int(q.get("n", ["250"])[0])))
    symbol = q.get("symbol", ["BTCUSD"])[0].upper()
    interval = q.get("interval", ["1d"])[0]
    rng = random.Random(42)  # noqa: S311 — deterministic fake chart data, not crypto
    step = {"1d": dt.timedelta(days=1)}.get(interval, dt.timedelta(minutes=60))
    t0 = dt.datetime(2026, 1, 1) - n * step
    price, bars = 60000.0, []
    for i in range(n):
        drift = math.sin(i / 40) * 0.004  # slow cycle so trends & reversals both show
        o = price
        c = o * (1 + drift + rng.gauss(0, 0.02))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.008)))
        low = min(o, c) * (1 - abs(rng.gauss(0, 0.008)))
        ts = t0 + i * step
        fmt = "%Y-%m-%d" if interval == "1d" else "%Y-%m-%d %H:%M"
        bars.append(
            [
                ts.strftime(fmt),
                round(o, 2),
                round(h, 2),
                round(low, 2),
                round(c, 2),
                round(abs(rng.gauss(800, 300)), 3),
            ]
        )
        price = c
    return {
        "summary": f"{n} {interval} crypto bars for {symbol} (synthetic preview data).",
        "symbol": symbol,
        "asset": "crypto",
        "interval": interval,
        "source": "preview",
        "stored_rows": n,
        "start": bars[0][0],
        "end": bars[-1][0],
        "bars": bars,
    }


# Fake HOST page: iframes /real and speaks the actual MCP-apps postMessage protocol
# (method names from the ext-apps bundle). Logs the flow + iframe errors so a
# headless screenshot shows exactly where the handshake dies.
HOST_PAGE = """<!doctype html><meta charset="utf-8">
<style>body{margin:0;font:12px monospace} iframe{width:100%;border:1px dashed #999}
pre{background:#f4f4f2;margin:0;padding:6px;white-space:pre-wrap}</style>
<iframe id="f" height="420"></iframe><pre id="log"></pre>
<script>
const log = (m) => { document.getElementById("log").textContent += m + "\\n"; };
const f = document.getElementById("f");
const payload = fetch("/payload.json" + location.search).then((r) => r.text());
window.addEventListener("message", async (ev) => {
  const m = ev.data;
  if (!m || m.jsonrpc !== "2.0") return;
  log("app -> host: " + (m.method ?? "response#" + m.id) + (m.error ? " ERROR " + JSON.stringify(m.error) : ""));
  const post = (msg) => f.contentWindow.postMessage({ jsonrpc: "2.0", ...msg }, "*");
  if (m.method === "ui/initialize") {
    post({ id: m.id, result: {
      protocolVersion: "2026-01-26",
      hostInfo: { name: "preview-host", version: "0" },
      hostCapabilities: {},
      hostContext: {
        theme: new URLSearchParams(location.search).get("theme") === "dark" ? "dark" : "light",
        displayMode: "inline", availableDisplayModes: ["inline"],
        containerDimensions: { maxHeight: 480 },
      },
    }});
  } else if (m.method === "ui/notifications/initialized") {
    post({ method: "ui/notifications/tool-result", params: {
      content: [{ type: "text", text: await payload }],
      structuredContent: { result: await payload }, isError: false,
    }});
    log("host -> app: tool-result sent");
  } else if (m.method === "ui/notifications/size-changed") {
    if (m.params?.height) f.height = m.params.height;
  } else if (m.id !== undefined && m.method) {
    post({ id: m.id, result: {} });
  }
});
f.addEventListener("load", () => {   // same-origin -> surface the iframe's own errors
  f.contentWindow.onerror = (msg, src, line) => log("IFRAME ERROR: " + msg + " @" + line);
});
f.src = "/real" + location.search;
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        url = urlparse(self.path)
        if url.path == "/payload.json":
            body = json.dumps(synth_payload(parse_qs(url.query))).encode()
            ctype = "application/json"
        elif url.path == "/":
            html = (WIDGETS / "chart.html").read_text()
            body = html.replace("/*__EXT_APPS_BUNDLE__*/", SHIM).encode()
            ctype = "text/html; charset=utf-8"
        elif url.path == "/real":
            html = (WIDGETS / "chart.html").read_text()
            bundle = (WIDGETS / "ext-apps-bundle.js").read_text()
            body = html.replace("/*__EXT_APPS_BUNDLE__*/", bundle).encode()
            ctype = "text/html; charset=utf-8"
        elif url.path == "/host":
            body = HOST_PAGE.encode()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # quiet: errors only
        if not str(args[1] if len(args) > 1 else "").startswith("200"):
            super().log_message(fmt, *args)


def main() -> None:
    port = int(os.getenv("PREVIEW_PORT", "8090"))
    print(f"widget preview: http://127.0.0.1:{port}/  (?theme=dark, ?n=500, 'd' toggles theme)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
