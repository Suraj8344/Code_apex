"""Classify common browser access challenges separately from app failures."""
from __future__ import annotations


CHALLENGE_MARKERS = (
    "performing security verification",
    "verify you are human",
    "checking your browser",
    "security verification",
    "incompatible browser extension",
    "challenge-platform",
    "cf-challenge",
    "captcha",
)


def access_block_reason(status: int, title: str, body: str) -> str:
    """Return a cautious access-block notice for explicit anti-bot challenges.

    A plain 401/403 can be a legitimate product response. Only classify it as
    an access challenge when the visible page also contains challenge cues.
    """
    if status not in {401, 403, 429}:
        return ""
    visible = f"{title} {body}".lower()
    if not any(marker in visible for marker in CHALLENGE_MARKERS):
        return ""
    return (
        f"The target returned HTTP {status} with a browser security or bot "
        "verification challenge. QArena could not inspect the actual application, "
        "so this run cannot confirm whether the site has bugs."
    )
