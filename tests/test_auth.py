"""Can the identity that row-level security enforces be forged?

Each test names an attack or a failure and asserts two things: the HTTP outcome, and
that the retrieval pipeline was never called. The second matters more. A 401 that
still ran the query would be a leak with a polite error message.
"""
from __future__ import annotations

import json
import sys
import time
import types

import jwt
import pytest

from conftest import AUDIENCE, ISSUER, bearer, public_pem

Q = {"question": "What is the salary band for a Senior Engineer II?"}


# --- the happy path ---------------------------------------------------------

def test_valid_token_uses_token_identity_and_groups(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers=bearer(make_token()))
    assert r.status_code == 200
    assert recorder.calls == [{
        "question": Q["question"], "user": "user-123",
        "verified_groups": ["public", "engineering"],
        "delegation": [],
    }]


def test_groups_claim_may_be_a_single_string(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers=bearer(make_token(groups="public")))
    assert r.status_code == 200
    assert recorder.calls[0]["verified_groups"] == ["public"]


def test_non_string_group_members_are_dropped(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers=bearer(
        make_token(groups=["public", 7, None, {"x": 1}, "", "public"])))
    assert r.status_code == 200
    assert recorder.calls[0]["verified_groups"] == ["public"]


def test_missing_groups_claim_means_no_groups_not_all_groups(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers=bearer(make_token(groups=None)))
    assert r.status_code == 200
    assert recorder.calls[0]["verified_groups"] == []


def test_admin_group_is_not_an_entitlement(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers=bearer(make_token(groups=["public", "cp-admin"])))
    assert r.status_code == 200
    assert recorder.calls[0]["verified_groups"] == ["public"]


# --- identity cannot come from the caller ----------------------------------

def test_body_user_is_rejected_even_with_a_valid_token(oidc, client, make_token, recorder):
    r = client.post("/ask", json={**Q, "user": "dana"}, headers=bearer(make_token()))
    assert r.status_code == 400
    assert recorder.calls == []


def test_body_user_without_a_token_is_a_401_not_a_pass(oidc, client, recorder):
    r = client.post("/ask", json={**Q, "user": "dana"})
    assert r.status_code == 401
    assert recorder.calls == []


# --- tokens that must fail --------------------------------------------------

def test_no_token(oidc, client, recorder):
    r = client.post("/ask", json=Q)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert recorder.calls == []


def test_wrong_scheme(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers={"Authorization": f"Basic {make_token()}"})
    assert r.status_code == 401
    assert recorder.calls == []


def test_garbage_token(oidc, client, recorder):
    r = client.post("/ask", json=Q, headers=bearer("not-a-jwt"))
    assert r.status_code == 401
    assert recorder.calls == []


def test_expired_token(oidc, client, make_token, recorder):
    past = int(time.time()) - 3600
    r = client.post("/ask", json=Q, headers=bearer(make_token(iat=past - 60, exp=past)))
    assert r.status_code == 401
    assert recorder.calls == []


def test_wrong_audience(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers=bearer(make_token(aud="some-other-api")))
    assert r.status_code == 401
    assert recorder.calls == []


def test_wrong_issuer(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers=bearer(make_token(iss="https://evil.example.test/")))
    assert r.status_code == 401
    assert recorder.calls == []


def test_forged_signature(oidc, client, make_token, other_keypair, recorder):
    forged = make_token(key=other_keypair, groups=["public", "hr-comp"], sub="dana")
    r = client.post("/ask", json=Q, headers=bearer(forged))
    assert r.status_code == 401
    assert recorder.calls == []


def test_alg_none_is_refused(oidc, client, recorder):
    now = int(time.time())
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "dana", "iat": now, "exp": now + 300,
         "groups": ["hr-comp"]},
        key=None, algorithm="none",
    )
    r = client.post("/ask", json=Q, headers=bearer(token))
    assert r.status_code == 401
    assert recorder.calls == []


