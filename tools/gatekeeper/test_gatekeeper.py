"""Gatekeeper's deploy_status formatting -- the agent-facing deploy UX.

format_deploy_status is a pure function (manifests + sidecar sources + reconciler
state + now -> text), so these tests pin its branching with no sidecar and no
network: deployed vs stale vs available sections, secrets/prerequisites lines,
and the reconciler's live / in-flight / absent guidance.
"""

import importlib.util
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_SPEC = importlib.util.spec_from_file_location(
    "gatekeeper_server", Path(__file__).parent / "server.py"
)
gk = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gk)

NOW = time.time()

MANIFESTS = {
    "weather": {
        "title": "Weather",
        "profile": "weather",
        "subdomain": "weather",
        "port": 8070,
        "summary": "Forecasts from an external API.",
        "secrets": [{"key": "W_KEY", "label": "Weather key", "hint": "example.com console"}],
        "prerequisites": ["enable the Weather API in the provider console"],
        "notes": ["tiny image"],
        "depends": [],
    }
}


def fmt(sources=None, deploy=None, manifests=None):
    return gk.format_deploy_status(
        manifests if manifests is not None else MANIFESTS, sources or {}, deploy, now=NOW
    )


def test_fresh_beacon_lists_deployed_with_last_used():
    out = fmt({"weather": {"registered": NOW - 20, "seen": NOW - 7200, "tools": 3}})
    assert "- weather: 3 tools, last used 2h ago" in out
    assert "Available to deploy" not in out


def test_gatekeeper_source_is_omitted():
    out = fmt({"gatekeeper": {"registered": NOW - 20, "seen": None, "tools": 5}}, manifests={})
    assert "gatekeeper" not in out


def test_stale_source_and_never_used():
    out = fmt({"weather": {"registered": NOW - 86400 * 3, "seen": None, "tools": 3}})
    assert "Stale (stored state, no live server):" in out
    assert "- weather: last registered 3d ago" in out


def test_undeployed_manifest_lists_prereqs_secrets_and_notes():
    out = fmt()
    assert "- weather: Forecasts from an external API." in out
    assert "before anything, step 1: enable the Weather API" in out
    assert "secrets needed: Weather key (example.com console)" in out
    assert "note: tiny image" in out


def test_inventory_shows_staged_or_missing_secrets():
    live = {"reconciler": "live", "inventory": {"weather": {"secrets_ready": True}}}
    assert "secrets staged: staged ✓" in fmt(deploy=live)
    missing = {
        "reconciler": "live",
        "inventory": {"weather": {"secrets_ready": False, "missing_secrets": ["W_KEY"]}},
    }
    assert "secrets staged: missing: W_KEY" in fmt(deploy=missing)


def test_reconciler_live_gives_the_deploy_steps():
    out = fmt(deploy={"reconciler": "live", "inventory": {}})
    assert "To deploy, in order:" in out
    assert "deploy_tool(<name>)" in out


def test_in_flight_deploy_reports_progress():
    out = fmt(
        deploy={
            "reconciler": "live",
            "in_flight": True,
            "request": {"tool": "weather"},
            "status": {"phase": "applying"},
        }
    )
    assert "Deploy in flight: weather (applying)" in out


def test_reconciler_absent_gives_the_manual_path():
    out = fmt(deploy=None)
    assert "deploy reconciler is not running" in out
    assert "docker" in out  # the manual compose instructions


def test_ago_buckets():
    assert gk._ago(None, NOW) == "never"
    assert gk._ago(NOW - 120, NOW) == "2m ago"
    assert gk._ago(NOW - 7200, NOW) == "2h ago"
    assert gk._ago(NOW - 86400 * 2, NOW) == "2d ago"
