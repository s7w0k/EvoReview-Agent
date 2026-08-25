"""Semantic review specialists layered on top of the deterministic rules.

The rule reviewers run per added line; the semantic reviewer rebuilds a
per-file logical snapshot from the added lines and uses the stdlib AST
analyzer (plus optional Bandit/Ruff) to catch cross-statement issues.  The
whole layer is opt-in via ``EVOAGENT_STATIC_ANALYZER`` and defaults to off,
so the runtime reviewers list is unchanged unless enabled.
"""
import logging
from typing import List, Optional

from .ast_analysis import analyze_added_lines
from .diff_parser import ParsedDiff
from .external_analyzers import (
    ToolUnavailable, is_available, run_analyzer, snapshot_source, severity_of,
)
from .models import Finding, Severity
from .reviewer import Reviewer

logger = logging.getLogger(__name__)


class SemanticReviewer(Reviewer):
    """Deterministic stdlib-AST reviewer over per-file added-line snapshots."""

    name = "semantic-agent"
    domains = ("security", "reliability", "correctness")
    analyzer = "ast"

    def __init__(self, analyzer: str = "ast"):
        self.analyzer = analyzer

    def review(self, diff: str, parsed: ParsedDiff) -> List[Finding]:
        findings = []
        for item in analyze_added_lines(parsed.added_lines):
            findings.append(Finding(
                rule_id=item["rule_id"], severity=Severity(item["severity"]),
                title=item["title"], explanation=item["explanation"],
                path=item["path"], line=item["line"], evidence=item["evidence"],
                fix=item["fix"], test=item["test"], confidence=0.8,
                analyzer=self.analyzer,
            ))
        return findings


class ExternalAnalyzerReviewer(Reviewer):
    """Optional Bandit/Ruff adapter; empty findings when the tool is missing."""

    name = "external-analyzer"
    domains = ("security", "correctness")

    def __init__(self, analyzer: str):
        self.analyzer = analyzer

    def review(self, diff: str, parsed: ParsedDiff) -> List[Finding]:
        files = {
            path: source for path, source in snapshot_source(parsed.added_lines).items()
            if not path.endswith((".lock", ".min.js", ".map"))
        }
        if not files:
            return []
        try:
            results = run_analyzer(self.analyzer, files)
        except ToolUnavailable as exc:
            logger.warning("external analyzer degraded: %s", exc)
            return []
        findings = []
        for item in results:
            findings.append(Finding(
                rule_id=item["rule_id"], severity=severity_of(item["severity"]),
                title=item["title"], explanation=item["explanation"],
                path=item["path"], line=item["line"], evidence=item.get("evidence", ""),
                fix=item.get("fix", ""), test=item.get("test", ""), confidence=0.6,
                analyzer=self.analyzer,
            ))
        return findings


class CompositeSemanticReviewer(Reviewer):
    """AST layer plus optional external tools; never raises on tool absence."""

    name = "composite-semantic"
    domains = ("security", "reliability", "correctness")

    def __init__(self):
        self._ast = SemanticReviewer("ast")

    def review(self, diff: str, parsed: ParsedDiff) -> List[Finding]:
        findings = self._ast.review(diff, parsed)
        for name in ("bandit", "ruff"):
            if is_available(name):
                findings.extend(ExternalAnalyzerReviewer(name).review(diff, parsed))
        return findings


def build_semantic_reviewer(analyzer: str) -> Optional[Reviewer]:
    """Create the semantic reviewer for a configured mode.

    Returns None for "off".  Bandit/Ruff modes fall back to the stdlib AST
    reviewer when the executable is not installed (optional dependency).
    """
    mode = (analyzer or "off").strip().lower()
    if mode == "off":
        return None
    if mode == "ast":
        return SemanticReviewer("ast")
    if mode in {"bandit", "ruff"}:
        if is_available(mode):
            return ExternalAnalyzerReviewer(mode)
        logger.warning(
            "EVOAGENT_STATIC_ANALYZER=%s requested but %r is not installed; "
            "falling back to the stdlib AST analyzer", mode, mode,
        )
        return SemanticReviewer("ast")
    if mode == "composite":
        return CompositeSemanticReviewer()
    raise ValueError(
        "EVOAGENT_STATIC_ANALYZER must be one of: off, ast, bandit, ruff, composite"
    )