def test_hs256_key_confusion_is_refused(oidc, client, keypair, recorder):
    """Sign with HS256 using the public key as the secret. A verifier that trusts the
    token's own `alg` header would accept this, because the public key is public."""
    import base64, hashlib, hmac

    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": ISSUER, "aud": AUDIENCE, "sub": "dana", "iat": now, "exp": now + 300,
               "groups": ["hr-comp"]}
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    signing_input = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(payload).encode())}"
    sig = hmac.new(public_pem(keypair), signing_input.encode(), hashlib.sha256).digest()
    token = f"{signing_input}.{b64(sig)}"
    r = client.post("/ask", json=Q, headers=bearer(token))
    assert r.status_code == 401
    assert recorder.calls == []


@pytest.mark.parametrize("missing", ["exp", "iat", "iss", "aud", "sub"])
def test_required_claims_are_required(oidc, client, make_token, recorder, missing):
    r = client.post("/ask", json=Q, headers=bearer(make_token(**{missing: None})))
    assert r.status_code == 401
    assert recorder.calls == []


def test_failure_reason_is_not_disclosed(oidc, client, make_token):
    expired = client.post("/ask", json=Q, headers=bearer(make_token(exp=int(time.time()) - 999)))
    bad_aud = client.post("/ask", json=Q, headers=bearer(make_token(aud="x")))
    assert expired.json() == bad_aud.json() == {"detail": "Authentication required."}


# --- the admin plane --------------------------------------------------------

@pytest.fixture()
def fake_evidence(monkeypatch):
    mod = types.ModuleType("provenance.evidence")
    mod.verify = lambda: {"ok": True}
    monkeypatch.setitem(sys.modules, "provenance.evidence", mod)


@pytest.mark.parametrize("path", ["/api/verify", "/api/usage", "/api/evidence"])
def test_admin_endpoints_require_a_token(oidc, client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ["/api/verify", "/api/usage", "/api/evidence"])
def test_admin_endpoints_reject_non_admins(oidc, client, make_token, path):
    assert client.get(path, headers=bearer(make_token())).status_code == 403


def test_admin_can_verify_the_ledger(oidc, client, make_token, fake_evidence):
    r = client.get("/api/verify", headers=bearer(make_token(groups=["cp-admin"])))
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_persona_table_is_hidden_in_oidc_mode(oidc, client):
    assert client.get("/api/personas").status_code == 404


# --- persona mode is unchanged ---------------------------------------------

def test_persona_mode_still_takes_the_body_user(monkeypatch, client, recorder):
    from provenance.config import settings

    monkeypatch.setattr(settings, "auth_mode", "persona")
    r = client.post("/ask", json={**Q, "user": "sam"})
    assert r.status_code == 200
    assert recorder.calls == [{"question": Q["question"], "user": "sam",
                               "verified_groups": None, "delegation": None}]


# --- configuration fails closed --------------------------------------------

def test_unknown_auth_mode_refuses_to_start_and_to_serve(monkeypatch, client, recorder):
    from provenance import auth
    from provenance.config import settings

    monkeypatch.setattr(settings, "auth_mode", "OIDC")  # a typo must not mean 'persona'
    with pytest.raises(RuntimeError):
        auth.validate_config()
    r = client.post("/ask", json={**Q, "user": "dana"})
    assert r.status_code == 500
    assert recorder.calls == []


def test_oidc_without_issuer_or_audience_refuses_to_start(oidc, monkeypatch):
    from provenance import auth
    from provenance.config import settings

    monkeypatch.setattr(settings, "oidc_issuer", "")
    with pytest.raises(RuntimeError):
        auth.validate_config()


@pytest.mark.parametrize("algs", ["HS256", "none", "RS256,HS256", ""])
def test_symmetric_or_empty_algorithm_list_refuses_to_start(oidc, monkeypatch, algs):
    from provenance import auth
    from provenance.config import settings

    monkeypatch.setattr(settings, "oidc_algorithms", algs)
    with pytest.raises(RuntimeError):
        auth.validate_config()


# --- delegation: who is acting for the subject ------------------------------
#
# The `act` claim (RFC 8693) is the only source of the chain the ledger records.
# Two properties matter: the chain reaches the pipeline exactly as the issuer
# signed it, and it never changes whose entitlements apply.

