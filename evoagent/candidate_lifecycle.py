"""Unified candidate lifecycle (closed-loop WP4).

Prompt and declarative Skill candidates share one state machine and one
actor-permission model.  The richer states are additive: the legacy
``draft -> validated -> active`` path still exists for the development
compatibility profile, while the production profile routes every promotion
through the deployment controller.

Actors:
- ``evaluator``: only ``evaluating -> validated/rejected``.
- ``deployment_controller``: only ``validated -> shadow -> canary -> active``.
- ``admin`` / ``rollback_policy``: only rollbacks into ``rolled_back`` (and
  emergency restoration of a historical version).
- ``candidate_builder`` / ``agent`` / ``reflection``: never activate or promote.
"""
from typing import FrozenSet, Optional

from . import skill_lifecycle as lifecycle

ACTOR_EVALUATOR = "evaluator"
ACTOR_DEPLOYMENT = "deployment_controller"
ACTOR_ADMIN = "admin"
ACTOR_ROLLBACK = "rollback_policy"
ACTOR_BUILDER = "candidate_builder"
ACTOR_AGENT = "agent"
ACTOR_REFLECTION = "reflection"

ACTORS: FrozenSet[str] = frozenset({
    ACTOR_EVALUATOR, ACTOR_DEPLOYMENT, ACTOR_ADMIN, ACTOR_ROLLBACK,
    ACTOR_BUILDER, ACTOR_AGENT, ACTOR_REFLECTION,
})

# The unified state machine delegates to skill_lifecycle (single source of truth).
def is_valid_status(status: str) -> bool:
    return lifecycle.is_valid(status)


def can_transition(source: str, target: str) -> bool:
    return lifecycle.can_transition(source, target)


def permitted(
    actor: str, source: str, target: str, *, is_historical: bool = False,
) -> bool:
    """Whether ``actor`` may move a candidate ``source -> target``."""
    if not lifecycle.can_transition(source, target):
        return False
    if actor == ACTOR_EVALUATOR:
        return (source, target) in {
            (lifecycle.DRAFT, lifecycle.EVALUATING),
            (lifecycle.EVALUATING, lifecycle.VALIDATED),
            (lifecycle.EVALUATING, lifecycle.REJECTED),
        }
    if actor == ACTOR_DEPLOYMENT:
        return (source, target) in {
            (lifecycle.VALIDATED, lifecycle.SHADOW),
            (lifecycle.SHADOW, lifecycle.CANARY),
            (lifecycle.CANARY, lifecycle.ACTIVE),
        }
    if actor in (ACTOR_ADMIN, ACTOR_ROLLBACK):
        if target == lifecycle.ROLLED_BACK and source in {
            lifecycle.SHADOW, lifecycle.CANARY, lifecycle.ACTIVE,
        }:
            return True
        # Emergency restoration of a previously-activated historical version.
        if (
            actor == ACTOR_ADMIN and is_historical
            and (source, target) == (lifecycle.VALIDATED, lifecycle.ACTIVE)
        ):
            return True
        return False
    # candidate_builder / agent / reflection have no promotion or activation.
    return False


def is_promotion_actor(actor: str) -> bool:
    return actor in {ACTOR_EVALUATOR, ACTOR_DEPLOYMENT}


def is_rollback_actor(actor: str) -> bool:
    return actor in {ACTOR_ADMIN, ACTOR_ROLLBACK}
