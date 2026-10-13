"""The CLI is not a side door around OIDC.

test_auth.py proves the API cannot be handed a claimed identity in oidc mode. These
tests prove the same for the other entry point: `python -m provenance.chain` resolves
identity through `auth.resolve_cli_identity`, which honors AUTH_MODE exactly as the
API does — and the persona table itself refuses to answer once a real identity
provider is configured, so no future code path can quietly consult it.
"""
from __future__ import annotations

import time

import pytest

from conftest import ISSUER  # noqa: F401  (session path setup happens on import)


def resolve(user=None):
    from provenance.auth import resolve_cli_identity

    return resolve_cli_identity(user)


# --- persona mode is unchanged ------------------------------------------------

def test_persona_mode_passes_the_claimed_user_through(monkeypatch):
    from provenance.config import settings

    monkeypatch.setattr(settings, "auth_mode", "persona")
    assert resolve("sam") == ("sam", None)
    assert resolve(None) == (None, None)


# --- oidc mode: identity comes from the token ---------------------------------

def test_valid_token_yields_subject_and_groups(oidc, monkeypatch, make_token):
    monkeypatch.setenv("CONTROLPLANE_TOKEN", make_token())
    assert resolve() == ("user-123", ["public", "engineering"])


def test_admin_group_is_not_an_entitlement_here_either(oidc, monkeypatch, make_token):
    monkeypatch.setenv("CONTROLPLANE_TOKEN", make_token(groups=["public", "cp-admin"]))
    assert resolve() == ("user-123", ["public"])


def test_missing_groups_claim_means_no_groups(oidc, monkeypatch, make_token):
    monkeypatch.setenv("CONTROLPLANE_TOKEN", make_token(groups=None))
    assert resolve() == ("user-123", [])


def test_user_flag_is_refused_even_with_a_valid_token(oidc, monkeypatch, make_token):
    monkeypatch.setenv("CONTROLPLANE_TOKEN", make_token())
    with pytest.raises(SystemExit):
        resolve("dana")


def test_no_token_is_a_refusal_not_a_pass(oidc, monkeypatch):
    monkeypatch.delenv("CONTROLPLANE_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        resolve()


def test_forged_signature_is_refused(oidc, monkeypatch, make_token, other_keypair):
    forged = make_token(key=other_keypair, groups=["hr-comp"], sub="dana")
    monkeypatch.setenv("CONTROLPLANE_TOKEN", forged)
    with pytest.raises(SystemExit):
        resolve()


def test_expired_token_is_refused(oidc, monkeypatch, make_token):
    past = int(time.time()) - 3600
    monkeypatch.setenv("CONTROLPLANE_TOKEN", make_token(iat=past - 60, exp=past))
    with pytest.raises(SystemExit):
        resolve()


def test_failure_reason_is_not_disclosed(oidc, monkeypatch, make_token, other_keypair):
    messages = set()
    for token in (make_token(exp=int(time.time()) - 999),
                  make_token(key=other_keypair),
                  "not-a-jwt"):
        monkeypatch.setenv("CONTROLPLANE_TOKEN", token)
        with pytest.raises(SystemExit) as exc:
            resolve()
        messages.add(str(exc.value))
    assert messages == {"Authentication failed."}


def test_unknown_auth_mode_refuses(monkeypatch):
    from provenance.config import settings

    monkeypatch.setattr(settings, "auth_mode", "OIDC")  # a typo must not mean 'persona'
    with pytest.raises(SystemExit):
        resolve()


# --- the stand-in itself fails closed ------------------------------------------

def test_persona_table_refuses_to_serve_in_oidc_mode(oidc):
    from provenance.identity import groups_for

    with pytest.raises(RuntimeError):
        groups_for("dana")
