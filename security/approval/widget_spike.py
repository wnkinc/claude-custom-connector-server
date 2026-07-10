"""SPIKE: in-chat approval widget (MCP Apps) — round 1, mechanism probe.

Enabled by SPIKE_APPROVAL_WIDGET=1; serve() calls register_widget_spike(mcp) before
it builds the security middleware. This is a THROWAWAY test surface, not the real
approval-widget integration -- it exists to answer three live-connector unknowns:

  1. Does an in-chat widget render for a tool (proven tool-definition _meta path)?
  2. Can the widget flip server state via callServerTool (Variant A), and does a
     direct fetch to the sidecar work or does CSP block it (Variant B probe)?
  3. Does app.sendMessage() make Claude take a new turn on its own?

It registers THREE things on the server:
  - resource ui://approve.html      -> the widget HTML (bundle inlined)
  - tool approval_probe(action)     -> approval+guardrail EXEMPT; mints a real pending
        approval via the sidecar and returns its token to the widget (round-1 shortcut:
        the token rides model-visible content; the forge-proof design moves it to _meta)
  - tool approve_gate(token, ...)   -> EXEMPT + hidden (visibility:["app"]); redeems the
        token against the sidecar's existing /approve/{token} endpoint

Round 2 will move the token into _meta and drive it from the real ApprovalMiddleware
pending path (which, being outermost, bypasses the guardrail).
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import httpx
from fastmcp.tools.tool import ToolResult

WIDGET_URI = "ui://approve.html"
_WIDGETS = Path(__file__).resolve().parent / "widgets"
_html_cache: str | None = None


def _widget_html() -> str:
    global _html_cache
    if _html_cache is None:
        _html_cache = (
            (_WIDGETS / "approve.html")
            .read_text()
            .replace("/*__EXT_APPS_BUNDLE__*/", (_WIDGETS / "ext-apps-bundle.js").read_text())
        )
    return _html_cache


def _approval_url() -> str:
    return os.getenv("APPROVAL_URL", "http://127.0.0.1:8072").rstrip("/")


def _public_base() -> str:
    return os.getenv("APPROVAL_PUBLIC_URL", "").rstrip("/")


def _extend_env_csv(name: str, *names: str) -> None:
    """Add tool names to a comma-separated env allowlist (e.g. MCP_APPROVAL_EXEMPT)."""
    have = {p.strip() for p in os.getenv(name, "").split(",") if p.strip()}
    os.environ[name] = ",".join(sorted(have | set(names)))


def register_widget_spike(mcp) -> None:  # type: ignore[no-untyped-def]
    # These helpers must not themselves be gated or screened, or the probe deadlocks.
    _extend_env_csv("MCP_APPROVAL_EXEMPT", "approval_probe", "approve_gate")
    _extend_env_csv("MCP_GUARDRAIL_EXEMPT", "approval_probe", "approve_gate")

    # Declare the sidecar origin in the widget's CSP connect list so the sandboxed
    # iframe may POST the approval directly to it (session-INDEPENDENT, unlike
    # callServerTool which dies with the MCP session once the turn ends). Both the
    # nested and flat key shapes, mirroring the resourceUri belt-and-suspenders.
    _csp = {"connectDomains": [b for b in [_public_base()] if b]}
    mcp.resource(
        WIDGET_URI,
        name="Approval widget",
        mime_type="text/html;profile=mcp-app",
        meta={"csp": _csp, "ui": {"csp": _csp}},
    )(lambda: _widget_html())

    async def approval_probe(action: str = "demo action") -> ToolResult:
        """SPIKE: render an in-chat Approve/Deny widget for a mock action.

        Creates a real pending approval in the sidecar and hands the widget its token
        so the buttons can redeem it. Use to exercise the in-chat approval widget.
        """
        import json

        # Unique call_key per invocation so every probe MINTS A FRESH pending (with a
        # token). Keying by `action` alone collided: a repeat call with the same action
        # landed on a prior pending, and /gate's non-create responses carry no token, so
        # the widget got token="" (this is the "no token" we saw, not a desktop bug). The
        # real middleware keys by (tool, args) on purpose; the probe wants a fresh one each time.
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_approval_url()}/gate",
                json={
                    "source": "approval-widget-spike",
                    "action": action,
                    "call_key": f"{action}:{secrets.token_hex(4)}",
                },
            )
        data = resp.json()
        payload = {
            "token": data.get("token", ""),
            "action": action,
            "public_base": _public_base(),
        }
        return ToolResult(content=json.dumps(payload))

    # Tool-definition _meta (both the nested and flat key, per the data-chart widget):
    # this is what makes the host render the widget for this tool's result.
    mcp.tool(
        name="approval_probe",
        meta={"ui": {"resourceUri": WIDGET_URI}, "ui/resourceUri": WIDGET_URI},
    )(approval_probe)

    async def approve_gate(token: str, decision: str = "approve") -> str:
        """SPIKE (widget-only): redeem an approval token. Hidden from Claude's tool list."""
        if decision not in {"approve", "deny"}:
            return "error: decision must be 'approve' or 'deny'"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_approval_url()}/approve/{token}", data={"decision": decision}
            )
        return f"approve_gate: {decision} -> HTTP {resp.status_code}"

    # visibility:["app"] hides this widget-only helper from Claude's tool list.
    mcp.tool(
        name="approve_gate",
        meta={"ui": {"visibility": ["app"]}},
    )(approve_gate)
