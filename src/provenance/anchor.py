"""Signed checkpoints of the evidence ledger's head hash.

The hash chain is tamper-evident only against someone who cannot rewrite the whole
table. Three attacks leave a perfectly valid chain behind:

  - rewriting every row from the edit onward (recomputing each hash in order)
  - deleting the newest rows (a shorter chain is still a valid chain)
  - restoring the table from a tampered backup

All three work because nothing outside the database remembers what the head hash was.
An anchor is that memory: a small signed record {seq, entry_hash, count} appended to a
file that lives where the database owner cannot write it (a git repository, an
object-locked bucket, a separate host). `verify_anchors` then checks that every anchored
row is still in the database with exactly the hash that was signed.

What this does and does not buy:

  - Anything anchored cannot be rewritten, removed or rolled back without detection.
  - Entries appended after the latest anchor are protected only by the chain, so the
    window of exposure is the anchoring interval. Anchor on a schedule you can defend.
  - The anchors are chained (each carries the SHA-256 of the previous line) and signed
    with an Ed25519 key the database host never holds, so deleting or editing an anchor
    line is itself detectable. Truncating the anchor file's *tail* is not, any more than
    truncating the ledger's was — the file's own custody is the control there.
  - The signing key is the new root of trust. Whoever holds it can sign a false anchor.

This module is pure: it takes rows and a lookup function, never a connection, so the
logic is tested without a database.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

GENESIS_ANCHOR = "0" * 64
SIGNED_FIELDS = ("version", "seq", "entry_hash", "count", "anchored_at", "prev_anchor_sha256")


def _canonical(anchor: dict) -> bytes:
    body = {k: anchor[k] for k in SIGNED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def line_sha256(line: str) -> str:
    return hashlib.sha256(line.strip().encode()).hexdigest()


def generate_keypair(directory: Path) -> tuple[Path, Path]:
    """Write ledger-anchor.pem (private, 0600) and ledger-anchor.pub (public)."""
    directory.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    priv = directory / "ledger-anchor.pem"
    pub = directory / "ledger-anchor.pub"
    priv.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    priv.chmod(0o600)
    pub.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    return priv, pub


def load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("anchor key must be Ed25519")
    return key


def load_public(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(Path(path).read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("anchor public key must be Ed25519")
    return key


def make_anchor(*, seq: int, entry_hash: str, count: int, prev_anchor_sha256: str,
                key: Ed25519PrivateKey, now: datetime | None = None) -> dict:
    anchor = {
        "version": 1,
        "seq": seq,
        "entry_hash": entry_hash,
        "count": count,
        "anchored_at": (now or datetime.now(timezone.utc)).isoformat(),
        "prev_anchor_sha256": prev_anchor_sha256,
    }
    anchor["signature"] = base64.b64encode(key.sign(_canonical(anchor))).decode()
    return anchor


def read_anchor_lines(path: Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    return [ln for ln in p.read_text().splitlines() if ln.strip()]


def previous_anchor_sha256(path: Path) -> str:
    lines = read_anchor_lines(path)
    return line_sha256(lines[-1]) if lines else GENESIS_ANCHOR


def append_anchor(path: Path, anchor: dict) -> None:
    line = json.dumps(anchor, sort_keys=True, separators=(",", ":"))
    with open(path, "a") as f:
        f.write(line + "\n")


def verify_anchors(
    lines: list[str],
    public_key: Ed25519PublicKey,
    lookup: Callable[[list[int]], dict[int, str]],
    head_seq: int | None,
) -> dict:
    """Check every anchor against the database.

    `lookup(seqs)` returns {seq: entry_hash} for the rows that exist.
    `head_seq` is the highest seq in the ledger, or None if it is empty.

    Returns {"ok": True, "anchors": n, "latest_seq": s} or
            {"ok": False, "anchor": i, "why": "..."} for the first problem.
    """
    if not lines:
        return {"ok": False, "anchor": 0, "why": "no anchors found"}

    anchors: list[dict] = []
    prev_sha = GENESIS_ANCHOR
    for i, line in enumerate(lines, start=1):
        try:
            a = json.loads(line)
            sig = base64.b64decode(a["signature"])
            public_key.verify(sig, _canonical(a))
        except (InvalidSignature, KeyError, ValueError, TypeError):
            return {"ok": False, "anchor": i, "why": "signature invalid or anchor malformed"}
        if a["prev_anchor_sha256"] != prev_sha:
            return {"ok": False, "anchor": i,
                    "why": "anchor file was edited: an earlier anchor was removed or changed"}
        if anchors and (a["seq"] < anchors[-1]["seq"] or a["count"] < anchors[-1]["count"]):
            return {"ok": False, "anchor": i, "why": "anchors go backwards in seq or count"}
        anchors.append(a)
        prev_sha = line_sha256(line)

    found = lookup([a["seq"] for a in anchors])
    for i, a in enumerate(anchors, start=1):
        have = found.get(a["seq"])
        if have is None:
            if head_seq is None or head_seq < a["seq"]:
                why = (f"ledger ends at seq {head_seq}, before anchored seq {a['seq']}: "
                       "newest entries were deleted, the table was truncated, or it was "
                       "restored from an older backup")
            else:
                why = f"anchored entry seq {a['seq']} no longer exists"
            return {"ok": False, "anchor": i, "why": why}
        if have != a["entry_hash"]:
            return {"ok": False, "anchor": i,
                    "why": f"entry seq {a['seq']} has a different hash than the one signed: "
                           "history was rewritten"}

    return {"ok": True, "anchors": len(anchors), "latest_seq": anchors[-1]["seq"]}
