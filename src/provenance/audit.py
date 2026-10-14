"""Audit the evidence ledger.

    python -m provenance.audit verify          # recompute the whole chain
    python -m provenance.audit verify --anchors FILE --pubkey KEY.pub
                                               # ...and check it against signed anchors
    python -m provenance.audit keygen DIR      # make an Ed25519 anchor keypair
    python -m provenance.audit anchor          # append a signed checkpoint of the head
    python -m provenance.audit trace <id>      # one entry, or everything for a principal
    python -m provenance.audit tamper-demo     # prove the chain actually detects edits

The tamper demo matters more than it looks. A hash chain nobody has watched break is a
claim, not a control.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import anchor as anchors_mod
from .db import owner_conn
from .evidence import head, verify

DEFAULT_ANCHOR_FILE = "ledger-anchors.jsonl"


def _anchor_file(args) -> Path:
    return Path(args.anchors or os.environ.get("LEDGER_ANCHOR_FILE", DEFAULT_ANCHOR_FILE))


def _lookup(seqs: list[int]) -> dict[int, str]:
    with owner_conn() as conn:
        rows = conn.execute(
            "SELECT seq, entry_hash FROM evidence WHERE seq = ANY(%s)", (seqs,)
        ).fetchall()
    return {seq: h for seq, h in rows}


def cmd_verify(args) -> int:
    result = verify(limit=args.limit)
    if not result["ok"]:
        print(f"CHAIN BROKEN at seq {result['broken_at']} after {result['checked']} good entries")
        print(f"Reason: {result['why']}")
        return 1
    if result["checked"] == 0:
        print("Ledger is EMPTY: nothing to verify. An empty chain passes by construction, "
              "so this proves nothing about what used to be here.")
    else:
        print(f"Chain intact: {result['checked']} entries verified")
        print(f"Head: {result.get('head')}")

    if not args.anchors:
        print("Not checked against anchors: a rewritten, truncated or restored-from-backup "
              "ledger still passes. Use --anchors FILE --pubkey KEY.pub.")
        return 0

    pub = args.pubkey or os.environ.get("LEDGER_ANCHOR_PUBKEY")
    if not pub:
        print("--pubkey (or LEDGER_ANCHOR_PUBKEY) is required with --anchors")
        return 2
    h = head()
    out = anchors_mod.verify_anchors(
        anchors_mod.read_anchor_lines(Path(args.anchors)),
        anchors_mod.load_public(Path(pub)), _lookup, h[0] if h else None,
    )
    if out["ok"]:
        print(f"Anchors hold: {out['anchors']} signed checkpoints, latest at seq "
              f"{out['latest_seq']}; every anchored entry is present with its signed hash")
        return 0
    print(f"ANCHOR CHECK FAILED at anchor {out['anchor']}")
    print(f"Reason: {out['why']}")
    return 1


def cmd_keygen(args) -> int:
    priv, pub = anchors_mod.generate_keypair(Path(args.directory))
    print(f"private key  {priv}   (keep OFF the database host; anchoring needs it)")
    print(f"public key   {pub}   (verification needs only this)")
    return 0


def cmd_anchor(args) -> int:
    key_path = args.key or os.environ.get("LEDGER_ANCHOR_KEY")
    if not key_path:
        print("--key (or LEDGER_ANCHOR_KEY) is required: path to the Ed25519 private key")
        return 2
    result = verify()
    if not result["ok"]:
        print(f"Refusing to anchor a broken chain (seq {result['broken_at']}: {result['why']})")
        return 1
    h = head()
    if h is None:
        print("Ledger is empty; nothing to anchor.")
        return 1
    seq, entry_hash, count = h
    path = _anchor_file(args)
    a = anchors_mod.make_anchor(
        seq=seq, entry_hash=entry_hash, count=count,
        prev_anchor_sha256=anchors_mod.previous_anchor_sha256(path),
        key=anchors_mod.load_private(Path(key_path)),
    )
    anchors_mod.append_anchor(path, a)
    print(f"Anchored seq {seq} ({count} entries), head {entry_hash[:16]}… -> {path}")
    print("Now put that file somewhere the database owner cannot write "
          "(commit it elsewhere, upload with object lock).")
    return 0


def cmd_trace(args) -> int:
    with owner_conn() as conn:
        rows = conn.execute(
            """
            SELECT e.seq, e.occurred_at, e.principal, e.groups, e.question,
                   e.classification, e.decision, e.model_id, e.rule_id,
                   e.retrieved, e.answer_sha256, e.cost_usd, e.correlation_id
            FROM evidence e
            WHERE e.principal = %(id)s OR e.correlation_id::text = %(id)s
               OR e.entry_id::text = %(id)s
            ORDER BY e.seq DESC LIMIT %(limit)s
            """,
            {"id": args.id, "limit": args.limit},
        ).fetchall()

    if not rows:
        print(f"No evidence entries for {args.id}")
        return 1

    for (seq, at, principal, groups, question, classification, decision, model_id,
         rule_id, retrieved, answer_hash, cost, correlation_id) in rows:
        print(f"\n#{seq}  {at:%Y-%m-%d %H:%M:%S}  {decision}")
        print(f"  principal      {principal} ({', '.join(groups)})")
        print(f"  question       {question}")
        print(f"  classification {classification}")
        print(f"  model          {model_id or '—'}   rule: {rule_id or '—'}")
        refs = sorted({"{} v{}".format(r["doc_id"], r["version"]) for r in retrieved})
        print(f"  retrieved      {len(retrieved)} chunks: {', '.join(refs) or '—'}")
        print(f"  answer sha256  {answer_hash or '—'}")
        print(f"  cost           ${cost or 0:.6f}")
        print(f"  correlation    {correlation_id}")

        # The model call log is joined on correlation id, so a single answer's
        # retrieval decision and model decision can be reconstructed together.
        with owner_conn() as conn:
            calls = conn.execute(
                """SELECT requested_model, resolved_model, decision, reason, rule_id,
                          cache_hit, cost_usd, latency_ms
                   FROM model_calls WHERE correlation_id = %s ORDER BY occurred_at""",
                (correlation_id,),
            ).fetchall()
        for req, res, dec, reason, rid, hit, ccost, latency in calls:
            print(f"    call  {dec:5} {req} -> {res or '—'}  [{rid}] {reason}"
                  f"{'  (cache)' if hit else ''}  {latency or 0}ms")
    return 0


def cmd_tamper_demo(args) -> int:
    """Edit a row behind the triggers, show the chain breaks, then restore it.

    Uses ALTER TABLE ... DISABLE TRIGGER, which is deliberately awkward: rewriting
    history should require an act nobody performs by accident.
    """
    before = verify()
    if not before["ok"]:
        print("Chain is already broken; fix that before running the demo.")
        return 1
    print(f"1. Chain intact: {before['checked']} entries verified")

    with owner_conn() as conn:
        row = conn.execute(
            "SELECT seq, question FROM evidence ORDER BY seq LIMIT 1"
        ).fetchone()
        if not row:
            print("Ledger is empty. Answer a question first.")
            return 1
        seq, original = row

        conn.execute("ALTER TABLE evidence DISABLE TRIGGER evidence_no_update")
        conn.execute("UPDATE evidence SET question = %s WHERE seq = %s",
                     ("[tampered] " + original, seq))
        conn.execute("ALTER TABLE evidence ENABLE TRIGGER evidence_no_update")
        conn.commit()
    print(f"2. Edited the question text of entry #{seq} directly in the database")

    after = verify()
    detected = not after["ok"]
    print(f"3. Verification: {'BROKEN at seq ' + str(after['broken_at']) if detected else 'still reports intact'}")
    if detected:
        print(f"   Reason: {after['why']}")

    with owner_conn() as conn:
        conn.execute("ALTER TABLE evidence DISABLE TRIGGER evidence_no_update")
        conn.execute("UPDATE evidence SET question = %s WHERE seq = %s", (original, seq))
        conn.execute("ALTER TABLE evidence ENABLE TRIGGER evidence_no_update")
        conn.commit()

    restored = verify()
    print(f"4. Restored original text; chain {'intact again' if restored['ok'] else 'STILL BROKEN'}")

    if detected and restored["ok"]:
        print("\nPASS  the chain detects a silent edit and the edit is reversible only "
              "by restoring the exact original bytes")
        return 0
    print("\nFAIL  tamper detection did not behave as expected")
    return 1


def cmd_append_only(args) -> int:
    """Confirm the triggers actually refuse UPDATE, DELETE and TRUNCATE.

    Every statement runs in a transaction that is always rolled back, so if a trigger is
    missing this check reports the failure instead of committing the damage it probes for.
    """
    import psycopg

    failures = 0
    for statement, label in [
        ("UPDATE evidence SET question = 'x' WHERE seq = (SELECT min(seq) FROM evidence)", "UPDATE"),
        ("DELETE FROM evidence WHERE seq = (SELECT min(seq) FROM evidence)", "DELETE"),
        ("TRUNCATE evidence", "TRUNCATE"),
    ]:
        with owner_conn() as conn:
            try:
                conn.execute(statement)
                print(f"  FAIL  {label} was permitted (rolled back)")
                failures += 1
            except psycopg.errors.RaiseException:
                print(f"  PASS  {label} refused by trigger")
            finally:
                conn.rollback()
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Evidence ledger audit")
    sub = ap.add_subparsers(dest="command", required=True)

    v = sub.add_parser("verify", help="recompute the hash chain")
    v.add_argument("--limit", type=int, default=None, help="verify only the last N entries")
    v.add_argument("--anchors", default=None, help="anchor file to check the ledger against")
    v.add_argument("--pubkey", default=None, help="Ed25519 public key that signed the anchors")
    v.set_defaults(func=cmd_verify)

    k = sub.add_parser("keygen", help="generate an Ed25519 keypair for signing anchors")
    k.add_argument("directory")
    k.set_defaults(func=cmd_keygen)

    an = sub.add_parser("anchor", help="append a signed checkpoint of the ledger head")
    an.add_argument("--key", default=None, help="Ed25519 private key (or LEDGER_ANCHOR_KEY)")
    an.add_argument("--anchors", default=None,
                    help=f"anchor file (or LEDGER_ANCHOR_FILE; default {DEFAULT_ANCHOR_FILE})")
    an.set_defaults(func=cmd_anchor)

    t = sub.add_parser("trace", help="entries by principal, correlation id, or entry id")
    t.add_argument("id")
    t.add_argument("--limit", type=int, default=5)
    t.set_defaults(func=cmd_trace)

    sub.add_parser("tamper-demo", help="prove the chain detects a silent edit").set_defaults(
        func=cmd_tamper_demo
    )
    sub.add_parser("append-only", help="confirm UPDATE, DELETE and TRUNCATE are refused").set_defaults(
        func=cmd_append_only
    )

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
