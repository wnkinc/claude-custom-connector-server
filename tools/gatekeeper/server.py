"""Gatekeeper: flip any telegram tool's approval requirement from chat.

A tiny control plane over the approval sidecar's runtime gating config
(security/approval/gating.py). Two tools:

  - list_gating()                       -- read-only, exempt: show the current overrides.
  - set_gating(tool, requires_approval) -- GATED: change whether a telegram tool needs
        approval. Because changing a safety gate is itself sensitive, this call is gated
        -- flipping a gate requires a human approval in the card (recursively safe).

MVP: telegram only (TARGET_SOURCE). The sidecar holds the config; the telegram server
reads it live, so a change takes effect within seconds (the in-chat cards follow on the
next connector refresh).
"""

import os
import sys
from pathlib import Path

import httpx
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from security.serve import serve  # noqa: E402

mcp = FastMCP(name="gatekeeper")

APPROVAL_URL = os.getenv("APPROVAL_URL", "http://approval:8072").rstrip("/")
TARGET_SOURCE = "telegram"  # MVP: this control plane manages the telegram server only


@mcp.tool
async def list_gating() -> str:
    """Show which telegram tools have been flipped to run freely vs. require approval.

    Read-only. A tool with no override follows its built-in default (writes require
    approval, vetted read-only tools run freely)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{APPROVAL_URL}/gating", params={"source": TARGET_SOURCE})
    overrides = resp.json().get("overrides", {})
    if not overrides:
        return (
            "No approval overrides set on telegram. Every tool follows its default: "
            "writes require approval, vetted read-only tools run freely."
        )
    lines = [
        f"  - {tool}: {'requires approval' if req else 'runs freely (no approval)'}"
        for tool, req in sorted(overrides.items())
    ]
    return "Telegram approval overrides:\n" + "\n".join(lines)


@mcp.tool
async def set_gating(tool: str, requires_approval: bool) -> str:
    """Flip whether a telegram tool requires approval.

    requires_approval=False makes the tool run freely (no approval card); True restores
    the gate. Changing a gate is itself gated, so this call needs your approval first.
    After it applies, refresh the telegram connector so its cards update."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{APPROVAL_URL}/gating",
            json={"source": TARGET_SOURCE, "tool": tool, "requires_approval": requires_approval},
        )
    ok = resp.json().get("ok")
    state = "REQUIRES APPROVAL" if requires_approval else "RUNS FREELY (no approval)"
    if not ok:
        return f"⚠️ Failed to update gating for `{tool}`."
    return (
        f"✅ `{tool}` on telegram now {state}. Takes effect within a few seconds; "
        "refresh the telegram connector to update its in-chat cards."
    )


def main() -> None:
    port = int(os.getenv("MCP_PORT", "8065"))
    # list_gating is read-only (exempt); set_gating is GATED -- changing a gate needs a
    # human approval, shown via the in-chat widget (SPIKE_APPROVAL_WIDGET).
    os.environ.setdefault("MCP_APPROVAL_EXEMPT", "list_gating")
    serve(mcp, port=port, require_approval=True)


if __name__ == "__main__":
    main()
