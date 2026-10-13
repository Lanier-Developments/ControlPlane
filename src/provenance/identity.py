"""Persona to groups. A stand-in for the identity provider.

In a real deployment these come from the OIDC token or a directory lookup. Keeping
resolution behind one function means Phase 5 swaps the source without touching
retrieval.
"""
from functools import lru_cache
from pathlib import Path

import yaml

from .config import settings


class UnknownPrincipal(Exception):
    pass


@lru_cache
def _table() -> dict[str, list[str]]:
    return yaml.safe_load(Path(settings.personas_file).read_text()) or {}


def groups_for(user: str | None) -> list[str]:
    """Resolve entitlements. An unknown or missing principal gets nothing, not everything."""
    if settings.auth_mode != "persona":
        # Once a real identity provider is configured, the stand-in is not a source on
        # any path — the API hides /api/personas, and this is the same rule for code.
        raise RuntimeError(
            f"AUTH_MODE={settings.auth_mode}: the persona table is not an identity "
            "source; groups must come from a verified token."
        )
    if not user:
        return []
    table = _table()
    if user not in table:
        raise UnknownPrincipal(user)
    return list(table[user])
