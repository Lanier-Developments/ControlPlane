"""Ledger anchoring: the three attacks a valid hash chain cannot see.

No database needed. A ledger is modelled as {seq: entry_hash}, built with the same
entry_digest the real ledger uses, so "a consistent forged chain" below is genuinely
consistent: it passes the chain's own arithmetic and only the anchors expose it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from provenance import anchor as A  # noqa: E402
from provenance.evidence import GENESIS, entry_digest  # noqa: E402


def build_ledger(payloads: list[dict]) -> dict[int, str]:
    ledger, prev = {}, GENESIS
    for seq, p in enumerate(payloads, start=1):
        prev = entry_digest(p, prev)
        ledger[seq] = prev
    return ledger


def chain_is_valid(payloads: list[dict], ledger: dict[int, str]) -> bool:
    return build_ledger(payloads) == ledger


def check(lines, key, ledger):
    return A.verify_anchors(
        lines, key.public_key(),
        lambda seqs: {s: ledger[s] for s in seqs if s in ledger},
        max(ledger) if ledger else None,
    )


@pytest.fixture
def key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def payloads():
    return [{"q": f"question {i}", "decision": "answered"} for i in range(1, 11)]


def anchor_at(ledger, seq, key, prev=A.GENESIS_ANCHOR, lines=None):
    a = A.make_anchor(seq=seq, entry_hash=ledger[seq], count=seq,
                      prev_anchor_sha256=prev, key=key)
    return json.dumps(a, sort_keys=True, separators=(",", ":"))


def chained(ledger, key, seqs):
    lines, prev = [], A.GENESIS_ANCHOR
    for s in seqs:
        line = anchor_at(ledger, s, key, prev)
        lines.append(line)
        prev = A.line_sha256(line)
    return lines


def test_untouched_ledger_passes(key, payloads):
    ledger = build_ledger(payloads)
    out = check(chained(ledger, key, [3, 7, 10]), key, ledger)
    assert out == {"ok": True, "anchors": 3, "latest_seq": 10}


def test_full_chain_rewrite_is_caught_by_anchor_not_by_chain(key, payloads):
    ledger = build_ledger(payloads)
    lines = chained(ledger, key, [5, 10])
    forged = [dict(p) for p in payloads]
    forged[1]["decision"] = "refused_policy"       # edit row 2, then rehash everything after
    forged_ledger = build_ledger(forged)
    assert chain_is_valid(forged, forged_ledger)    # the chain alone is satisfied
    out = check(lines, key, forged_ledger)
    assert not out["ok"] and "rewritten" in out["why"]


def test_tail_truncation_is_caught(key, payloads):
    ledger = build_ledger(payloads)
    lines = chained(ledger, key, [4, 10])
    truncated = {s: h for s, h in ledger.items() if s <= 8}   # still a valid, shorter chain
    out = check(lines, key, truncated)
    assert not out["ok"] and "truncated" in out["why"]


def test_restore_from_older_backup_is_caught(key, payloads):
    ledger = build_ledger(payloads)
    lines = chained(ledger, key, [10])
    older = build_ledger(payloads[:6])
    assert not check(lines, key, older)["ok"]


def test_emptied_ledger_is_caught(key, payloads):
    ledger = build_ledger(payloads)
    out = check(chained(ledger, key, [10]), key, {})
    assert not out["ok"] and "truncated" in out["why"]


def test_deleted_middle_row_is_caught(key, payloads):
    ledger = build_ledger(payloads)
    lines = chained(ledger, key, [5])
    gone = {s: h for s, h in ledger.items() if s != 5}
    out = check(lines, key, gone)
    assert not out["ok"] and "no longer exists" in out["why"]


def test_appends_after_the_latest_anchor_are_fine(key, payloads):
    ledger = build_ledger(payloads)
    lines = chained(ledger, key, [6])
    assert check(lines, key, ledger)["ok"]          # rows 7-10 are not yet anchored


def test_removing_an_anchor_line_is_caught(key, payloads):
    ledger = build_ledger(payloads)
    lines = chained(ledger, key, [3, 6, 9])
    del lines[1]
    out = check(lines, key, ledger)
    assert not out["ok"] and "edited" in out["why"]


def test_editing_an_anchor_is_caught(key, payloads):
    ledger = build_ledger(payloads)
    lines = chained(ledger, key, [5, 10])
    doc = json.loads(lines[1])
    doc["entry_hash"] = "f" * 64
    lines[1] = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    assert "signature" in check(lines, key, ledger)["why"]


def test_anchor_signed_with_the_wrong_key_is_rejected(key, payloads):
    ledger = build_ledger(payloads)
    lines = chained(ledger, Ed25519PrivateKey.generate(), [10])
    assert not check(lines, key, ledger)["ok"]


def test_no_anchors_is_a_failure_not_a_pass(key, payloads):
    assert not check([], key, build_ledger(payloads))["ok"]


def test_file_roundtrip_chains_prev_hash(tmp_path, key, payloads):
    ledger = build_ledger(payloads)
    f = tmp_path / "anchors.jsonl"
    for s in (4, 9):
        A.append_anchor(f, A.make_anchor(
            seq=s, entry_hash=ledger[s], count=s,
            prev_anchor_sha256=A.previous_anchor_sha256(f), key=key))
    assert check(A.read_anchor_lines(f), key, ledger)["ok"]


def test_keygen_roundtrip(tmp_path):
    priv, pub = A.generate_keypair(tmp_path)
    assert oct(priv.stat().st_mode & 0o777) == "0o600"
    k = A.load_private(priv)
    a = A.make_anchor(seq=1, entry_hash="a" * 64, count=1,
                      prev_anchor_sha256=A.GENESIS_ANCHOR, key=k)
    line = json.dumps(a, sort_keys=True, separators=(",", ":"))
    assert A.verify_anchors([line], A.load_public(pub),
                            lambda s: {1: "a" * 64}, 1)["ok"]
