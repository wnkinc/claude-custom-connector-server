# deploy/host — the deploy reconciler

The host-side half of chat-driven tool deploys. The approval sidecar (after a human
approves the gatekeeper's `deploy_tool` call) can only **write a request file**; this
reconciler — running on the host, where `docker compose` lives — validates and
applies it. No container ever holds Docker rights. `reconcile.py`'s docstring
documents the control-file protocol and validation rules.

## Install (local box or cloud VM — identical)

From the repo root:

```bash
sudo python3 deploy/host/reconcile.py --init --repo $(pwd) --user $(whoami)
sed "s|__REPO__|$(pwd)|; s|__RUN_AS__|$(whoami)|" deploy/host/mcp-reconciler.service \
  | sudo tee /etc/systemd/system/mcp-reconciler.service
sudo systemctl daemon-reload && sudo systemctl enable --now mcp-reconciler
```

The AWS path (`deploy/aws`) does this automatically at boot. Logs:
`journalctl -u mcp-reconciler -f`.

## Without the daemon

Everything still works one notch more manually: the chat flow stages a request and
`deploy_status` will say one is waiting — apply it with

```bash
python3 deploy/host/reconcile.py --once
```
