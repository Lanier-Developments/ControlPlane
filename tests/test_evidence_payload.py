"""What the ledger's hash covers, checked without a database.

Two claims are pinned here. Adding the delegation chain did not change the digest of
any entry written before it, so an existing ledger still verifies. And once a chain
is recorded it is inside the hash: adding, dropping, reordering or renaming a hop
changes the digest, which is what `verify` turns into a broken chain.
"""
from __future__ import annotations

import hashlib
import json

from conftest import ISSUER  # noqa: F401  (session path setup happens on import)

BASE = dict(
    entry_id="11111111-1111-1111-1111-111111111111",
    correlation_id="22222222-2222-2222-2222-222222222222",
    principal="dana", groups=["public", "hr-comp"],
    question="What is the salary band for a Senior Engineer II?",
    classification="confidential",
    retrieved=[{"doc_id": "HR-007", "version": 2, "chunk_id": 3,
                "classification": "confidential"}],
    model_id="llama3.1:8b", rule_id="confidential-local-only", decision="answered",
    answer_sha256="ab" * 32, cost_usd=0.0,
)
PREV = "cd" * 32


def digest(**overrides) -> str:
    from provenance.evidence import entry_digest, entry_payload

    return entry_digest(entry_payload(**{**BASE, "delegation": None, **overrides}), PREV)


def test_entry_without_a_chain_hashes_exactly_as_it_did_before_the_field_existed():
    # The pre-delegation payload, written out literally rather than built by the
    # code under test: twelve keys, no `delegation`.
    legacy = {
        "entry_id": BASE["entry_id"], "correlation_id": BASE["correlation_id"],
        "principal": "dana", "groups": ["hr-comp", "public"],
        "question": BASE["question"], "classification": "confidential",
        "retrieved": BASE["retrieved"], "model_id": "llama3.1:8b",
        "rule_id": "confidential-local-only", "decision": "answered",
        "answer_sha256": BASE["answer_sha256"], "cost_usd": "0.000000",
    }
    canonical = json.dumps(legacy, sort_keys=True, separators=(",", ":"), default=str)
    expected = hashlib.sha256(f"{PREV}\x00{canonical}".encode()).hexdigest()
    assert digest() == expected


def test_direct_and_unrecorded_are_different_records():
    assert digest(delegation=[]) != digest(delegation=None)


def test_every_change_to_a_chain_changes_the_digest():
    chain = [{"sub": "agent-c"}, {"sub": "agent-b"}, {"sub": "scheduler-a"}]
    base = digest(delegation=chain)
    tampered = {
        "hop removed": chain[:2],
        "chain erased to direct": [],
        "chain erased to unrecorded": None,
        "hop added": chain + [{"sub": "someone"}],
        "hops reordered": [chain[1], chain[0], chain[2]],
        "actor renamed": [{"sub": "agent-x"}, *chain[1:]],
        "issuer added": [{"sub": "agent-c", "iss": "https://other.example/"}, *chain[1:]],
    }
    for what, value in tampered.items():
        assert digest(delegation=value) != base, what


def test_digest_survives_the_jsonb_round_trip():
    """Postgres returns jsonb objects with its own key order; the digest must not care."""
    stored = [{"sub": "agent-c", "iss": "https://agents.example.test/"}]
    returned = [{"iss": "https://agents.example.test/", "sub": "agent-c"}]
    assert digest(delegation=stored) == digest(delegation=returned)


def test_trace_line_tells_the_three_states_apart():
    from provenance.audit import describe_delegation

    assert describe_delegation(None).startswith("not recorded")
    assert describe_delegation([]).startswith("direct")
    line = describe_delegation([{"sub": "agent-c", "iss": "https://a/"}, {"sub": "agent-b"}])
    assert line.startswith("agent-c @ https://a/ <- agent-b")
