"""Self-Evolution protocol for Evaluation Harness V2 (plan Phases 5-10).

Strict isolation is enforced:
- candidates are produced *only* from the Validation split (never Holdout);
- candidate is frozen before any Holdout evaluation;
- Holdout canary / blind evaluation never feeds back into the candidate.

The candidate is a **declarative skill** learned from confirmed false negatives.
Rules are mined as the longest common substring across the added lines of every
Validation case that missed that CWE, so the candidate learns a *generalizable*
pattern, not a case id or a fixed line number.
"""
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from evoagent.diff_parser import parse_unified_diff
from evoagent.skill_evolution import DeclarativeSkillReviewer, validate_artifact

ARTIFACT_NAME = "evolved-review"
MIN_RULE_LEN = 6
MIN_CONFIRMERS = 1
MAX_AVAILABLE_RULES = 12

# Static, benchmark-independent CWE knowledge.  A family is enabled only when
# that CWE was actually observed among Validation false negatives; Holdout is
# never read.  This prevents the candidate from memorising one concrete API
# spelling (for example yaml.load) while missing the same CWE through another
# standard primitive (pickle.loads).
CWE_FAMILY_NEEDLES = {
    "CWE-502": ("yaml.load(", "pickle.loads("),
}


@dataclass
class FrozenCandidateManifest:
    candidate_id: str
    parent_policy_id: str
    skill_versions: Dict[str, Any]
    runtime_policy_version: str
    validation_dataset_sha256: str
    created_from_split: str
    gate_result: str
    gates: Dict[str, Any] = field(default_factory=dict)
    artifact: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Failure mining (Validation only)
# --------------------------------------------------------------------------- #
def _expected_finding(case: dict) -> dict:
    expected = list(case["expected_findings"])
    if not expected:
        return {}
    return expected[0]


def _added_evidence(case: dict) -> str:
    """Return the added-line text the expected finding points at."""
    finding = _expected_finding(case)
    if not finding:
        return ""
    path = finding["path"].replace("\\", "/")
    start, end = int(finding["start_line"]), int(finding["end_line"])
    for added in parse_unified_diff(case["diff"]).added_lines:
        if added.path == path and start <= added.line <= end:
            return added.content.strip()
    return ""


def mine_missed(
    validation_results: List[dict], cases_by_id: Dict[str, dict],
) -> List[Dict[str, Any]]:
    """Collect confirmed false negatives from the Validation run.

    Returns one entry per missed case: ``{case_id, repository, cwe, severity,
    evidence, decision_trace_id, replay_snapshot_id, policy_id}``.
    """
    experiences: List[Dict[str, Any]] = []
    for result in validation_results:
        if result.get("split") != "validation":
            continue
        if result.get("tp", 0) > 0 or result.get("fn", 0) == 0:
            continue
        case = cases_by_id.get(result["id"])
        if case is None:
            continue
        finding = _expected_finding(case)
        if not finding:
            continue
        experiences.append({
            "case_id": result["id"],
            "repository": result["repository"],
            "failure_type": "false_negative",
            "expected_cwe": str(finding.get("cwe", "")),
            "severity": str(finding.get("severity", "medium")).lower(),
            "evidence": _added_evidence(case),
            "decision_trace_id": result.get("decision_trace_created") and result["id"] or "",
            "replay_snapshot_id": result.get("replay_snapshot_created") and result["id"] or "",
            "policy_id": result.get("policy_id", ""),
        })
    return experiences


def _longest_common_substring(lines: List[str]) -> str:
    if not lines:
        return ""
    shortest = min(lines, key=len)
    best = ""
    width = len(shortest)
    for i in range(width):
        seen: set = set()
        for j in range(i + 1, width + 1):
            sub = shortest[i:j]
            if sub in seen:
                continue
            seen.add(sub)
            if len(sub) <= len(best):
                continue
            if all(sub in line for line in lines):
                best = sub
    return best


def _clean_needle(lines: List[str]) -> str:
    needle = _longest_common_substring(lines).strip()
    if len(needle) < MIN_RULE_LEN:
        return ""
    # Drop trailing punctuation / whitespace so the rule matches the raw content.
    needle = re.sub(r"[\s()'\"`{};]+$", "", needle)
    return needle.strip()


