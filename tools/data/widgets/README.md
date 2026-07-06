# chart.html — the data-chart widget (MCP Apps)

`chart.html` is the in-chat candlestick widget for the `data-chart` tool. The server
inlines `ext-apps-bundle.js` into it at first resource read (see `_widget_html` in
`server.py`) — the host iframe's CSP blocks external script fetches, so the runtime
must travel inside the HTML.

## Local dev loop

```
python3 tools/data/widgets/preview.py     # -> http://127.0.0.1:8090/
```

Serves the widget with a fake `ExtApps` shim and synthetic bars — plain browser tab,
ordinary devtools, re-reads chart.html per request (edit → refresh). `?theme=dark`,
`?n=500`, `?interval=1h`, `?payload={"error":"..."}`; press `d` to toggle theme.

## ext-apps-bundle.js — vendored, do not edit

| | |
|---|---|
| Source | npm `@modelcontextprotocol/ext-apps@1.7.4`, file `dist/src/app-with-deps.js` |
| Tarball sha256 | `f9bd6546d6c18f7ad3e9d9bb934eec909db4ea6b85988647db447d08c4b6ce4a` |
| Local change | trailing `export{…}` rewritten to `globalThis.ExtApps={…}` (the standard inlining transform from the MCP apps SDK docs) — no other edits |

To upgrade: download the new tarball, extract the same file, re-apply the export
rewrite, update this table.
