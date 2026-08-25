from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskState(str, Enum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    REVIEWING = "REVIEWING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ChangedLine:
    path: str
    line: int
    content: str


@dataclass
class Finding:
    rule_id: str
    severity: Severity
    title: str
    explanation: str
    path: str
    line: int
    evidence: str
    fix: str
    test: str
    confidence: float = 0.8
    # Optional explicit provenance: "skill_name@version" for findings produced
    # by a declarative evolved skill.  Absent/NULL means attribution is unknown
    # and the finding must not be counted towards per-version usage metrics.
    source_skill: Optional[str] = None
    # Analyzer that produced this finding: "rule" (default) or a semantic
    # analyzer such as "ast" / "bandit" / "ruff".  Absent/NULL keeps legacy
    # behavior for older serialized reports.
    analyzer: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["severity"] = self.severity.value
        return value


@dataclass
class ReviewReport:
    repository: str
    pull_request: Optional[int]
    summary: str
    risk: str
    findings: List[Finding] = field(default_factory=list)
    files_reviewed: List[str] = field(default_factory=list)
    reviewer: str = "local-rules"
    collaboration: Dict[str, Any] = field(default_factory=dict)
    # Work Package 6: append-only result quality metadata.
    classification: Dict[str, Any] = field(default_factory=dict)
    clustering: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repository": self.repository,
            "pull_request": self.pull_request,
            "summary": self.summary,
            "risk": self.risk,
            "findings": [item.to_dict() for item in self.findings],
            "files_reviewed": self.files_reviewed,
            "reviewer": self.reviewer,
            "collaboration": self.collaboration,
            "classification": self.classification,
            "clustering": self.clustering,
        }


@dataclass
class TraceEvent:
    step: int
    state: TaskState
    message: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value
