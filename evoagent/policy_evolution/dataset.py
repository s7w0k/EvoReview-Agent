"""Replay dataset splitting for policy evolution (plan section 11.5).

Snapshots are split into ``train / validation / holdout / temporal_holdout``
following a deterministic rule.  The critical constraint is that candidate
*generation* must never see the holdout sets -- so the split exposes a
``generation_pool`` (train + validation) plus strictly separated evaluation
sets (``holdout`` and ``temporal_holdout``).
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..replay.models import ReplaySnapshot

# Per-snapshot timestamp accessor (overridable for deterministic tests).
_TIMESTAMP = Callable[[ReplaySnapshot], float]


@dataclass
class DatasetSplit:
    """A partitioned replay dataset (plan section 11.5)."""

    train: List[ReplaySnapshot] = field(default_factory=list)
    validation: List[ReplaySnapshot] = field(default_factory=list)
    holdout: List[ReplaySnapshot] = field(default_factory=list)
    temporal_holdout: List[ReplaySnapshot] = field(default_factory=list)
    config: Dict[str, float] = field(default_factory=dict)

    @property
    def generation_pool(self) -> List[ReplaySnapshot]:
        """Train + validation -- the only pool candidate generation may use."""
        return list(self.train) + list(self.validation)

    def __len__(self) -> int:
        return (len(self.train) + len(self.validation)
                + len(self.holdout) + len(self.temporal_holdout))


def split_dataset(
    snapshots: List[ReplaySnapshot],
    *,
    train_ratio: float = 0.7,
    validation_ratio: float = 0.1,
    holdout_ratio: float = 0.1,
    temporal_holdout_ratio: float = 0.1,
    task_key: Optional[Callable[[ReplaySnapshot], str]] = None,
    timestamp: Optional[_TIMESTAMP] = None,
) -> DatasetSplit:
    """Split snapshots deterministically and returns the four partitions.

    The oldest ``temporal_holdout_ratio`` by timestamp (defaulting to the
    snapshots' recorded ``created_at``) is set aside so evolution is judged on
    genuinely future data.  The remaining snapshots are hashed by ``task_key``
    (defaulting to ``task_id``) so that identical tasks always land in the same
    partition -- no task ever leaks across train / validation / holdout.
    """
    ratios = [train_ratio, validation_ratio, holdout_ratio, temporal_holdout_ratio]
    if any(value < 0 for value in ratios) or sum(ratios) <= 0:
        raise ValueError("split ratios must be non-negative and sum > 0")

    get_time = timestamp or (lambda snap: snap.created_at)
    key_for = task_key or (lambda snap: snap.task_id)

    # Oldest subset -> temporal holdout (future-unseen data).
    ordered = sorted(snapshots, key=get_time)
    temporal_count = round(len(ordered) * temporal_holdout_ratio)
    temporal = ordered[:temporal_count] if temporal_count else []
    remainder = ordered[temporal_count:]

    # Deterministic, task-stable assignment of the remainder.
    buckets: Dict[str, str] = {}  # task_key -> partition label
    train: List[ReplaySnapshot] = []
    validation: List[ReplaySnapshot] = []
    holdout: List[ReplaySnapshot] = []

    for snapshot in remainder:
        label = buckets.get(key_for(snapshot))
        if label is None:
            label = _assign_partition(
                key_for(snapshot),
                train_ratio, validation_ratio, holdout_ratio,
            )
            buckets[key_for(snapshot)] = label
        target = {"train": train, "validation": validation, "holdout": holdout}[label]
        target.append(snapshot)

    return DatasetSplit(
        train=train,
        validation=validation,
        holdout=holdout,
        temporal_holdout=temporal,
        config={
            "train_ratio": train_ratio,
            "validation_ratio": validation_ratio,
            "holdout_ratio": holdout_ratio,
            "temporal_holdout_ratio": temporal_holdout_ratio,
        },
    )


def _assign_partition(key: str, train_ratio: float, validation_ratio: float,
                      holdout_ratio: float) -> str:
    import hashlib

    total = train_ratio + validation_ratio + holdout_ratio
    bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
    position = (bucket % 1_000_000) / 1_000_000.0 * total
    if position < train_ratio:
        return "train"
    if position < train_ratio + validation_ratio:
        return "validation"
    return "holdout"