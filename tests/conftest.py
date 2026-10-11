"""Shared fixtures for the auth tests.

These tests need no database, no model and no network. `provenance.chain` is replaced
with a recorder so the tests can assert exactly what identity the API hands to the
retrieval pipeline. The row-level security guarantees are covered separately by
`make rls-test`; this suite covers the layer in front of them: whether the identity
those policies enforce can be forged.
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ISSUER = "https://idp.example.test/"
AUDIENCE = "controlplane-api"


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def ask(self, question, user=None, record_evidence=True, requested_model=None,
            verified_groups=None):
        self.calls.append({"question": question, "user": user,
                           "verified_groups": verified_groups})
        return {"answer": "stub", "user": user}


@pytest.fixture(scope="session")
def recorder() -> Recorder:
    rec = Recorder()
    stub = types.ModuleType("provenance.chain")
    stub.ask = rec.ask
    sys.modules["provenance.chain"] = stub
    return rec


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch):
    """A developer's local .env must not change what these tests mean.

    Demo mode in particular adds rate limiting and canned-question checks, which
    would turn assertions about authentication into assertions about throttling.
    """
    from provenance.config import settings

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "auth_mode", "persona")


@pytest.fixture(scope="session")
def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def other_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StaticResolver:
    def __init__(self, public_key) -> None:
        self.public_key = public_key

    def signing_key(self, token: str):
        return self.public_key


@pytest.fixture()
def oidc(monkeypatch, keypair, recorder):
    """Configure oidc mode with a local signing key."""
    from provenance import auth
    from provenance.config import settings

    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)
    monkeypatch.setattr(settings, "oidc_jwks_url", "https://idp.example.test/jwks")
    monkeypatch.setattr(settings, "oidc_algorithms", "RS256")
    monkeypatch.setattr(settings, "oidc_groups_claim", "groups")
    monkeypatch.setattr(settings, "oidc_admin_group", "cp-admin")
    auth.set_key_resolver(StaticResolver(keypair.public_key()))
    recorder.calls.clear()
    yield
    auth.set_key_resolver(None)


@pytest.fixture()
def client(recorder):
    from fastapi.testclient import TestClient

    from provenance.api import app

    recorder.calls.clear()
    return TestClient(app)


@pytest.fixture()
def make_token(keypair):
    def _make(key=None, headers=None, algorithm="RS256", **overrides) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER, "aud": AUDIENCE, "sub": "user-123",
            "iat": now, "exp": now + 300, "groups": ["public", "engineering"],
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or keypair, algorithm=algorithm, headers=headers)
    return _make


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def public_pem(private_key) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
