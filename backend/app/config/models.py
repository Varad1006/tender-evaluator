"""Role-based model resolver, ported from the bidder-side Tender AI `config/models.py` pattern.

Business logic asks for a model by *role*, never by name. This module is the single source of
model IDs for this system (its own config, not a shared import with the bidder-side tool).
Replace the defaults with the IDs the existing system currently pins; each role can also be
overridden per environment with TE_MODEL_<ROLE>.
"""

from __future__ import annotations

import os

# Cheap default / concrete-critical / on-demand escalation tiers.
_DEFAULTS: dict[str, str] = {
    "extraction_default": "gemini-2.5-flash",
    "extraction_critical": "gemini-2.5-pro",
    "judge": "gemini-2.5-flash",
    "escalation": "gemini-2.5-pro",
    # OpenRouter roles. `reader` does the grounded reading for every bidder; `critical` is the
    # second opinion a negative has to survive before it is shown to a committee, and the model a
    # low-confidence reading is escalated to. Overridable per environment, as above.
    "reader": os.environ.get("TE_MODEL_READER", "google/gemini-2.5-flash"),
    "critical": os.environ.get("TE_MODEL_CRITICAL", "anthropic/claude-sonnet-4.5"),
}


def get_model(role: str) -> str:
    if role not in _DEFAULTS:
        raise KeyError(f"Unknown model role '{role}'. Known roles: {', '.join(sorted(_DEFAULTS))}")
    return os.environ.get(f"TE_MODEL_{role.upper()}", _DEFAULTS[role])


def roles() -> dict[str, str]:
    return {role: get_model(role) for role in _DEFAULTS}
