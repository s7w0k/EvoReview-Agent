"""Outcome feedback wiring for evolvable memory (plan section 14.3).

After a review, the Outcome tells us whether the memories that were used helped:

* a verified + accepted finding -> ``helpful`` (usefulness up);
* a rejected finding / false positive -> ``unhelpful`` (usefulness down).

This module tracks which memories each task actually used and applies the
outcome to those entries -- so memory usefulness is driven by real production
outcomes, not synthetic labels.
"""
from typing import Dict, List, Optional

from ..outcome_evolution.outcome import Outcome, OutcomeKind

# Outcome kinds that increase usefulness.
_HELPFUL_KINDS = {
    OutcomeKind.TASK_SUCCESS,
    OutcomeKind.FINDING_ACCEPTED,
    OutcomeKind.FIX_ACCEPTED,
}
# Outcome kinds that decrease usefulness.
_UNHELPFUL_KINDS = {
    OutcomeKind.FINDING_REJECTED,
    OutcomeKind.FALSE_POSITIVE,
}


class MemoryUseTracker:
    """Records the memory ids used by each task."""

    def __init__(self):
        self._used: Dict[str, List[str]] = {}

    def note(self, task_id: str, memory_ids: List[str]) -> None:
        bucket = self._used.setdefault(task_id, [])
        for memory_id in memory_ids:
            if memory_id not in bucket:
                bucket.append(memory_id)

    def used(self, task_id: str) -> List[str]:
        return list(self._used.get(task_id, []))

    def clear(self, task_id: str) -> None:
        self._used.pop(task_id, None)


class MemoryOutcomeFeedback:
    """Applies a production outcome to the memories a task used."""

    def __init__(self, manager, tracker: Optional[MemoryUseTracker] = None):
        # ``manager`` is a memory_evolution.MemoryManager (confirm_helpful /
        # confirm_unhelpful).  Imported lazily to avoid a hard dependency.
        self._manager = manager
        self._tracker = tracker or MemoryUseTracker()

    def note_used(self, task_id: str, memory_ids: List[str]) -> None:
        self._tracker.note(task_id, memory_ids)

    def apply(self, outcome: Outcome) -> List[str]:
        """Update usefulness of the used memories and return the ids touched."""
        used = self._tracker.used(outcome.task_id)
        if not used:
            return []
        touched = []
        for memory_id in used:
            if self._apply_one(outcome, memory_id):
                touched.append(memory_id)
        self._tracker.clear(outcome.task_id)
        return touched

    def _apply_one(self, outcome: Outcome, memory_id: str) -> bool:
        try:
            if outcome.kind in _HELPFUL_KINDS:
                self._manager.confirm_helpful(memory_id)
                return True
            if outcome.kind in _UNHELPFUL_KINDS:
                self._manager.confirm_unhelpful(memory_id)
                return True
        except KeyError:
            # The memory was used but is no longer present; ignore.
            return False
        # Neutral outcomes (task_failure, safety signals) do not move usefulness.
        return False