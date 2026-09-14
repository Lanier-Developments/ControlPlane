"""Demo mode: the guardrails a publicly reachable instance needs.

Three exposures when this is on the open internet, and each has a different fix:

**Spend.** Anyone with the URL can put questions through the gateway, and a cloud
provider bills for every one. Demo mode restricts which registry entries may be
requested — self-hosted only by default — so an open URL cannot spend money. The
routing story still demonstrates: the DENY on a cloud model is exactly what visitors
should see.

**Free text.** Arbitrary questions mean arbitrary prompts, arbitrary length, and
arbitrary content sent to a model. Canned mode accepts only the questions the console
ships with, which removes the category entirely.

**Volume.** A rate limit per client, in memory. Adequate for a single instance behind
a tunnel; a real deployment would put this at the edge, where it belongs.

Everything here is off by default. Local development stays unrestricted, and the
production question — where these controls actually belong — is a deployment concern
rather than something to bake into the application.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import settings

# client key -> timestamps of recent requests. Bounded by the window, not by callers,
# so a wide spread of source addresses grows this; acceptable for a demo instance,
# not a substitute for edge rate limiting.
_HITS: dict[str, deque] = defaultdict(deque)
_WINDOW = 60.0


def demo_models() -> set[str]:
    return {m.strip() for m in settings.demo_allowed_models.split(",") if m.strip()}


def client_key(request: Request) -> str:
    """Prefer the tunnel's forwarded address, since every request otherwise shares one IP."""
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate(request: Request) -> None:
    if not settings.demo_mode or settings.demo_rate_per_minute <= 0:
        return
    key = client_key(request)
    now = time.monotonic()
    hits = _HITS[key]
    while hits and now - hits[0] > _WINDOW:
        hits.popleft()
    if len(hits) >= settings.demo_rate_per_minute:
        retry = int(_WINDOW - (now - hits[0])) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Demo rate limit: {settings.demo_rate_per_minute} questions per minute. "
                   f"Try again in {retry}s, or run it locally — the repo is linked above.",
            headers={"Retry-After": str(retry)},
        )
    hits.append(now)


def check_model(requested: str | None) -> None:
    """Refuse a model the demo instance is not willing to pay for."""
    if not settings.demo_mode or not requested:
        return
    if requested not in demo_models():
        raise HTTPException(
            status_code=403,
            detail=f"{requested} is disabled on the hosted demo (it bills a real account). "
                   f"Self-hosted models are available. The cloud routing figures are in "
                   f"docs/FINDINGS.md.",
        )


def check_question(question: str, canned: set[str]) -> None:
    """In canned mode, only the console's own example questions are accepted."""
    if not settings.demo_mode or not settings.demo_canned_only:
        return
    if question.strip() not in canned:
        raise HTTPException(
            status_code=403,
            detail="The hosted demo answers only its example questions. "
                   "Clone the repo to ask your own.",
        )


def status() -> dict:
    """What the console needs to render its own restrictions honestly."""
    return {
        "demo_mode": settings.demo_mode,
        "canned_only": settings.demo_mode and settings.demo_canned_only,
        "allowed_models": sorted(demo_models()) if settings.demo_mode else None,
        "rate_per_minute": settings.demo_rate_per_minute if settings.demo_mode else None,
    }
