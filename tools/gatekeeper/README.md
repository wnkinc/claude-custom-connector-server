# gatekeeper (:8065)

The operator's control plane over every other tool: per-tool permission modes
(always_allow / needs_approval / blocked), the in-chat permissions panel and
secrets-staging form, and chat-driven deploys via the host reconciler. Native
code — no wrapped engine — and always on, like the sidecars: "using" it is just
attaching its connector in claude.ai.

The full design (mode authority, pins, the manage/deploy flows) lives in
[docs/GATEKEEPER.md](../../docs/GATEKEEPER.md); the deploy protocol's host side
in [deploy/host/README.md](../../deploy/host/README.md).
