"""Deterministic risk profiling for a diff.

The profiler deliberately does not call an LLM.  It derives a risk level and a
``0..1`` score from three orthogonal signals:

* sensitive paths touched by the diff (auth, security, payments, ...)
* dangerous code tokens in the added lines (SQL, shell, subprocess, crypto, ...)
* the overall change volume (number of files and added lines)

The result feeds the ``PolicyResolver`` so risk maps to an ``ExecutionPolicy``.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..diff_parser import ParsedDiff
from .models import RISK_RANK, RISK_LEVELS


HIGH_RISK_PATH_RE = re.compile(
    r"(^|/)(auth|security|ssl|session|oauth|crypto|keys?)/|"
    r"(^|/)(payment|billing|checkout|ledger)/|"
    r"(^|/)(permission|rbac|iam|roles?)/|"
    r"(^|/)(deployment|infra|ci|k8s|docker|terraform)/|"
    r"(^|/)(database|migrations?|postgres|sql)/",
    re.IGNORECASE,
)

SECURITY_TOKEN_RE = re.compile(
    r"\b(password|passwd|credential|secret|api[_-]?key|access[_-]?token|"
    r"session|auth|oauth|permission|rbac|acl|grant|role)\b",
    re.IGNORECASE,
)

DANGEROUS_EXEC_RE = re.compile(
    r"\b(shell\s*=\s*true|os\.system|os\.popen|subprocess|execve|popen|"
    r"eval\s*\(|exec\s*\(|pickle\s*\.loads|yaml\.load|deserialize|"
    r"sql\b|injection|execute|innerhtml|dangerouslySetInnerHTML)\b",
    re.IGNORECASE,
)


@dataclass
class RiskProfile:
    level: str = "low"
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.level not in RISK_LEVELS:
            raise ValueError("invalid risk level: %s" % self.level)

    @property
    def rank(self) -> int:
        return RISK_RANK[self.level]

    def to_dict(self) -> Dict[str, Any]:
        return {"level": self.level, "score": round(self.score, 4), "reasons": list(self.reasons)}


def _level_from_score(score: float) -> str:
    if score >= 0.75:
        return "critical"
    if score >= 0.45:
        return "high"
    if score >= 0.1:
        return "medium"
    return "low"


class RiskProfiler:
    """Compute a deterministic risk profile from a parsed unified diff."""

    def __init__(
        self,
        high_risk_file_weight: float = 0.35,
        dangerous_token_weight: float = 0.30,
        volume_weight: float = 0.15,
        large_file_threshold: int = 10,
        large_line_threshold: int = 500,
    ):
        self.high_risk_file_weight = high_risk_file_weight
        self.dangerous_token_weight = dangerous_token_weight
        self.volume_weight = volume_weight
        self.large_file_threshold = large_file_threshold
        self.large_line_threshold = large_line_threshold

    def profile(self, parsed: Optional[ParsedDiff]) -> RiskProfile:
        parsed = parsed or ParsedDiff(files=[], added_lines=[])
        reasons: List[str] = []
        file_score = self._file_score(parsed.files, reasons)
        token_score = self._token_score(parsed.added_lines, reasons)
        volume_score = self._volume_score(parsed, reasons)
        # Score is bounded at 1.0; the weighted sum rarely reaches it, so a
        # single critical signal (rank-derived) can still force "critical".
        score = min(1.0, file_score + token_score + volume_score)
        level = _level_from_score(score)
        forced = self._forced_level(parsed, level, reasons)
        level = forced if forced else level
        return RiskProfile(level=level, score=score, reasons=reasons)

    def _file_score(self, files: List[str], reasons: List[str]) -> float:
        if not files:
            return 0.0
        hits = sum(1 for path in files if HIGH_RISK_PATH_RE.search(path))
        score = min(0.6, hits * self.high_risk_file_weight)
        if hits:
            reasons.append("%d changed file(s) in sensitive path(s)" % hits)
        return score

    def _token_score(self, added_lines, reasons: List[str]) -> float:
        hits = 0
        critical_hint = False
        for line in added_lines:
            content = (line.content or "").lower()
            if DANGEROUS_EXEC_RE.search(content):
                hits += 1
            if SECURITY_TOKEN_RE.search(content):
                hits += 1
            if re.search(r"\bpassword\b", content) and re.search(r"\b[a-f0-9]{32,}\b", content):
                critical_hint = True
        score = min(0.45, hits * self.dangerous_token_weight)
        if hits:
            reasons.append("%d added line(s) contain dangerous tokens" % hits)
        if critical_hint:
            reasons.append("added line(s) contain a hardcoded secret-like value")
        return max(score, 0.15 if critical_hint else score)

    def _volume_score(self, parsed: ParsedDiff, reasons: List[str]) -> float:
        score = 0.0
        if len(parsed.files) > self.large_file_threshold:
            score += self.volume_weight
            reasons.append(
                "large change: %d file(s) modified" % len(parsed.files)
            )
        if len(parsed.added_lines) > self.large_line_threshold:
            score += self.volume_weight
            reasons.append(
                "large change: %d line(s) added" % len(parsed.added_lines)
            )
        return score

    @staticmethod
    def _forced_level(parsed: ParsedDiff, level: str, reasons: List[str]) -> Optional[str]:
        critical = any(
            line.path and re.search(r"(^|/)(auth|security|payment|keys?)/", line.path, re.I)
            and re.search(r"\b(eval|exec|shell=True|os\.system|pickle|subprocess)\b",
                          (line.content or ""), re.I)
            for line in parsed.added_lines
        )
        if critical and RISK_RANK[level] < RISK_RANK["critical"]:
            reasons.append("critical sensitive-path + dangerous-token signal")
            return "critical"
        return None