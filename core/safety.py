"""Small shared safety policies for exploratory browser actions."""
from __future__ import annotations

import re

_SAFE_BUTTON = re.compile(
    r"^(open|view|show|expand|learn|explore|browse|next|previous|back|"
    r"menu|options|details|tab|start learning|start exploring)\b", re.I)
_RISKY_BUTTON = re.compile(
    r"\b(delete|remove|logout|log out|sign out|buy|purchase|pay|checkout|"
    r"submit|send|save|create|register|sign up|publish|invite|transfer|"
    r"place order|confirm)\b", re.I)


def is_safe_exploration_button(name: str) -> bool:
    """Only click UI controls that clearly reveal/navigate content.

    Actions with ambiguous or consequential wording are left for a human or
    for a specifically authorized, requirement-grounded test.
    """
    label = " ".join((name or "").split())
    return bool(label and _SAFE_BUTTON.search(label)
                and not _RISKY_BUTTON.search(label))
