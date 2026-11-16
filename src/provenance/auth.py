"""Authentication: who is calling, proven by a signed token.

Identity used to arrive in the request body (`{"user": "dana"}`), which made
row-level security a control over *claimed* identity. This module moves the
claim out of the caller's hands: the principal is derived from a bearer JWT
issued by an OIDC identity provider, verified here, and handed to retrieval as an
explicit list of groups. Nothing in the request body can widen it.

Two modes, chosen by AUTH_MODE:

  persona   Legacy. Identity comes from the request body and personas.yaml.
            For local development, the eval gate and the demo console only.
  oidc      Identity comes from a verified JWT. The body `user` field is
            rejected, `/api/personas` disappears, and the evidence/usage/verify
            endpoints require the admin group. The CLI is under the same rule:
            `resolve_cli_identity` refuses --user and takes the token from
            $CONTROLPLANE_TOKEN, so there is no entry point left where
            identity is claimed rather than proven.

Design rules, all of which fail closed:

  * Only asymmetric algorithms are accepted, and the list is pinned in config.
    The token's own `alg` header never selects the verifier, which closes the
    "alg: none" and RS256->HS256 key-confusion attacks.
  * `exp`, `iat`, `iss`, `aud` and `sub` are required, not merely checked if present.
  * Any verification failure is a 401 with a generic body. The reason goes to the
    server log, not to the caller.
  * An unknown AUTH_MODE refuses to start rather than falling back to persona.
  * A missing or malformed groups claim means no groups, which means no rows.

Delegation. When an agent acts for someone else, the token says so in the `act`
claim (RFC 8693, OAuth 2.0 Token Exchange): `sub` is the party the call is made on
behalf of, `act.sub` is the agent making it, and each nested `act` is the actor one
hop further upstream. The chain is read from the verified token only and written to
the evidence ledger; nothing in the request can add, drop or reorder a hop.

  * Entitlements still come from `sub` and its groups. The chain is recorded, not
    used to widen access; RFC 8693 section 4.1 says the nested actors are
    informational and must not drive access decisions.
  * A token with no `act` claim is a direct call and records an empty chain.
  * A malformed `act` claim is a 401, not an empty chain. Quietly dropping a hop
    the issuer asserted would turn a delegated call into a direct one in the audit
    record, which is the exact thing the record exists to prevent.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

log = logging.getLogger("provenance.auth")

# Asymmetric only. HS* is excluded on purpose: with a shared secret, anyone who can
# verify a token can also mint one, and it enables key-confusion attacks.
ASYMMETRIC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)
REQUIRED_CLAIMS = ("exp", "iat", "iss", "aud", "sub")

# How many hops of `act` nesting are accepted. A deeper chain is refused rather than
# truncated: a truncated chain is a false record.
MAX_DELEGATION_DEPTH = 8

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Actor:
    """One hop of a delegation chain: a party acting for the token's subject."""

    sub: str
    iss: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {"sub": self.sub} if self.iss is None else {"sub": self.sub, "iss": self.iss}


@dataclass(frozen=True)
class Principal:
    """A verified caller. `groups` are entitlements and never include the admin group.

    `subject` is who the call is for. `delegation` is who is making it, when that is
    someone else: the current actor first, then each earlier actor in turn. Empty
    means the subject called directly.
    """

    subject: str
    groups: tuple[str, ...] = ()
    is_admin: bool = False
    claims: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    delegation: tuple[Actor, ...] = ()

    def delegation_record(self) -> list[dict[str, str]]:
        """The chain in the form the evidence ledger stores and hashes."""
        return [a.as_dict() for a in self.delegation]


class KeyResolver(Protocol):
    def signing_key(self, token: str) -> Any: ...


class JWKSResolver:
    """Resolve the signing key from the IdP's JWKS endpoint, matched on the token's `kid`.

    PyJWKClient caches keys and re-fetches when it meets an unknown `kid`, which is
    how key rotation is picked up without a restart.
    """

    def __init__(self, jwks_url: str) -> None:
        self._client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5)

    def signing_key(self, token: str) -> Any:
        return self._client.get_signing_key_from_jwt(token).key


_resolver: KeyResolver | None = None


def set_key_resolver(resolver: KeyResolver | None) -> None:
    """Install a resolver. Tests use this to supply a local key without a network."""
    global _resolver
    _resolver = resolver


def _get_resolver() -> KeyResolver:
    global _resolver
    if _resolver is None:
        _resolver = JWKSResolver(settings.oidc_jwks_url)
    return _resolver


def allowed_algorithms() -> list[str]:
    return [a.strip() for a in settings.oidc_algorithms.split(",") if a.strip()]


def validate_config() -> None:
    """Called at startup. A misconfigured auth layer must stop the service, not weaken it."""
    mode = settings.auth_mode
    if mode == "persona":
        log.warning(
            "AUTH_MODE=persona: identity is taken from the request body. "
            "Development only; do not expose this instance."
        )
        return
    if mode != "oidc":
        raise RuntimeError(f"Unknown AUTH_MODE {mode!r}. Use 'persona' or 'oidc'.")
    missing = [
        name for name, value in (
            ("OIDC_ISSUER", settings.oidc_issuer),
            ("OIDC_AUDIENCE", settings.oidc_audience),
            ("OIDC_JWKS_URL", settings.oidc_jwks_url),
        ) if not value
    ]
    if missing and _resolver is None:
        raise RuntimeError(f"AUTH_MODE=oidc requires {', '.join(missing)}.")
    if not settings.oidc_issuer or not settings.oidc_audience:
        raise RuntimeError("AUTH_MODE=oidc requires OIDC_ISSUER and OIDC_AUDIENCE.")
    algs = allowed_algorithms()
    bad = [a for a in algs if a not in ASYMMETRIC_ALGORITHMS]
    if not algs or bad:
        raise RuntimeError(
            f"OIDC_ALGORITHMS must be a non-empty subset of asymmetric algorithms; got {algs!r}."
        )


