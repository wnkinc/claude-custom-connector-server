"""Standard composition + serving for mcp-tools servers.

Every tool builds its own FastMCP ``mcp`` (just its tools), then calls :func:`serve`,
which applies the shared cross-cutting security layers UNIFORMLY and runs the HTTP
server. This is the one place the layering/order lives, so every tool gets it right:

- **Google OAuth** (``security.auth``) — always applied; no-ops to an open loopback
  server when ``MCP_AUTH_ENABLED`` is off.
- **Out-of-band human approval** (``security.approval``) — opt in with
  ``require_approval=True``.
- **Guardrail output screening** (``security.guardrail``) — opt in with
  ``untrusted_output=True`` (for tools that return untrusted external content).

A tool declares its threat posture in one line::

    serve(mcp, port=p, untrusted_output=True, require_approval=True)  # e.g. x-mcp
    serve(mcp, port=p)                                                # trusted internal data
"""

from __future__ import annotations

import os

from security.approval.middleware import ApprovalMiddleware, register_approval_routes
from security.auth import build_oauth_provider
from security.guardrail.middleware import GuardrailMiddleware


def _csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def serve(
    mcp,  # type: ignore[no-untyped-def]
    *,
    port: int,
    host: str | None = None,
    untrusted_output: bool = False,
    require_approval: bool = False,
    guardrail_source: str | None = None,
    approval_exempt_env: str = "MCP_APPROVAL_EXEMPT",
) -> None:
    """Apply the shared security layers to ``mcp`` and run it over HTTP.

    ORDER MATTERS: FastMCP wraps ``reversed(middleware)``, so the first-added is the
    OUTERMOST. Approval must be outermost — it short-circuits BEFORE the tool runs, so
    a pending-approval message is never screened — with the guardrail INSIDE it,
    screening only results of calls the human already approved.
    """
    host = host or os.getenv("MCP_HOST", "127.0.0.1")

    if require_approval:
        mcp.add_middleware(ApprovalMiddleware(exempt=_csv_set(os.getenv(approval_exempt_env))))
        register_approval_routes(mcp)
    if untrusted_output:
        mcp.add_middleware(GuardrailMiddleware(source=guardrail_source or mcp.name))

    auth = build_oauth_provider()
    if auth is not None:
        mcp.auth = auth

    mcp.run(transport="http", host=host, port=port)
