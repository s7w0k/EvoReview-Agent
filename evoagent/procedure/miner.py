"""Mine reusable tool paths from production traces (plan section 10.1 / 10.2).

Every review produces a ``DecisionTrace`` / ``ReplaySnapshot``.  This module
aggregates the *successful* tool paths across those traces into candidate
patterns and only promotes a pattern to a ``CandidateSource`` when it clears
the statistical bar of section 10.2:

    min_support       (>= 5)
    success_rate      (>= 0.8)
    verification_pass (>= 0.8)

A qualifying pattern still leaves room for a hypothesis / reflection before a
skill is synthesised; it is the *source*, not the skill itself.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass
class TraceRecord:
    """A single successful-trace observation fed to the miner.

    ``tool_path`` is an ordered list of tool names invoked during the review
    (e.g. ``["search_code", "find_callers", "read_file"]``).
    """
    task_type: str
    risk_type: str
    tool_path: List[str]
    outcome: str = "accepted"   # "accepted" | "rejected" | "no_finding"
    verification: bool = True   # verification outcome (verifier passed)
    human_feedback: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_type": self.task_type,
            "risk_type": self.risk_type,
            "tool_path": list(self.tool_path),
            "outcome": self.outcome,
            "verification": self.verification,
            "human_feedback": self.human_feedback,
        }


@dataclass
class ProcedurePattern:
    """A tool path aggregated over semantically similar traces."""

    task_type: str
    risk_type: str
    tool_path: Tuple[str, ...]
    support: int = 0
    success_count: int = 0
    verification_count: int = 0

    @property
    def success_rate(self) -> float:
        return self.success_count / self.support if self.support else 0.0

    @property
    def verification_rate(self) -> float:
        return self.verification_count / self.support if self.support else 0.0

    def qualifies(
        self,
        *,
        min_support: int = 5,
        min_success_rate: float = 0.8,
        min_verification_pass: float = 0.8,
    ) -> bool:
        return (
            self.support >= min_support
            and self.success_rate >= min_success_rate
            and self.verification_rate >= min_verification_pass
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_type": self.task_type,
            "risk_type": self.risk_type,
            "tool_path": list(self.tool_path),
            "support": self.support,
            "success_count": self.success_count,
            "success_rate": round(self.success_rate, 4),
            "verification_count": self.verification_count,
            "verification_rate": round(self.verification_rate, 4),
        }


@dataclass
class CandidateSource:
    """A qualified ``ProcedurePattern`` ready to seed a synthesizer."""

    pattern: ProcedurePattern
    sample_traces: List[TraceRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern": self.pattern.to_dict(),
            "sample_traces": [record.to_dict() for record in self.sample_traces],
        }


class ProcedureMiner:
    """Aggregate successful tool paths into qualified candidate sources."""

    def __init__(
        self,
        *,
        min_support: int = 5,
        min_success_rate: float = 0.8,
        min_verification_pass: float = 0.8,
    ):
        self._min_support = min_support
        self._min_success_rate = min_success_rate
        self._min_verification_pass = min_verification_pass

    def mine(
        self,
        records: Iterable[TraceRecord],
        *,
        task_type: Optional[str] = None,
        risk_type: Optional[str] = None,
    ) -> List[CandidateSource]:
        """Return the qualified ``CandidateSource`` records.

        Records are grouped by ``(task_type, risk_type, tool_path)``; optional
        ``task_type`` / ``risk_type`` filters narrow the pool first.
        """
        grouped: Dict[Tuple[str, str, Tuple[str, ...]], List[TraceRecord]] = {}
        for record in records:
            if task_type is not None and record.task_type != task_type:
                continue
            if risk_type is not None and record.risk_type != risk_type:
                continue
            key = (record.task_type, record.risk_type, tuple(record.tool_path))
            grouped.setdefault(key, []).append(record)

        sources: List[CandidateSource] = []
        for (task, risk, path), sample in grouped.items():
            if not path:
                continue
            support = len(sample)
            success_count = sum(1 for r in sample if r.outcome == "accepted")
            verification_count = sum(1 for r in sample if r.verification)
            pattern = ProcedurePattern(
                task_type=task,
                risk_type=risk,
                tool_path=path,
                support=support,
                success_count=success_count,
                verification_count=verification_count,
            )
            if pattern.qualifies(
                min_support=self._min_support,
                min_success_rate=self._min_success_rate,
                min_verification_pass=self._min_verification_pass,
            ):
                sources.append(CandidateSource(pattern=pattern, sample_traces=sample))

        sources.sort(key=lambda source: (-source.pattern.support,
                                         source.pattern.tool_path))
        return sources