def groups_from_claims(claims: dict[str, Any]) -> tuple[list[str], bool]:
    """Map token claims to (entitlement groups, is_admin).

    The groups claim may be a list or a single string. Non-string members are
    dropped. A missing claim yields no groups, so the caller sees no rows.
    """
    raw = claims.get(settings.oidc_groups_claim)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return [], False
    seen: list[str] = []
    for g in raw:
        if isinstance(g, str) and g and g not in seen:
            seen.append(g)
    is_admin = bool(settings.oidc_admin_group) and settings.oidc_admin_group in seen
    entitlements = [g for g in seen if g != settings.oidc_admin_group]
    return entitlements, is_admin


def delegation_from_claims(claims: dict[str, Any]) -> tuple[Actor, ...]:
    """Read the RFC 8693 `act` chain: current actor first, earliest actor last.

    No `act` claim means a direct call and an empty chain. Anything present but not
    well formed raises, so the token is rejected: see the module docstring for why a
    malformed chain is not treated as an absent one.
    """
    if "act" not in claims:
        return ()
    chain: list[Actor] = []
    node: Any = claims["act"]
    while True:
        if not isinstance(node, dict):
            raise jwt.InvalidTokenError("act must be a JSON object")
        if len(chain) >= MAX_DELEGATION_DEPTH:
            raise jwt.InvalidTokenError(
                f"act chain is deeper than {MAX_DELEGATION_DEPTH} hops"
            )
        actor_sub = node.get("sub")
        if not isinstance(actor_sub, str) or not actor_sub:
            raise jwt.InvalidTokenError("act.sub must be a non-empty string")
        actor_iss = node.get("iss")
        if actor_iss is not None and (not isinstance(actor_iss, str) or not actor_iss):
            raise jwt.InvalidTokenError("act.iss must be a non-empty string when present")
        chain.append(Actor(sub=actor_sub, iss=actor_iss))
        if "act" not in node:
            return tuple(chain)
        node = node["act"]


def verify_token(token: str) -> Principal:
    """Verify signature and registered claims. Raises jwt.PyJWTError on any failure."""
    key = _get_resolver().signing_key(token)
    claims = jwt.decode(
        token,
        key,
        algorithms=allowed_algorithms(),
        audience=settings.oidc_audience,
        issuer=settings.oidc_issuer,
        leeway=settings.oidc_leeway_seconds,
        options={"require": list(REQUIRED_CLAIMS)},
    )
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise jwt.InvalidTokenError("sub must be a non-empty string")
    groups, is_admin = groups_from_claims(claims)
    return Principal(
        subject=subject, groups=tuple(groups), is_admin=is_admin, claims=claims,
        delegation=delegation_from_claims(claims),
    )


CLI_TOKEN_ENV = "CONTROLPLANE_TOKEN"


def resolve_cli_identity(user: str | None) -> tuple[str | None, list[str] | None]:
    """Identity for a CLI entry point, honoring AUTH_MODE the way the API does.

    persona mode: the claimed user passes through and groups resolve from the
    persona table downstream — development only, same trust as the request body.
    oidc mode: --user is refused, the token comes from $CONTROLPLANE_TOKEN, and
    the verified subject and groups are returned. Same verifier, same pinned
    algorithms, same generic failure as /ask: the CLI is not a side door.

    Raises SystemExit rather than returning a weaker identity on any failure.
    """
    user, groups, _delegation = resolve_cli_caller(user)
    return user, groups


def resolve_cli_caller(
    user: str | None,
) -> tuple[str | None, list[str] | None, list[dict[str, str]] | None]:
    """`resolve_cli_identity` plus the delegation chain, for the evidence ledger.

    The third value is None in persona mode, where identity is claimed and there is
    no verified chain to record, and a list (empty for a direct call) in oidc mode.
    """
    mode = settings.auth_mode
    if mode == "persona":
        return user, None, None
    if mode != "oidc":
        raise SystemExit(f"Unknown AUTH_MODE {mode!r}. Use 'persona' or 'oidc'.")
    if user is not None:
        raise SystemExit(
            "AUTH_MODE=oidc: identity comes from the verified token; --user is not accepted."
        )
    token = os.environ.get(CLI_TOKEN_ENV, "")
    if not token:
        raise SystemExit(f"AUTH_MODE=oidc: set {CLI_TOKEN_ENV} to a bearer token.")
    try:
        principal = verify_token(token)
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        # Operators get the reason in the log; the caller learns only that it failed.
        log.info("token rejected: %s: %s", type(exc).__name__, exc)
        raise SystemExit("Authentication failed.") from None
    return principal.subject, list(principal.groups), principal.delegation_record()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Authentication required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal | None:
    """FastAPI dependency. None means persona mode; otherwise a verified Principal."""
    mode = settings.auth_mode
    if mode == "persona":
        return None
    if mode != "oidc":
        raise HTTPException(status_code=500, detail="Authentication is misconfigured.")
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise _unauthorized()
    try:
        principal = verify_token(creds.credentials)
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        # The reason is for operators. The caller learns only that it failed.
        log.info("token rejected: %s: %s", type(exc).__name__, exc)
        raise _unauthorized() from None
    request.state.principal = principal
    return principal


def require_admin(principal: Principal | None = Depends(current_principal)) -> Principal | None:
    """Gate for the evidence, usage and verify endpoints.

    Those endpoints read through the owner connection, which RLS does not constrain,
    so they sit behind a stronger check than `/ask`. In persona mode they stay open,
    as before, because persona mode is development only.
    """
    if principal is None:
        return None
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return principal
