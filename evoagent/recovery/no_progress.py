"""Detect agent no-progress loops (e.g. repeated identical tool calls)."""
import json
from typing import Any, Dict, List, Optional


def _stable_args(arguments: Any) -> str:
    try:
        return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"),
                          default=str)
    except (TypeError, ValueError):
        return str(arguments)


class NoProgressDetector:
    """Detect when an agent repeats the same action without making progress."""

    def __init__(self, window: int = 5, max_duplicates: int = 3):
        self.window = max(2, window)
        self.max_duplicates = max(2, max_duplicates)

    @staticmethod
    def fingerprint(action: Dict[str, Any]) -> str:
        return "%s|%s|%s" % (
            str(action.get("action", "")),
            str(action.get("tool", "")),
            _stable_args(action.get("arguments")),
        )

    def detect(self, actions: List[Dict[str, Any]]) -> bool:
        """Return True when the agent is looping without meaningful progression."""
        recent = [self.fingerprint(item) for item in actions[-self.window:]]
        if not recent:
            return False
        # The most recent N actions are all identical.
        if len(recent) >= self.max_duplicates and len(set(recent[-self.max_duplicates:])) == 1:
            return True
        # Alternating A/B/A/B with no new tool or final action.
        return self._is_two_cycle(recent)

    @staticmethod
    def _is_two_cycle(recent: List[str]) -> bool:
        if len(recent) < 4:
            return False
        if len(set(recent)) > 2:
            return False
        for idx in range(len(recent)):
            if recent[idx] != recent[idx % 2]:
                return False
        return True