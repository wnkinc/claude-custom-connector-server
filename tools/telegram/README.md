# telegram (:8063)

[chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) — a Telethon
**user-account** MTProto client exposed as MCP tools — running behind this repo's
shared security stack. The engine is a source checkout pinned by commit + sha256
in the Dockerfile (the PyPI name `telegram-mcp` belongs to an unrelated project;
the engine's own install guard refuses it). It speaks stdio as a child process;
`server.py` fronts it with a fastmcp proxy so `serve()` applies auth and the
guardrail exactly as for a native tool.

## Posture

- **Read-only by default** (`TELEGRAM_EXPOSED_TOOLS=read-only`, enforced as the
  child-env default in `server.py`): the engine's 49 `readOnlyHint` tools — read
  chats/messages/contacts, search. No send, delete, join, or admin ops.
- **Writes behind the approval gate**: `TELEGRAM_EXPOSED_TOOLS=all` (in `.env`)
  exposes the 67 write tools, and on the public posture every non-exempt call
  blocks on the out-of-band Slack approval (`security/approval/`) until a human
  clicks Approve. `approval-exempt.txt` — the engine's read-only names minus two
  upstream mislabels (`get_invite_link`/`export_chat_invite` actually *create*
  invite links) — keeps reads flowing without a tap per call. Regenerate that
  file when bumping the engine pin (instructions in its header).
- **Guardrail-screened output** (`untrusted_output=True`): message content from
  arbitrary chats is a prompt-injection vector, same class as xmcp's web content.
- **Egress**: MTProto dials Telegram DC IPs directly, so this tool's squid
  listener (:3131) carries the repo's first dst-CIDR allowlist (Telegram's
  published DC ranges) next to the usual domain list; Telethon is pointed at the
  wall via the engine's `TELEGRAM_PROXY_*` http-CONNECT support (`python-socks`).

## Setup (out-of-band, once)

1. API credentials: <https://my.telegram.org> → "API development tools" →
   `TELEGRAM_API_ID` + `TELEGRAM_API_HASH`.
2. Session string (interactive phone login, on a trusted machine):
   `uvx --from git+https://github.com/chigwell/telegram-mcp telegram-mcp-generate-session`
3. `cp env.example .env`, fill in the three values (plus Google OAuth for public).

The session string is full account access — read everything, send as you. It
lives only in the gitignored `.env`.

## Tests

`pytest tools/telegram` — no Telegram, no network: child-env overrides, approval
exemptions, schema strip, and proxy forwarding against a dummy stdio child.