def test_direct_call_records_an_empty_chain_not_a_missing_one(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers=bearer(make_token()))
    assert r.status_code == 200
    assert recorder.calls[0]["delegation"] == []


def test_persona_mode_has_no_verified_chain_to_record(client, recorder):
    r = client.post("/ask", json={**Q, "user": "dana"})
    assert r.status_code == 200
    assert recorder.calls[0]["delegation"] is None


def test_single_actor(oidc, client, make_token, recorder):
    r = client.post("/ask", json=Q, headers=bearer(make_token(act={"sub": "agent-billing"})))
    assert r.status_code == 200
    assert recorder.calls[0]["delegation"] == [{"sub": "agent-billing"}]


def test_three_hops_upstream_are_recorded_in_order_caller_first(oidc, client, make_token, recorder):
    act = {"sub": "agent-c", "iss": "https://agents.example.test/",
           "act": {"sub": "agent-b", "act": {"sub": "scheduler-a"}}}
    r = client.post("/ask", json=Q, headers=bearer(make_token(act=act)))
    assert r.status_code == 200
    assert recorder.calls[0]["delegation"] == [
        {"sub": "agent-c", "iss": "https://agents.example.test/"},
        {"sub": "agent-b"},
        {"sub": "scheduler-a"},
    ]


def test_actor_does_not_change_whose_entitlements_apply(oidc, client, make_token, recorder):
    """An actor that names groups of its own gets none of them: entitlements are the
    subject's, and only identity members of `act` are read."""
    act = {"sub": "agent-c", "groups": ["hr-comp", "cp-admin"]}
    r = client.post("/ask", json=Q, headers=bearer(make_token(act=act)))
    assert r.status_code == 200
    call = recorder.calls[0]
    assert call["user"] == "user-123"
    assert call["verified_groups"] == ["public", "engineering"]
    assert call["delegation"] == [{"sub": "agent-c"}]


def test_actor_with_admin_group_does_not_reach_the_admin_plane(oidc, client, make_token):
    act = {"sub": "agent-c", "groups": ["cp-admin"]}
    r = client.get("/api/verify", headers=bearer(make_token(act=act)))
    assert r.status_code == 403


def test_body_cannot_supply_a_chain(oidc, client, make_token, recorder):
    body = {**Q, "delegation": [{"sub": "someone-else"}], "act": {"sub": "someone-else"}}
    r = client.post("/ask", json=body, headers=bearer(make_token()))
    assert r.status_code == 200
    assert recorder.calls[0]["delegation"] == []


@pytest.mark.parametrize("act", [
    "agent-c",                                   # not an object
    ["agent-c"],
    {},                                          # no sub
    {"sub": ""},
    {"sub": 7},
    {"sub": "agent-c", "iss": 7},
    {"sub": "agent-c", "iss": ""},
    {"sub": "agent-c", "act": "agent-b"},        # malformed further up the chain
    {"sub": "agent-c", "act": {"iss": "x"}},
])
def test_malformed_chain_is_refused_not_recorded_as_direct(oidc, client, make_token, recorder, act):
    r = client.post("/ask", json=Q, headers=bearer(make_token(act=act)))
    assert r.status_code == 401
    assert recorder.calls == []


def test_chain_deeper_than_the_limit_is_refused_not_truncated(oidc, client, make_token, recorder):
    from provenance.auth import MAX_DELEGATION_DEPTH

    def nest(depth):
        node = {"sub": "hop-0"}
        for i in range(1, depth):
            node = {"sub": f"hop-{i}", "act": node}
        return node

    ok = client.post("/ask", json=Q, headers=bearer(make_token(act=nest(MAX_DELEGATION_DEPTH))))
    assert ok.status_code == 200
    assert len(recorder.calls[0]["delegation"]) == MAX_DELEGATION_DEPTH

    recorder.calls.clear()
    r = client.post("/ask", json=Q, headers=bearer(make_token(act=nest(MAX_DELEGATION_DEPTH + 1))))
    assert r.status_code == 401
    assert recorder.calls == []
