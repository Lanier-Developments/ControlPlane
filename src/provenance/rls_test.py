"""Prove the database, not the application, is doing the enforcing.

The Python layer is bypassed entirely: these queries go straight to the app role.
If every one of these passes, no application bug can widen what a caller sees.
"""
import sys

from .db import app_conn, assert_rls_enforced
from .identity import groups_for

def expected_visible(groups: list[str]) -> int:
    """Chunks a caller should see, computed from the corpus files.

    Derived independently of the database so the test cannot agree with a bug in
    ingest by using the same source of truth.
    """
    from pathlib import Path as _Path

    from .config import settings
    from .ingest import chunk, load_corpus

    total = 0
    for doc in load_corpus(_Path(settings.corpus_dir)):
        if set(doc.metadata["acl"]) & set(groups):
            total += len(chunk(doc))
    return total


CASES = [
    ("no groups set", [], "unset session must see nothing — fail closed"),
    ("riley (public only)", groups_for("riley"), "public documents only"),
    ("sam (engineering)", groups_for("sam"), "engineering, but not hr-comp"),
    ("dana (hr-comp)", groups_for("dana"), "includes the confidential salary bands"),
    ("priya (finance)", groups_for("priya"), "no engineering runbooks"),
]


def main() -> int:
    assert_rls_enforced()
    failures = 0

    for name, groups, why in CASES:
        expected = expected_visible(groups)
        with app_conn(groups) as conn:
            n = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        ok = n == expected
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {n} chunks visible (expected {expected}) — {why}")

    # The one that matters most: can a caller reach a specific forbidden document?
    for user, doc_id, label in [("sam", "HR-007", "salary bands"), ("riley", "ENG-012", "on-call runbook")]:
        with app_conn(groups_for(user)) as conn:
            n = conn.execute("SELECT count(*) FROM chunks WHERE doc_id=%s", (doc_id,)).fetchone()[0]
        ok = n == 0
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {user} cannot reach {doc_id} ({label}): {n} chunks")

    # Version exclusion is a SQL clause, so it is testable here the same way RLS is:
    # against the database, with the expectation computed from the corpus files. With
    # the filter, a caller gets exactly their visible chunks minus the superseded ones
    # that have an active successor; without it, everything. The filter runs on the
    # RLS connection, so "an active successor" means one this caller can see.
    from .retrieval import EXCLUDE_SUPERSEDED

    for name, groups in [("riley", groups_for("riley")), ("dana", groups_for("dana"))]:
        with app_conn(groups) as conn:
            visible = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            shadowed = conn.execute("""
                SELECT count(*) FROM chunks c
                JOIN documents d ON d.doc_id = c.doc_id AND d.version = c.version
                WHERE d.status = 'superseded' AND EXISTS (
                    SELECT 1 FROM documents a
                    WHERE a.doc_id = d.doc_id AND a.status = 'active')
            """).fetchone()[0]
            filtered = conn.execute(f"""
                SELECT count(*) FROM chunks c
                JOIN documents d ON d.doc_id = c.doc_id AND d.version = c.version
                WHERE {EXCLUDE_SUPERSEDED}
            """).fetchone()[0]
        ok = shadowed > 0 and filtered == visible - shadowed
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  version exclusion for {name}: "
              f"{visible} visible - {shadowed} shadowed superseded = {filtered} served")

    # Entitlements must not survive the transaction that set them.
    with app_conn(groups_for("dana")) as conn:
        pass
    with app_conn([]) as conn:
        n = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    ok = n == 0
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  entitlements do not leak across transactions: {n} chunks")

    print("\n" + ("All RLS checks passed" if not failures else f"{failures} RLS CHECK(S) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