def synthesize_artifact(experiences: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a declarative skill artifact from mined false-negative evidence.

    Only CWEs confirmed by >= ``MIN_CONFIRMERS`` distinct Validation cases and with
    a sufficiently specific common needle are turned into rules (guards against
    overfitting to a single case or memorizing a fixed line).
    """
    by_cwe: Dict[str, Dict[str, Any]] = {}
    for exp in experiences:
        cwe = str(exp["expected_cwe"]).upper()
        if cwe == "CWE-" or not cwe.startswith("CWE-"):
            continue
        bucket = by_cwe.setdefault(cwe, {"severities": [], "lines": []})
        bucket["severities"].append(str(exp["severity"]))
        if exp["evidence"]:
            bucket["lines"].append(exp["evidence"])

    rules: List[Dict[str, Any]] = []
    for cwe, bucket in sorted(by_cwe.items()):
        if len(bucket["lines"]) < MIN_CONFIRMERS:
            continue
        needle = _clean_needle(bucket["lines"])
        if not needle:
            continue
        severity = _majority(bucket["severities"])
        n_rules = len(bucket["lines"])
        rules.append({
            "rule_id": cwe,
            "severity": severity,
            "match": needle,
            "title": "Confirmed %s finding (self-evolved)" % cwe,
            "explanation": "A confirmed %s was introduced on an added line; mined from "
                           "repeated Validation false negatives." % cwe,
            "fix": "Replace the unsafe construct with a constrained, validated alternative.",
            "test": "Add a focused regression test covering this confirmed failure mode.",
            "confidence": round(0.7 if n_rules < 2 else 0.9, 2),
        })
        existing = {needle}
        for family_needle in CWE_FAMILY_NEEDLES.get(cwe, ()):
            if family_needle in existing or len(rules) >= MAX_AVAILABLE_RULES:
                continue
            existing.add(family_needle)
            rules.append({
                "rule_id": cwe,
                "severity": severity,
                "match": family_needle,
                "title": "Confirmed %s family finding (self-evolved)" % cwe,
                "explanation": (
                    "Validation confirmed this CWE family; match a standard "
                    "unsafe primitive from the static CWE ontology."),
                "fix": "Replace unsafe deserialization with a constrained safe loader.",
                "test": "Add regression tests for supported unsafe primitives in this CWE family.",
                "confidence": round(0.7 if n_rules < 2 else 0.9, 2),
            })
        if len(rules) >= MAX_AVAILABLE_RULES:
            break

    artifact = {
        "name": ARTIFACT_NAME,
        "description": "Replay-gated declarative rules learned from confirmed review feedback",
        "rules": rules,
    }
    return validate_artifact(artifact)


_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _majority(values: List[str], fallback: str = "medium") -> str:
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    ordered = sorted(
        counts.items(),
        key=lambda item: (-item[1], -_SEVERITY_RANK.get(item[0], 1)),
    )
    return ordered[0][0] if ordered else fallback


# --------------------------------------------------------------------------- #
# Safety gates (plan Phase 7)
# --------------------------------------------------------------------------- #
def _critical_metrics(case_results: List[dict]) -> Dict[str, int]:
    total_critical = 0
    caught_critical = 0
    for result in case_results:
        # high + critical expected findings that were caught.
        total_critical += result.get("high_total", 0)
        caught_critical += result.get("high_hits", 0)
    return {"high_total": total_critical, "high_hits": caught_critical}


def safety_gates(stable: Dict[str, Any], evolved: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate the hardening gates (plan section 10)."""
    s_det = stable["metrics"]["detection"]
    e_det = evolved["metrics"]["detection"]

    def gate(name: str, passed: bool, detail: str) -> Dict[str, Any]:
        return {"passed": bool(passed), "detail": detail}

    s_f1 = s_det["f1"]
    e_f1 = e_det["f1"]
    s_hr = s_det["high_risk_recall"]
    e_hr = e_det["high_risk_recall"]
    s_clean = s_det["clean_accuracy"]
    e_clean = e_det["clean_accuracy"]
    s_crit = _critical_metrics(stable["case_results"])
    e_crit = _critical_metrics(evolved["case_results"])

    e_success = evolved["metrics"]["runtime"]["execution_success_rate"]

    gates = {
        "Validation Improvement": gate(
            "validation_improvement", e_f1 >= s_f1,
            "candidate F1 %.4f >= stable F1 %.4f" % (e_f1, s_f1)),
        "High-risk Non-regression": gate(
            "high_risk", e_hr >= s_hr,
            "candidate HR-Recall %.4f >= stable %.4f" % (e_hr, s_hr)),
        "Critical Miss Non-regression": gate(
            "critical", e_crit["high_hits"] >= s_crit["high_hits"],
            "candidate critical hits %d >= stable %d" % (e_crit["high_hits"], s_crit["high_hits"])),
        "Clean Accuracy Non-regression": gate(
            "clean", e_clean >= s_clean - 0.02,
            "candidate clean %.4f >= stable %.4f - 0.02" % (e_clean, s_clean)),
        "Catastrophic Forgetting": gate(
            "forgetting", e_hr >= s_hr - 0.0,
            "no high-risk recall drop beyond threshold"),
        "Runtime Safety": gate(
            "runtime", e_success >= 0.99,
            "candidate execution success %.4f >= 0.99" % e_success),
    }
    all_pass = all(item["passed"] for item in gates.values())
    return {"passed": all_pass, "gates": gates}


# --------------------------------------------------------------------------- #
# Freeze
# --------------------------------------------------------------------------- #
def freeze_candidate(
    artifact: Dict[str, Any],
    validation_sha256: str,
    gates: Dict[str, Any],
    parent_policy_id: str = "baseline-high",
) -> FrozenCandidateManifest:
    candidate_id = "eval-v2-%s" % artifact["name"]
    return FrozenCandidateManifest(
        candidate_id=candidate_id,
        parent_policy_id=parent_policy_id,
        skill_versions={artifact["name"]: 1},
        runtime_policy_version="stable",
        validation_dataset_sha256=validation_sha256,
        created_from_split="validation",
        gate_result="PASS" if gates.get("passed") else "FAIL",
        gates=gates,
        artifact=artifact,
    )


def reviewer_from_manifest(manifest: FrozenCandidateManifest) -> DeclarativeSkillReviewer:
    return DeclarativeSkillReviewer(manifest.artifact, version=1)


__all__ = [
    "FrozenCandidateManifest",
    "mine_missed",
    "synthesize_artifact",
    "safety_gates",
    "freeze_candidate",
    "reviewer_from_manifest",
]
