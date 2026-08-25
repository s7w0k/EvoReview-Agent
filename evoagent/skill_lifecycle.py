"""Skill artifact lifecycle states and legal transitions.

This module is the single source of truth for the compatible lifecycle model:

    draft -> validated -> active
       \\-> rejected
    validated <-> active
    active / validated / rejected -> archived

``validated -> active`` is allowed so that rolling back to a previously
activated historical version stays possible.  A replaced ``active`` version
returns to ``validated`` (still able to be re-activated) rather than being
archived directly.

State meanings:
- draft:      freshly saved, evaluation not yet complete.
- validated:  passed replay, can be activated or used as a rollback target.
- active:     the currently running version.
- rejected:   failed a gate, must not be activated.
- archived:   explicitly archived by an operator, not part of runtime and not
              directly activatable.
"""
DRAFT = "draft"
QUARANTINED = "quarantined"
EVALUATING = "evaluating"
VALIDATED = "validated"
SHADOW = "shadow"
CANARY = "canary"
ACTIVE = "active"
STALE = "stale"
REJECTED = "rejected"
ROLLED_BACK = "rolled_back"
ARCHIVED = "archived"

STATUSES = (
    DRAFT, QUARANTINED, EVALUATING, VALIDATED, SHADOW, CANARY, ACTIVE,
    STALE, REJECTED, ROLLED_BACK, ARCHIVED,
)

# source -> legal direct targets.  The legacy entries (draft->validated/rejected,
# validated->active/archived, active->validated/archived) are preserved so the
# compatible activation/rollback path stays unchanged; the richer states are
# additive and only used by the unified candidate lifecycle (WP4).
_TRANSITIONS = {
    DRAFT: (QUARANTINED, EVALUATING, VALIDATED, REJECTED),
    QUARANTINED: (EVALUATING, REJECTED),
    EVALUATING: (VALIDATED, REJECTED),
    VALIDATED: (SHADOW, ACTIVE, ARCHIVED),
    SHADOW: (CANARY, ROLLED_BACK, ARCHIVED),
    CANARY: (ACTIVE, ROLLED_BACK, ARCHIVED),
    ACTIVE: (VALIDATED, STALE, ROLLED_BACK, ARCHIVED),
    STALE: (ARCHIVED,),
    REJECTED: (ARCHIVED,),
    ROLLED_BACK: (VALIDATED, ARCHIVED),
    ARCHIVED: (),
}

# Dark switch.  When disabled, activation reverts to the legacy behaviour
# (no lifecycle gating) so the runtime path is unchanged from before WP2.
_ENABLED = True


def set_enabled(value: bool) -> None:
    global _ENABLED
    _ENABLED = bool(value)


def enabled() -> bool:
    return _ENABLED


def is_valid(status: str) -> bool:
    return status in STATUSES


def can_transition(source: str, target: str) -> bool:
    return is_valid(source) and target in _TRANSITIONS.get(source, ())


def is_activatable(status: str) -> bool:
    """A version may be activated directly only when validated or already active."""
    return status in (VALIDATED, ACTIVE)


def default_status(activate: bool) -> str:
    """Safe default for legacy save calls that do not pass an explicit status."""
    return ACTIVE if activate else VALIDATED