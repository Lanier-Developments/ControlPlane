"""Connections. Two roles, deliberately.

owner   migrations and ingest. Exempt from row-level security.
app     retrieval only. NOBYPASSRLS, SELECT-only.

Retrieval must never borrow the owner connection. That single rule is what makes the
permission story checkable rather than aspirational.
"""
from __future__ import annotations

from contextlib import contextmanager

import psycopg

from .config import settings


def _dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@contextmanager
def owner_conn():
    with psycopg.connect(_dsn(settings.database_url)) as conn:
        yield conn


@contextmanager
def app_conn(groups: list[str]):
    """Open a retrieval connection scoped to `groups` for one transaction.

    SET LOCAL means the scope dies with the transaction, so a pooled connection can
    never carry one caller's entitlements into the next caller's query. Empty groups
    is allowed and returns nothing — an unauthenticated caller sees no rows rather
    than triggering a special case in Python.
    """
    with psycopg.connect(_dsn(settings.app_database_url)) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.groups', %s, true)", (",".join(groups),))
            yield conn


def assert_rls_enforced() -> None:
    """Startup check: prove the app role is actually constrained.

    Guards against the failure that matters most — someone points APP_DATABASE_URL at
    the owner role and every policy silently stops applying.
    """
    with app_conn([]) as conn:
        role, bypass = conn.execute(
            "SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        if bypass:
            raise RuntimeError(f"Retrieval role '{role}' bypasses RLS. Point APP_DATABASE_URL at rag_app.")
        visible = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        if visible:
            raise RuntimeError(f"Role '{role}' sees {visible} chunks with no groups set. RLS is not enforcing.")
