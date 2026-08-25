"""Evolution budget & deduplication guard.

Self-evolution must not explode into unbounded candidates (plan section 10.6).
The guard caps how many candidates / replays / activations happen per day,
deduplicates structurally identical candidates, enforces a cooldown, and
black-lists hypotheses that have repeatedly failed.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvolutionBudget:
    max_candidates_per_day: int = 20
    max_replay_cases: int = 200
    max_evaluation_cost: float = 1e6        # abstract cost units
    max_active_experiments: int = 5
    max_activations_per_day: int = 5
    cooldown_seconds: float = 3600.0        # seconds before re-proposing
    blacklist_hypotheses: List[str] = field(default_factory=list)


class BudgetDenied(Exception):
    """Raised when an evolution action is denied by the budget guard."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class BudgetDecision:
    allowed: bool
    reason: str = ""


class EvolutionBudgetGuard:
    """Tracks usage and enforces the evolution budget."""

    def __init__(self, budget: Optional[EvolutionBudget] = None):
        self.budget = budget or EvolutionBudget()
        self._candidate_signatures: set = set()
        self._candidates_today: List[str] = []
        self._replays_used: int = 0
        self._activations_today: List[str] = []
        self._active_experiments: set = set()
        self._failed_hypotheses: Dict[str, int] = {}
        self._last_activation_at: Dict[str, float] = {}
        self._evaluation_cost: float = 0.0

    # -- candidate registration ---------------------------------------------

    def register_candidate(self, candidate_id: str, hypothesis_id: Optional[str] = None):
        decision = self.check_candidate(candidate_id, hypothesis_id)
        if not decision.allowed:
            raise BudgetDenied(decision.reason)
        self._candidates_today.append(candidate_id)

    def check_candidate(self, candidate_id: str,
                        hypothesis_id: Optional[str] = None) -> BudgetDecision:
        reasons = []
        if hypothesis_id and hypothesis_id in self.budget.blacklist_hypotheses:
            reasons.append(f"hypothesis {hypothesis_id} is black-listed")
        if hypothesis_id:
            fails = self._failed_hypotheses.get(hypothesis_id, 0)
            if fails >= 3:
                reasons.append(f"hypothesis {hypothesis_id} failed {fails}x")
        if len(self._candidates_today) >= self.budget.max_candidates_per_day:
            reasons.append("candidate-per-day cap reached")
        return BudgetDecision(allowed=not reasons, reason="; ".join(reasons))

    # -- replay -------------------------------------------------------------

    def register_replay(self, cases: int = 1):
        if self._replays_used + cases > self.budget.max_replay_cases:
            raise BudgetDenied("replay-case budget exhausted")
        self._replays_used += cases

    # -- evaluation cost ----------------------------------------------------

    def reserve_cost(self, amount: float):
        if self._evaluation_cost + amount > self.budget.max_evaluation_cost:
            raise BudgetDenied("evaluation cost budget exhausted")
        self._evaluation_cost += amount

    # -- active experiments -------------------------------------------------

    def begin_experiment(self, experiment_id: str):
        if len(self._active_experiments) >= self.budget.max_active_experiments:
            raise BudgetDenied("too many active experiments")
        self._active_experiments.add(experiment_id)

    def close_experiment(self, experiment_id: str):
        self._active_experiments.discard(experiment_id)

    # -- activation / cooldown / blacklist ----------------------------------

    def record_activation(self, candidate_id: str, now: float = 0.0):
        if len(self._activations_today) >= self.budget.max_activations_per_day:
            raise BudgetDenied("activation-per-day cap reached")
        if candidate_id in self._last_activation_at:
            last = self._last_activation_at[candidate_id]
            if 0.0 < now - last < self.budget.cooldown_seconds:
                raise BudgetDenied("candidate is still within cooldown")
        self._activations_today.append(candidate_id)
        self._last_activation_at[candidate_id] = now

    def record_hypothesis_failure(self, hypothesis_id: str):
        self._failed_hypotheses[hypothesis_id] = \
            self._failed_hypotheses.get(hypothesis_id, 0) + 1

    def blacklist_default_failed(self) -> List[str]:
        """Auto-black-list hypotheses that have failed at least 3 times."""
        auto = [hid for hid, fails in self._failed_hypotheses.items() if fails >= 3]
        for hypothesis in auto:
            if hypothesis not in self.budget.blacklist_hypotheses:
                self.budget.blacklist_hypotheses.append(hypothesis)
        return auto

    # -- dedup --------------------------------------------------------------

    def dedupe_signature(self, signature: str) -> bool:
        """Return True if the signature is new; adds it if so."""
        if signature in self._candidate_signatures:
            return False
        self._candidate_signatures.add(signature)
        return True

    def seen_signature(self, signature: str) -> bool:
        return signature in self._candidate_signatures