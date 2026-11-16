"""The evidence ledger: append-only, hash-chained record of every answered question.

What an answer has to be able to prove, after the fact and without the original session:

  who asked, who was acting for them, what they were entitled to, which chunks were
  retrieved, which model ran, which policy rule permitted it, and that the record has
  not been altered since.

Design choices worth stating:

**Digests, not answers.** The ledger stores a SHA-256 of the answer, not the answer.
Storing answers would recreate, in an append-only table that cannot be deleted from, a
copy of exactly the confidential content the permission layer works to contain. The
digest proves what was said to anyone who still has the text; it discloses nothing to
anyone who does not. Same reasoning for chunk ids instead of chunk text.

**Refusals are entries.** A refused question is evidence — arguably the more important
kind, because it is how you demonstrate the controls fired rather than that they were
merely configured. A ledger holding only successes proves nothing about enforcement.

**The chain is tamper-evident, not tamper-proof.** Each entry hashes its own content
plus the previous entry's hash, so altering or removing a past row invalidates every
hash after it. Anyone who can rewrite the entire table in order can still forge a
consistent chain. Detecting quiet edits is the goal; defeating a determined operator
with database ownership is not, and claiming otherwise would be dishonest.

**The delegation chain is inside the hash.** `principal` is who the call was for;
`delegation` is who made it when that was someone else, current actor first, taken
from the verified token's `act` claim (see auth.py). It has three states and they mean
different things: a list of actors is a delegated call, an empty list is a verified
direct call, and NULL means no verified chain existed to record (persona mode, or an
entry written before the column did). NULL is left out of the hashed payload, which
is what lets entries written before this field keep verifying; any non-NULL value is
hashed, so a hop cannot be added to, removed from or reordered in a past entry
without breaking the chain.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from .db import owner_conn

GENESIS = "0" * 64


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def entry_digest(payload: dict, prev_hash: str) -> str:
    """Hash over canonical JSON plus the previous hash.

    sort_keys makes the digest independent of dict ordering, so the same entry always
    hashes the same way — otherwise verification would fail on a Python version change
    rather than on tampering.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(f"{prev_hash}\x00{canonical}")


def entry_payload(
    *,
    entry_id: str,
    correlation_id: str,
    principal: str | None,
    groups: list[str],
    question: str,
    classification: str,
    retrieved: list[dict],
    model_id: str | None,
    rule_id: str | None,
    decision: str,
    answer_sha256: str | None,
    cost_usd,
    delegation: list[dict] | None,
) -> dict:
    """The content an entry's hash covers. One builder for `record` and `verify`, so
    the two cannot drift into hashing different things."""
    payload = {
        "entry_id": entry_id,
        "correlation_id": correlation_id,
        "principal": principal,
        "groups": sorted(groups),
        "question": question,
        "classification": classification,
        "retrieved": retrieved,
        "model_id": model_id,
        "rule_id": rule_id,
        "decision": decision,
        "answer_sha256": answer_sha256,
        "cost_usd": None if cost_usd is None else f"{cost_usd:.6f}",
    }
    # Absent, not null, when there is no chain to record: entries written before the
    # delegation column existed were hashed without this key and must still verify.
    if delegation is not None:
        payload["delegation"] = delegation
    return payload


@dataclass
class Entry:
    entry_id: str
    seq: int
    entry_hash: str


def record(
    *,
    correlation_id: str,
    principal: str | None,
    groups: list[str],
    question: str,
    classification: str,
    retrieved: list[dict],
    decision: str,
    model_id: str | None = None,
    rule_id: str | None = None,
    answer: str | None = None,
    cost_usd: float | None = None,
    delegation: list[dict] | None = None,
) -> Entry:
    """Append one entry. Serialized so concurrent writers cannot fork the chain.

    `delegation` is the verified actor chain, current actor first: a list for a
    verified call (empty when the principal called directly), None when there is no
    verified chain to record.
    """
    entry_id = str(uuid.uuid4())
    answer_hash = sha256(answer) if answer is not None else None

    with owner_conn() as conn:
        # Lock the table for the duration of the append. Two concurrent writers reading
        # the same tail hash would produce two entries claiming the same predecessor,
        # and the chain would be unverifiable through that point. Appends are rare
        # relative to reads, so the contention cost is acceptable; a high-throughput
        # deployment would use a sequence-ordered chain built by a single writer.
        conn.execute("LOCK TABLE evidence IN EXCLUSIVE MODE")
        row = conn.execute("SELECT entry_hash FROM evidence ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = row[0] if row else GENESIS

        payload = entry_payload(
            entry_id=entry_id, correlation_id=correlation_id, principal=principal,
            groups=groups, question=question, classification=classification,
            retrieved=retrieved, model_id=model_id, rule_id=rule_id, decision=decision,
            answer_sha256=answer_hash, cost_usd=cost_usd, delegation=delegation,
        )
        entry_hash = entry_digest(payload, prev_hash)

        seq = conn.execute(
            """
            INSERT INTO evidence (entry_id, correlation_id, principal, groups, question,
                                  classification, retrieved, model_id, rule_id, decision,
                                  answer_sha256, cost_usd, delegation, prev_hash, entry_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING seq
            """,
            (entry_id, correlation_id, principal, sorted(groups), question, classification,
             json.dumps(retrieved), model_id, rule_id, decision, answer_hash,
             cost_usd, None if delegation is None else json.dumps(delegation),
             prev_hash, entry_hash),
        ).fetchone()[0]
        conn.commit()

    return Entry(entry_id=entry_id, seq=seq, entry_hash=entry_hash)


def head() -> tuple[int, str, int] | None:
    """(seq, entry_hash, row count) of the newest entry, or None for an empty ledger."""
    with owner_conn() as conn:
        row = conn.execute(
            "SELECT seq, entry_hash, (SELECT count(*) FROM evidence) "
            "FROM evidence ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    return (row[0], row[1], row[2]) if row else None


def verify(limit: int | None = None) -> dict:
    """Recompute the chain and report the first break.

    Recomputes each entry's digest from its stored content rather than trusting the
    stored hash, so an edited row is caught even if someone also updated its hash —
    that edit breaks the next entry's link instead.
    """
    with owner_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT seq, entry_id, correlation_id, principal, groups, question,
                   classification, retrieved, model_id, rule_id, decision,
                   answer_sha256, cost_usd, prev_hash, entry_hash, delegation
            FROM evidence ORDER BY seq {'DESC LIMIT ' + str(limit) if limit else ''}
            """
        ).fetchall()

    if limit:
        rows = list(reversed(rows))

    prev = GENESIS if not limit else (rows[0][13] if rows else GENESIS)
    checked = 0

    for row in rows:
        (seq, entry_id, correlation_id, principal, groups, question, classification,
         retrieved, model_id, rule_id, decision, answer_hash, cost_usd,
         stored_prev, stored_hash, delegation) = row

        if stored_prev != prev:
            return {"ok": False, "checked": checked, "broken_at": seq,
                    "why": "prev_hash does not match the previous entry"}

        payload = entry_payload(
            entry_id=str(entry_id), correlation_id=str(correlation_id),
            principal=principal, groups=groups, question=question,
            classification=classification, retrieved=retrieved, model_id=model_id,
            rule_id=rule_id, decision=decision, answer_sha256=answer_hash,
            cost_usd=cost_usd, delegation=delegation,
        )
        if entry_digest(payload, stored_prev) != stored_hash:
            return {"ok": False, "checked": checked, "broken_at": seq,
                    "why": "entry content does not match its hash"}

        prev = stored_hash
        checked += 1

    return {"ok": True, "checked": checked, "head": prev}
