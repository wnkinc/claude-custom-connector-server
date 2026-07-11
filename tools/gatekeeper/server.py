"""Gatekeeper: flip any telegram tool's approval mode from chat.

A tiny control plane over the approval sidecar's runtime gating config
(security/approval/gating.py). Two tools:

  - list_gating()        -- read-only, exempt: show the current overrides.
  - set_gating(tool, mode) -- GATED: set a telegram tool's mode:
        free   -- runs with no approval card
        gated  -- each call needs a human approval
        hidden -- disabled: calls refuse outright AND the tool is filtered from
                  tools/list (the model stops seeing it once the connector
                  refreshes its cached list; the refusal is immediate)
    Because changing a safety gate is itself sensitive, this call is gated --
    flipping a gate requires a human approval in the card (recursively safe).

MVP: telegram only (TARGET_SOURCE). The sidecar holds the config; the telegram server
reads it live, so a change takes effect within seconds (the in-chat cards and the
visible tool list follow on the next connector refresh).
"""

import os
import sys
from pathlib import Path
from typing import Literal

import httpx
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from security.serve import serve  # noqa: E402

mcp = FastMCP(name="gatekeeper")

APPROVAL_URL = os.getenv("APPROVAL_URL", "http://approval:8072").rstrip("/")
TARGET_SOURCE = "telegram"  # MVP: this control plane manages the telegram server only

_MODE_LABEL = {
    "free": "runs freely (no approval)",
    "gated": "requires approval",
    "hidden": "HIDDEN (disabled: calls refuse, filtered from the tool list)",
}


@mcp.tool
async def list_gating() -> str:
    """Show each telegram tool's mode override: free (no approval), gated (needs
    approval), or hidden (disabled and filtered from the tool list).

    Read-only. A tool with no override follows its built-in default (writes require
    approval, vetted read-only tools run freely). Hidden tools ARE listed here --
    this is the operator's view, nothing is invisible to it."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{APPROVAL_URL}/gating", params={"source": TARGET_SOURCE})
    overrides = resp.json().get("overrides", {})
    if not overrides:
        return (
            "No mode overrides set on telegram. Every tool follows its default: "
            "writes require approval, vetted read-only tools run freely."
        )
    lines = [
        f"  - {tool}: {_MODE_LABEL.get(mode, mode)}" for tool, mode in sorted(overrides.items())
    ]
    return "Telegram mode overrides:\n" + "\n".join(lines)


@mcp.tool
async def set_gating(tool: str, mode: Literal["free", "gated", "hidden"]) -> str:
    """Set a telegram tool's mode: 'free' runs with no approval card, 'gated' needs a
    human approval per call, 'hidden' disables the tool outright (calls refuse
    immediately, and it disappears from the model's tool list once the connector
    refreshes).

    Changing a gate is itself gated, so this call needs your approval first. After it
    applies, refresh the telegram connector so its cards and tool list update."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{APPROVAL_URL}/gating",
            json={"source": TARGET_SOURCE, "tool": tool, "mode": mode},
        )
    if not resp.json().get("ok"):
        return f"⚠️ Failed to update gating for `{tool}`."
    effect = _MODE_LABEL[mode]
    return (
        f"✅ `{tool}` on telegram is now {effect}. Enforcement takes effect within a "
        "few seconds; refresh the telegram connector to update its in-chat cards "
        "and visible tool list."
    )


def main() -> None:
    port = int(os.getenv("MCP_PORT", "8065"))
    # list_gating is read-only (exempt); set_gating is GATED -- changing a gate needs a
    # human approval, shown via the in-chat widget (SPIKE_APPROVAL_WIDGET).
    os.environ.setdefault("MCP_APPROVAL_EXEMPT", "list_gating")
    serve(mcp, port=port, require_approval=True)


if __name__ == "__main__":
    main()
