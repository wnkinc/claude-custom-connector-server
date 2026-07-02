"""Tests for the fail-closed OAuth config gating and the email-allowlist wrapper.

Upstream Google verification is faked by patching GoogleTokenVerifier.verify_token
BEFORE the provider is built (GoogleAllowlistProvider captures it at init), so only
our screening logic is under test -- no network.
"""

import asyncio

import pytest
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleTokenVerifier

from security.auth import GoogleAllowlistProvider, _email_is_verified, build_oauth_provider

AUTH_ENV = [
    "MCP_AUTH_ENABLED",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "MCP_PUBLIC_URL",
    "MCP_ALLOWED_GOOGLE_EMAILS",
]


@pytest.fixture(autouse=True)
def clean_auth_env(monkeypatch):
    for name in AUTH_ENV:
        monkeypatch.delenv(name, raising=False)


def _enable_auth(monkeypatch, **overrides):
    env = {
        "MCP_AUTH_ENABLED": "1",
        "GOOGLE_CLIENT_ID": "cid",
        "GOOGLE_CLIENT_SECRET": "cs",
        "MCP_PUBLIC_URL": "https://tool.example.com",
        "MCP_ALLOWED_GOOGLE_EMAILS": "me@example.com",
        **overrides,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)


# --- build_oauth_provider: fail-closed config gate --------------------------------


def test_auth_disabled_returns_none():
    assert build_oauth_provider() is None


@pytest.mark.parametrize("missing", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "MCP_PUBLIC_URL"])
def test_missing_credential_refuses_to_start(monkeypatch, missing):
    _enable_auth(monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match=missing):
        build_oauth_provider()


def test_empty_allowlist_refuses_to_start(monkeypatch):
    _enable_auth(monkeypatch, MCP_ALLOWED_GOOGLE_EMAILS="")
    with pytest.raises(RuntimeError, match="MCP_ALLOWED_GOOGLE_EMAILS"):
        build_oauth_provider()


def test_full_config_builds_allowlist_provider(monkeypatch):
    _enable_auth(monkeypatch, MCP_ALLOWED_GOOGLE_EMAILS=" Me@Example.com , two@example.com ")
    provider = build_oauth_provider()
    assert isinstance(provider, GoogleAllowlistProvider)
    assert provider._allowed_emails == {"me@example.com", "two@example.com"}


# --- the allowlist wrapper around Google's verifier -------------------------------


def _provider_with_fake_google(monkeypatch, claims_or_none, allowed):
    async def fake_verify(self, token):
        if claims_or_none is None:
            return None  # upstream Google verification failed
        return AccessToken(token=token, client_id="cid", scopes=[], claims=claims_or_none)

    monkeypatch.setattr(GoogleTokenVerifier, "verify_token", fake_verify)
    return GoogleAllowlistProvider(
        allowed_emails=allowed,
        client_id="cid",
        client_secret="cs",
        base_url="http://127.0.0.1:9",
        redirect_path="/auth/callback",
        required_scopes=["openid", "email"],
    )


def _verify(provider):
    return asyncio.run(provider._token_validator.verify_token("tok"))


def test_allowlisted_verified_email_passes(monkeypatch):
    provider = _provider_with_fake_google(
        monkeypatch, {"email": "me@example.com", "email_verified": True}, {"me@example.com"}
    )
    result = _verify(provider)
    assert result is not None
    assert result.claims["email"] == "me@example.com"


def test_unlisted_email_is_rejected(monkeypatch):
    provider = _provider_with_fake_google(
        monkeypatch, {"email": "intruder@example.com", "email_verified": True}, {"me@example.com"}
    )
    assert _verify(provider) is None


def test_missing_email_claim_is_rejected(monkeypatch):
    provider = _provider_with_fake_google(monkeypatch, {}, {"me@example.com"})
    assert _verify(provider) is None


def test_unverified_email_is_rejected(monkeypatch):
    provider = _provider_with_fake_google(
        monkeypatch, {"email": "me@example.com", "email_verified": "false"}, {"me@example.com"}
    )
    assert _verify(provider) is None


def test_allowlist_is_case_insensitive(monkeypatch):
    provider = _provider_with_fake_google(
        monkeypatch, {"email": "Me@Example.com", "email_verified": "true"}, {"ME@EXAMPLE.COM"}
    )
    assert _verify(provider) is not None


def test_upstream_failure_stays_failed(monkeypatch):
    provider = _provider_with_fake_google(monkeypatch, None, {"me@example.com"})
    assert _verify(provider) is None


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        (None, True),  # absent claim: don't second-guess a verified token
        (True, True),
        ("true", True),
        ("1", True),
        (False, False),
        ("false", False),
    ],
)
def test_email_verified_claim_forms(claim, expected):
    assert _email_is_verified(claim) is expected
