"""Shared state model for the persistent Evolution Controller (closed-loop WP1).

This module owns the job/step/trigger/capability vocabulary and the legal
transition table so every layer (store, controller, API, tests) agrees on the
same state machine.  The job lifecycle is deliberately narrower than the full
closed-loop plan (which spans WP2-WP6): WP1 only wraps the existing
``auto_propose`` call in a durable, idempotent, recoverable job.  Later work
packages extend ``STEP_*`` and ``JOB_*`` without changing these constants.
"""
from typing import Dict, FrozenSet, Set


# --- Trigger types ---------------------------------------------------------
TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULED = "scheduled"
TRIGGER_EVENT = "event"
TRIGGER_TYPES: FrozenSet[str] = frozenset(
    {TRIGGER_MANUAL, TRIGGER_SCHEDULED, TRIGGER_EVENT}
)

# --- Capability kinds ------------------------------------------------------
CAPABILITY_PROMPT = "prompt"
CAPABILITY_RULE_SKILL = "rule_skill"
CAPABILITY_KINDS: FrozenSet[str] = frozenset(
    {CAPABILITY_PROMPT, CAPABILITY_RULE_SKILL}
)

# --- Job status ------------------------------------------------------------
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"
JOB_PAUSED = "paused"
JOB_CANCELLED = "cancelled"
JOB_STATUSES: FrozenSet[str] = frozenset(
    {JOB_PENDING, JOB_RUNNING, JOB_COMPLETED, JOB_FAILED, JOB_PAUSED, JOB_CANCELLED}
)

# A job is "active" if it has not reached a terminal outcome yet.
ACTIVE_JOB_STATUSES: FrozenSet[str] = frozenset(
    {JOB_PENDING, JOB_RUNNING, JOB_PAUSED}
)
TERMINAL_JOB_STATUSES: FrozenSet[str] = frozenset(
    {JOB_COMPLETED, JOB_FAILED, JOB_CANCELLED}
)

# --- Steps (checkpoint markers) -------------------------------------------
STEP_COLLECTING = "collecting"
STEP_EVALUATING = "evaluating"
STEP_DONE = "done"
STEP_STATUSES: FrozenSet[str] = frozenset(
    {STEP_COLLECTING, STEP_EVALUATING, STEP_DONE}
)

# --- Legal status transitions ---------------------------------------------
# ``resume`` is modelled as pending->running, so it is not listed here as a
# distinct transition; the controller resets paused/failed back to pending and
# then acquires a lease.
TRANSITIONS: Dict[str, Set[str]] = {
    JOB_PENDING: {JOB_RUNNING, JOB_CANCELLED},
    JOB_RUNNING: {JOB_COMPLETED, JOB_FAILED, JOB_PAUSED, JOB_CANCELLED},
    JOB_FAILED: {JOB_PENDING, JOB_CANCELLED},       # retry resets to pending
    JOB_PAUSED: {JOB_PENDING, JOB_CANCELLED},       # resume resets to pending
    JOB_COMPLETED: set(),
    JOB_CANCELLED: set(),
}


def is_valid_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def build_idempotency_key(
    tenant_id: str, capability_kind: str, capability_name: str,
    trigger_type: str, trigger_ref: str = "",
) -> str:
    """Deterministic idempotency key for a job.

    Same tenant + capability + trigger + trigger_ref must collapse to the same
    key so duplicate events never create duplicate jobs.  trigger_ref is the
    caller's signal fingerprint (e.g. a corroborated Experience fingerprint
    set hash); empty for a plain manual trigger.
    """
    normalized = "|".join(
        (tenant_id or "default", capability_kind or "", capability_name or "",
         trigger_type or "", trigger_ref or "")
    )
    return normalized
