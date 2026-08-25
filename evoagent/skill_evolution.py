"""Versioned, replay-gated evolution for executable review skills.

The evolved artifact is deliberately declarative.  Feedback may change matching
behaviour, but it cannot inject Python or acquire host permissions.  Every
candidate is replayed against validation and holdout datasets before activation.
"""
import hashlib
import json
import re
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from .diff_parser import parse_unified_diff
from .evolution import RegressionEvaluator
from .evolution_gates import forgetting_gate, generalization_gate, production_source_gate
from .feedback_trust import (
    confirmed_missed_issue_keys,
    downgraded_feedbacker,
    missed_issue_signature,
    trusted_feedbacker_ids,
)
from .models import Finding, Severity
from .reviewer import Reviewer
from .store import utc_now
from . import skill_lifecycle


ARTIFACT_SCHEMA_VERSION = 1
RULE_ID = re.compile(r"[A-Z][A-Z0-9_-]{1,79}")
SKILL_NAME = re.compile(r"evolved-[a-z0-9][a-z0-9_-]{0,72}")
RUNTIME_VERSION = "0.3"


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: dict) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _iso_age_minutes(value: str) -> Optional[float]:
    """Age in minutes of an ISO-8601 timestamp; None when unparseable."""
    try:
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 60.0


def validate_artifact(artifact: dict, expected_name: str = "") -> dict:
    """Validate and normalize an untrusted declarative skill artifact."""
    if not isinstance(artifact, dict):
        raise ValueError("skill artifact must be an object")
    name = str(artifact.get("name", expected_name)).strip().lower()
    if expected_name and name != expected_name:
        raise ValueError("skill artifact name must match skill_name")
    if not SKILL_NAME.fullmatch(name):
        raise ValueError("evolved skill names must start with 'evolved-'")
    raw_rules = artifact.get("rules", [])
    if not isinstance(raw_rules, list) or len(raw_rules) > 100:
        raise ValueError("skill artifact rules must be a list with at most 100 items")

    rules = []
    identities = set()
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise ValueError("each evolved skill rule must be an object")
        rule_id = str(raw.get("rule_id", "")).strip().upper()
        if not RULE_ID.fullmatch(rule_id):
            raise ValueError("invalid evolved skill rule_id: %s" % rule_id)
        try:
            severity = Severity(str(raw.get("severity", "medium")).lower()).value
        except ValueError as exc:
            raise ValueError("invalid severity for rule %s" % rule_id) from exc
        match = str(raw.get("match", "")).strip()
        if not match or len(match) > 240 or "\n" in match or "\r" in match:
            raise ValueError("rule %s match must be a single non-empty line" % rule_id)
        identity = (rule_id, match, bool(raw.get("ignore_case", False)))
        if identity in identities:
            raise ValueError("duplicate evolved skill rule: %s" % rule_id)
        identities.add(identity)
        confidence = float(raw.get("confidence", .85))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("rule confidence must be between 0 and 1")
        include_paths = raw.get("include_paths", [])
        exclude_paths = raw.get("exclude_paths", ["tests/"])
        if not isinstance(include_paths, list) or not isinstance(exclude_paths, list):
            raise ValueError("rule path filters must be lists")
        if any(not isinstance(item, str) or len(item) > 200 for item in include_paths + exclude_paths):
            raise ValueError("invalid rule path filter")
        rules.append({
            "rule_id": rule_id,
            "severity": severity,
            "match": match,
            "ignore_case": bool(raw.get("ignore_case", False)),
            "include_paths": include_paths,
            "exclude_paths": exclude_paths,
            "title": str(raw.get("title") or ("Confirmed %s finding" % rule_id))[:200],
            "explanation": str(raw.get("explanation") or "A confirmed feedback pattern was found on an added line.")[:2000],
            "fix": str(raw.get("fix") or "Replace the unsafe construct with a constrained alternative.")[:2000],
            "test": str(raw.get("test") or "Add a regression test covering the confirmed failure mode.")[:2000],
            "confidence": round(confidence, 4),
        })
    rules.sort(key=lambda item: (item["rule_id"], item["match"]))
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "name": name,
        "description": str(artifact.get("description") or "Replay-gated rules learned from confirmed review feedback")[:500],
        "permissions": [],
        "rules": rules,
    }


class DeclarativeSkillReviewer(Reviewer):
    """Execute a validated evolved artifact without eval, imports or subprocesses."""

    def __init__(self, artifact: dict, version: Optional[int] = None):
        self.artifact = validate_artifact(artifact, str(artifact.get("name", "")))
        self.version = version
        self.name = self.artifact["name"] + ("@%s" % version if version is not None else "")

    def review(self, diff: str, parsed) -> List[Finding]:
        findings = []
        seen = set()
        for line in parsed.added_lines:
            for rule in self.artifact["rules"]:
                if rule["include_paths"] and not any(line.path.startswith(p) for p in rule["include_paths"]):
                    continue
                if any(line.path.startswith(p) for p in rule["exclude_paths"]):
                    continue
                content = line.content.lower() if rule["ignore_case"] else line.content
                needle = rule["match"].lower() if rule["ignore_case"] else rule["match"]
                key = (rule["rule_id"], line.path, line.line)
                if needle not in content or key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(
                    rule_id=rule["rule_id"], severity=Severity(rule["severity"]),
                    title=rule["title"], explanation=rule["explanation"],
                    path=line.path, line=line.line, evidence=line.content.strip()[:240],
                    fix=rule["fix"], test=rule["test"], confidence=rule["confidence"],
                    source_skill=self.name,
                ))
        return findings


class SkillEvolutionEngine:
    """Create, replay, activate and roll back declarative skill versions."""

    def __init__(
        self, store, reviewer_factory: Optional[Callable[[dict], Reviewer]] = None,
        min_cases: int = 3, max_cases: int = 100, min_improvement: float = .01,
        min_holdout_cases: int = 2, max_metric_regression: float = 0.0,
        experience_mode: str = "off",
        marginal_gate: str = "off", min_unique_tp: int = 1, max_new_fp: int = 0,
        eval_source: str = "builtin",
        # Work Package 9: feedback trust and overfitting protection.  Defaults
        # preserve the pre-WP9 behavior exactly.
        min_confirmers: int = 1, trust_enabled: bool = False,
        trust_min_ratio: float = 0.5, compare_history: int = 1,
        cooldown_minutes: int = 0, holdout_rotation: int = 0,
        quality_gates_enabled: bool = False,
        production_profile: bool = False,
    ):
        self.store = store
        self.reviewer_factory = reviewer_factory or (lambda artifact: DeclarativeSkillReviewer(artifact))
        self.min_cases = min_cases
        self.max_cases = max_cases
        self.min_improvement = min_improvement
        self.min_holdout_cases = min_holdout_cases
        self.max_metric_regression = max_metric_regression
        self.experience_mode = (experience_mode or "off").strip().lower()
        self.marginal_gate = (marginal_gate or "off").strip().lower()
        self.min_unique_tp = int(min_unique_tp)
        self.max_new_fp = int(max_new_fp)
        # Work Package 8: evaluation dataset scope, mirrored from EvolutionEngine.
        self.eval_source = eval_source
        # Work Package 9 guards.
        self.min_confirmers = int(min_confirmers)
        self.trust_enabled = bool(trust_enabled)
        self.trust_min_ratio = float(trust_min_ratio)
        self.compare_history = int(compare_history)
        self.cooldown_minutes = int(cooldown_minutes)
        self.holdout_rotation = int(holdout_rotation)
        # Closed-loop WP3: forgetting/generalization/production-source gates.
        self.quality_gates_enabled = bool(quality_gates_enabled)
        # Closed-loop WP4: production profile defers activation to deployment.
        self.production_profile = bool(production_profile)
        self._lock = threading.RLock()

    @staticmethod
    def empty_artifact(skill_name: str) -> dict:
        return validate_artifact({"name": skill_name, "rules": []}, skill_name)

    def _factory(self, serialized: str) -> Reviewer:
        return self.reviewer_factory(json.loads(serialized))

    @staticmethod
    def _redact_holdout(metrics: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in metrics.items() if key not in {"case_results", "errors"}}

    @staticmethod
    def _fingerprint(cases: List[dict]) -> str:
        canonical = [
            {
                "name": case.get("name"), "split": case.get("split"),
                "diff": case.get("diff"), "expected": case.get("expected", []),
            }
            for case in cases
        ]
        return _sha256({"cases": canonical})

    def _compute_marginal(
        self, cases: List[dict], baseline_artifact: dict, candidate_artifact: dict,
    ) -> Dict[str, Any]:
        """Marginal metrics of candidate relative to the current active artifact."""
        baseline_findings = set()
        candidate_findings = set()
        expected_keys = set()
        for case in cases:
            parsed = parse_unified_diff(case["diff"])
            for finding in self._factory(_canonical_json(baseline_artifact)).review(case["diff"], parsed):
                baseline_findings.add((finding.path, finding.line, finding.rule_id))
            for finding in self._factory(_canonical_json(candidate_artifact)).review(case["diff"], parsed):
                candidate_findings.add((finding.path, finding.line, finding.rule_id))
            for item in case.get("expected", []):
                expected_keys.add((str(item["path"]), int(item["line"]), str(item.get("rule_id", ""))))
        candidate_tp = candidate_findings & expected_keys
        baseline_tp = baseline_findings & expected_keys
        # New TP: true positives the candidate finds that the baseline misses.
        unique_tp = candidate_tp - baseline_tp
        # New FP: candidate false positives that the baseline did not emit.
        baseline_fp = baseline_findings - (baseline_findings & expected_keys)
        new_fp = (candidate_findings - (candidate_findings & expected_keys)) - baseline_fp
        return {
            "unique_true_positives": len(unique_tp),
            "new_false_positives": len(new_fp),
            "finding_overlap": len(candidate_findings & baseline_findings),
            "candidate_true_positives": len(candidate_tp),
            "candidate_findings": len(candidate_findings),
            "baseline_findings": len(baseline_findings),
        }

    def _marginal_gate_result(self, marginal: Dict[str, Any]) -> Dict[str, Any]:
        would_pass = (
            marginal["unique_true_positives"] >= self.min_unique_tp
            and marginal["new_false_positives"] <= self.max_new_fp
        )
        return {
            "mode": self.marginal_gate,
            "would_pass": would_pass,
            "min_unique_tp": self.min_unique_tp,
            "max_new_fp": self.max_new_fp,
            **marginal,
        }

    def _non_regressing(self, candidate: dict, baseline: dict) -> bool:
        protected = ["score", "precision", "recall", "high_severity_recall", "success_rate"]
        if baseline.get("positive_cases", 0):
            protected.append("severity_accuracy")
        if baseline.get("clean_cases", 0):
            protected.append("clean_accuracy")
        return all(
            float(candidate.get(name, 0)) + self.max_metric_regression
            >= float(baseline.get(name, 0)) for name in protected
        )

    def status(
        self, skill_name: str = "evolved-review", tenant_id: str = "default",
    ) -> Dict[str, Any]:
        validation = self.store.list_evaluation_cases(
            "validation", True, self.max_cases, self.eval_source
        )
        holdout = self.store.list_evaluation_cases(
            "holdout", True, self.max_cases, self.eval_source
        )
        active = self.store.get_active_skill_artifact(skill_name, tenant_id)
        return {
            "tenant_id": tenant_id, "skill_name": skill_name,
            "active_version": active.get("version") if active else None,
            "active_artifact_sha256": active.get("artifact_sha256") if active else None,
            "validation_cases": len(validation), "holdout_cases": len(holdout),
            "minimum_cases": self.min_cases, "minimum_holdout_cases": self.min_holdout_cases,
            "minimum_improvement": self.min_improvement,
            "maximum_metric_regression": self.max_metric_regression,
            "ready": len(validation) >= self.min_cases and len(holdout) >= self.min_holdout_cases,
        }

    def propose(
        self, skill_name: str, artifact: dict, tenant_id: str = "default",
        source_task_ids: Optional[List[str]] = None,
        source_case_ids: Optional[List[str]] = None,
        source_experience_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        skill_name = skill_name.strip().lower()
        candidate_artifact = validate_artifact(artifact, skill_name)
        with self._lock:
            return self._propose(
                skill_name, candidate_artifact, tenant_id,
                list(source_task_ids or []), list(source_case_ids or []),
                list(source_experience_ids or []),
            )

    def _propose(
        self, skill_name: str, artifact: dict, tenant_id: str,
        source_task_ids: Optional[List[str]] = None,
        source_case_ids: Optional[List[str]] = None,
        source_experience_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        active = self.store.get_active_skill_artifact(skill_name, tenant_id)
        if active and active["artifact_sha256"] == _sha256(artifact):
            return {
                "version": self._public_version(active), "decision": "deferred",
                "reason": "candidate skill artifact is identical to the active version",
                "candidate": {}, "baseline": {}, "candidate_holdout": {},
                "baseline_holdout": {}, "gates": {}, "run_id": None,
            }
        # Work Package 9 cooldown: a recently rejected candidate with the same
        # fingerprint is not replayed until the cooldown window elapses.
        if self.cooldown_minutes > 0:
            candidate_sha = _sha256(artifact)
            for run in self.store.list_skill_evolution_runs(200):
                if run.get("decision") != "rejected":
                    continue
                record_sha = (
                    (run.get("metrics") or {}).get("reproducibility") or {}
                ).get("candidate_artifact_sha256")
                if record_sha != candidate_sha:
                    continue
                age = _iso_age_minutes(str(run.get("created_at", "")))
                if age is not None and age <= self.cooldown_minutes:
                    return {
                        "version": None, "decision": "deferred",
                        "reason": (
                            "candidate fingerprint was rejected within the "
                            "evolution cooldown window"
                        ),
                        "candidate": {}, "baseline": {}, "candidate_holdout": {},
                        "baseline_holdout": {}, "gates": {}, "run_id": None,
                        "cooldown_minutes": self.cooldown_minutes,
                    }
        baseline_artifact = active["artifact"] if active else self.empty_artifact(skill_name)
        validation = self.store.list_evaluation_cases(
            "validation", True, self.max_cases, self.eval_source
        )
        holdout = self.store.list_evaluation_cases(
            "holdout", True, self.max_cases, self.eval_source
        )
        # Work Package 9 history comparison: baseline is extended from the
        # active version to the recent N-1 validated/active historical versions.
        history_baselines: List[dict] = []
        if self.compare_history > 1 and active is not None:
            for item in self.store.list_skill_artifact_versions(skill_name, tenant_id):
                if item.get("active") or item.get("status") not in {
                    skill_lifecycle.VALIDATED, skill_lifecycle.ACTIVE,
                }:
                    continue
                history_baselines.append(item["artifact"])
                if len(history_baselines) >= self.compare_history - 1:
                    break
        decision = "deferred"
        reason = ""
        gates = {
            "artifact_valid": True,
            "validation_dataset_ready": len(validation) >= self.min_cases,
            "holdout_dataset_ready": len(holdout) >= self.min_holdout_cases,
            "evaluation_success": None, "validation_improvement": None,
            "validation_non_regression": None, "holdout_non_regression": None,
            "history_non_regression": (
                None if self.compare_history <= 1 or not history_baselines else False
            ),
        }
        evaluator = RegressionEvaluator(self._factory)
        baseline_metrics = self._empty_metrics(len(validation))
        candidate_metrics = self._empty_metrics(len(validation))
        baseline_holdout = self._empty_metrics(len(holdout))
        candidate_holdout = self._empty_metrics(len(holdout))
        history_metrics: List[Dict[str, Any]] = []
        history_non_regression = True
        marginal = None
        marginal_gate = None
        if len(validation) < self.min_cases:
            reason = "candidate saved but the validation dataset is smaller than the activation minimum"
        elif len(holdout) < self.min_holdout_cases:
            reason = "candidate saved but the holdout dataset is smaller than the activation minimum"
        else:
            baseline_metrics = evaluator.run(_canonical_json(baseline_artifact), validation, self.store)
            candidate_metrics = evaluator.run(_canonical_json(artifact), validation, self.store)
            baseline_holdout = evaluator.run(_canonical_json(baseline_artifact), holdout, self.store)
            candidate_holdout = evaluator.run(_canonical_json(artifact), holdout, self.store)
            no_errors = not (
                baseline_metrics["errors"] or candidate_metrics["errors"]
                or baseline_holdout["errors"] or candidate_holdout["errors"]
            )
            improved = candidate_metrics["score"] >= baseline_metrics["score"] + self.min_improvement
            validation_safe = self._non_regressing(candidate_metrics, baseline_metrics)
            holdout_safe = self._non_regressing(candidate_holdout, baseline_holdout)
            gates.update({
                "evaluation_success": no_errors, "validation_improvement": improved,
                "validation_non_regression": validation_safe,
                "holdout_non_regression": holdout_safe,
            })
            forgetting = forgetting_gate(baseline_metrics, candidate_metrics)
            generalization = generalization_gate(baseline_holdout, candidate_holdout)
            production_source = production_source_gate(validation)
            quality_safe = (
                forgetting["passed"] and generalization["passed"]
                and production_source["passed"]
            )
            gates.update({
                "forgetting": forgetting,
                "generalization": generalization,
                "production_source": production_source,
                "quality_non_regression": quality_safe,
            })
            quality_enforced = self.quality_gates_enabled and not quality_safe
            # Work Package 9 history comparison: candidate must not regress
            # against any of the recent validated baselines.  Recorded always,
            # enforced only in "enforce" mode (shadow records the result).
            for index, history_artifact in enumerate(history_baselines, 1):
                history_validation = evaluator.run(
                    _canonical_json(history_artifact), validation, self.store,
                )
                history_holdout = evaluator.run(
                    _canonical_json(history_artifact), holdout, self.store,
                )
                v_safe = self._non_regressing(candidate_metrics, history_validation)
                h_safe = self._non_regressing(candidate_holdout, history_holdout)
                history_metrics.append({
                    "index": index,
                    "score": history_validation.get("score", 0.0),
                    "validation_non_regression": v_safe,
                    "holdout_non_regression": h_safe,
                })
                history_non_regression = history_non_regression and v_safe and h_safe
            gates["history_non_regression"] = (
                history_non_regression if history_baselines else None
            )
            if self.marginal_gate in {"shadow", "enforce"}:
                marginal = self._compute_marginal(validation, baseline_artifact, artifact)
                marginal_gate = self._marginal_gate_result(marginal)
                gates["marginal_gate"] = marginal_gate
            history_enforced = (
                self.compare_history > 1
                and bool(history_baselines)
                and self.marginal_gate == "enforce"
            )
            if no_errors and improved and validation_safe and holdout_safe and not quality_enforced:
                decision = "activated"
                reason = "candidate skill improved validation and passed holdout non-regression"
                if history_enforced and not history_non_regression:
                    decision = "rejected"
                    reason = "candidate regressed against a recent validated baseline"
                if marginal_gate is not None and marginal_gate["mode"] == "enforce" and not marginal_gate["would_pass"]:
                    decision = "rejected"
                    reason = (
                        "marginal gate failed: %d unique TP < %d or %d new FP > %d"
                        % (marginal_gate["unique_true_positives"], self.min_unique_tp,
                           marginal_gate["new_false_positives"], self.max_new_fp)
                    )
            else:
                decision = "rejected"
                failures = []
                if not no_errors:
                    failures.append("evaluation failed")
                if not improved:
                    failures.append("validation improvement was below threshold")
                if not validation_safe:
                    failures.append("a protected validation metric regressed")
                if not holdout_safe:
                    failures.append("a protected holdout metric regressed")
                if quality_enforced:
                    failures.append("a forgetting/generalization/production-source gate failed")
                reason = "; ".join(failures)

        if self.production_profile and decision == "activated":
            decision = "validated"
            reason = "candidate validated offline and requires deployment approval before activation"

        if decision == "activated":
            final_status = skill_lifecycle.ACTIVE
        elif decision == "validated":
            final_status = skill_lifecycle.VALIDATED
        elif decision == "rejected":
            final_status = skill_lifecycle.REJECTED
        else:
            final_status = skill_lifecycle.DRAFT
        provenance = {
            "origin": "agent-created",
            "source_task_ids": list(source_task_ids or []),
            "source_case_ids": list(source_case_ids or []),
            "source_experience_ids": list(source_experience_ids or []),
            "generator": {"type": "feedback-rule-builder", "version": "1"},
            "dataset": {
                "validation_sha256": self._fingerprint(validation),
                "holdout_sha256": self._fingerprint(holdout),
            },
            "runtime_version": RUNTIME_VERSION,
            # Work Package 9: provenance fingerprint extension (append-only).
            "model": "declarative",
            "tool_version": RUNTIME_VERSION,
            "dataset_source": self.eval_source,
            "prompt_fingerprint": _sha256(artifact),
        }
        version = self.store.save_skill_artifact(
            skill_name, artifact, candidate_metrics.get("score", 0.0),
            decision == "activated", tenant_id,
            status=final_status, origin="agent-created", provenance=provenance,
        )
        rotated_holdout_ids: List[int] = []
        # Work Package 9 holdout rotation: every N-th activation archives the
        # oldest active holdout batch (rows are kept for audit with active=0).
        # Counted before persisting so this run is included (existing + 1).
        if decision == "activated" and self.holdout_rotation > 0:
            previous_activations = sum(
                1 for item in self.store.list_skill_evolution_runs(500)
                if item.get("decision") == "activated"
            )
            total_activations = previous_activations + 1
            if total_activations % self.holdout_rotation == 0:
                rotated_holdout_ids = self.store.archive_oldest_holdout_cases(
                    self.holdout_rotation
                )
        run = {
            "id": str(uuid.uuid4()), "tenant_id": tenant_id, "skill_name": skill_name,
            "candidate_version": version["version"],
            "baseline_version": active.get("version") if active else None,
            "decision": decision, "candidate_score": candidate_metrics.get("score", 0.0),
            "baseline_score": baseline_metrics.get("score", 0.0),
            "metrics": {
                "candidate": candidate_metrics, "baseline": baseline_metrics,
                "candidate_holdout": self._redact_holdout(candidate_holdout),
                "baseline_holdout": self._redact_holdout(baseline_holdout),
                "marginal_gate": marginal_gate,
                "history_comparison": history_metrics,
                "holdout_rotation_archived": rotated_holdout_ids,
                "gates": gates, "reason": reason,
                "reproducibility": {
                    "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
                    "candidate_artifact_sha256": version["artifact_sha256"],
                    "baseline_artifact_sha256": active.get("artifact_sha256") if active else _sha256(baseline_artifact),
                    # Work Package 8: evaluation dataset provenance (append-only).
                    "dataset_source": self.eval_source,
                    "dataset_sha256": self._fingerprint(validation),
                },
            }, "created_at": utc_now(),
        }
        self.store.save_skill_evolution_run(run)
        return {
            "version": version, "decision": decision, "reason": reason,
            "candidate": candidate_metrics, "baseline": baseline_metrics,
            "candidate_holdout": self._redact_holdout(candidate_holdout),
            "baseline_holdout": self._redact_holdout(baseline_holdout),
            "gates": gates, "run_id": run["id"],
        }

    def auto_propose(self, skill_name: str = "evolved-review", tenant_id: Optional[str] = None) -> Dict[str, Any]:
        skill_name = skill_name.strip().lower()
        if not SKILL_NAME.fullmatch(skill_name):
            raise ValueError("evolved skill names must start with 'evolved-'")
        tenant_id = tenant_id or "default"
        if self.experience_mode == "enforce":
            return self._auto_propose_from_experiences(skill_name, tenant_id)
        return self._auto_propose_from_failures(skill_name, tenant_id)

    def _auto_propose_from_experiences(self, skill_name: str, tenant_id: str) -> Dict[str, Any]:
        """Enforce path: only corroborated rule candidates drive evolution."""
        candidates = self.store.list_corroborated_rule_candidates(tenant_id)
        active = self.store.get_active_skill_artifact(skill_name, tenant_id)
        artifact = dict(active["artifact"]) if active else self.empty_artifact(skill_name)
        rules = [dict(item) for item in artifact["rules"]]
        used_exp_ids = []
        used_case_ids = []
        learned = []
        for exp in candidates:
            finding = (exp.get("payload") or {}).get("finding") or {}
            rule_id = str(finding.get("rule_id", "")).strip().upper()
            if not RULE_ID.fullmatch(rule_id):
                continue
            evidence = str(exp.get("evidence") or "").strip()
            if not evidence or len(evidence) > 240 or "\n" in evidence or "\r" in evidence:
                continue
            # Every valid corroborated candidate feeds the same candidate run and
            # is consumed together on activation.
            used_exp_ids.append(exp["id"])
            raw_rule = {
                "rule_id": rule_id, "severity": finding.get("severity", "medium"),
                "match": evidence, "ignore_case": False,
                "include_paths": [], "exclude_paths": ["tests/"],
                "title": finding.get("title"), "explanation": finding.get("explanation"),
                "fix": finding.get("fix"), "test": finding.get("test"),
                "confidence": finding.get("confidence", .85),
            }
            normalized = validate_artifact({"name": skill_name, "rules": [raw_rule]}, skill_name)["rules"][0]
            if not any(
                rule["rule_id"] == normalized["rule_id"] and rule["match"] == normalized["match"]
                for rule in rules
            ):
                rules.append(normalized)
                learned.append(rule_id)
        candidate = validate_artifact({**artifact, "name": skill_name, "rules": rules}, skill_name)
        if not used_exp_ids:
            return {
                "version": None, "decision": "deferred",
                "reason": "no corroborated rule candidate experience was found",
                "experiences_used": 0, "learned_rule_ids": [],
                "removed_rule_ids": [], "run_id": None,
            }
        # Map consumed experiences back to their legacy failure cases (exact
        # task+category+rule signature match, never fuzzy).
        used_case_ids = self.store.find_failure_case_ids_for_experiences(
            self.store.list_experiences_by_ids(used_exp_ids)
        )
        result = self.propose(
            skill_name, candidate, tenant_id,
            source_task_ids=sorted({exp["task_id"] for exp in candidates if exp["id"] in used_exp_ids}),
            source_case_ids=sorted(used_case_ids),
            source_experience_ids=sorted(used_exp_ids),
        )
        result.update({
            "experiences_used": len(used_exp_ids),
            "learned_rule_ids": sorted(set(learned)),
            "removed_rule_ids": [],
        })
        if result["decision"] == "activated":
            self.store.mark_experience_consumed(used_exp_ids, result.get("run_id"))
            self.store.resolve_failure_cases(used_case_ids)
        else:
            # Kept for later re-evaluation: record the candidate run and reason.
            self.store.mark_experience_run(used_exp_ids, result.get("run_id"))
        return result

    def _auto_propose_from_failures(self, skill_name: str, tenant_id: str) -> Dict[str, Any]:
        failures = self.store.list_failure_cases(True, 100, tenant_id)
        # Work Package 9: low-trust feedback and unconfirmed missed_issue
        # signatures never directly generate a candidate (defaults preserve
        # the legacy behavior).
        confirmed = confirmed_missed_issue_keys(failures, self.min_confirmers)
        trusted = trusted_feedbacker_ids(
            failures, self.trust_enabled, self.trust_min_ratio
        )
        failures = [
            case for case in failures
            if not downgraded_feedbacker(case, trusted, self.trust_enabled)
            and (
                case.get("category") != "missed_issue"
                or confirmed is None
                or missed_issue_signature(case) in confirmed
            )
        ]
        active = self.store.get_active_skill_artifact(skill_name, tenant_id)
        artifact = dict(active["artifact"]) if active else self.empty_artifact(skill_name)
        rules = [dict(item) for item in artifact["rules"]]
        used_ids = []
        learned = []
        removed = []
        for case in failures:
            finding = (case.get("payload") or {}).get("finding") or {}
            rule_id = str(finding.get("rule_id", "")).strip().upper()
            if not RULE_ID.fullmatch(rule_id):
                continue
            if case.get("category") == "false_positive":
                before = len(rules)
                rules = [rule for rule in rules if rule["rule_id"] != rule_id]
                if len(rules) != before:
                    removed.append(rule_id)
                    used_ids.append(case["id"])
                continue
            if case.get("category") != "missed_issue":
                continue
            evidence = str(finding.get("evidence", "")).strip()
            if not evidence:
                evidence = self._evidence_from_task(case["task_id"], finding)
            if not evidence or len(evidence) > 240 or "\n" in evidence or "\r" in evidence:
                continue
            raw_rule = {
                "rule_id": rule_id, "severity": finding.get("severity", "medium"),
                "match": evidence, "ignore_case": False,
                "include_paths": [], "exclude_paths": ["tests/"],
                "title": finding.get("title"), "explanation": finding.get("explanation"),
                "fix": finding.get("fix"), "test": finding.get("test"),
                "confidence": finding.get("confidence", .85),
            }
            normalized = validate_artifact({"name": skill_name, "rules": [raw_rule]}, skill_name)["rules"][0]
            if not any(
                rule["rule_id"] == normalized["rule_id"] and rule["match"] == normalized["match"]
                for rule in rules
            ):
                rules.append(normalized)
                learned.append(rule_id)
                used_ids.append(case["id"])
        candidate = validate_artifact({**artifact, "name": skill_name, "rules": rules}, skill_name)
        if not used_ids:
            return {
                "version": None, "decision": "deferred",
                "reason": "no supported skill mutation signal was found in unresolved feedback",
                "failure_cases_used": len(failures), "learned_rule_ids": [],
                "removed_rule_ids": [], "run_id": None,
            }
        result = self.propose(
            skill_name, candidate, tenant_id,
            source_task_ids=sorted({case.get("task_id") for case in failures if case.get("id") in used_ids}),
            source_case_ids=sorted(used_ids),
        )
        result.update({
            "failure_cases_used": len(used_ids),
            "learned_rule_ids": sorted(set(learned)),
            "removed_rule_ids": sorted(set(removed)),
        })
        if result["decision"] == "activated":
            self.store.resolve_failure_cases(used_ids)
        return result

    def _evidence_from_task(self, task_id: str, finding: dict) -> str:
        diff = self.store.get_task_payload(task_id)
        if not diff:
            return ""
        try:
            path = str(finding.get("path", ""))
            line = int(finding.get("line", 0))
            for changed in parse_unified_diff(diff).added_lines:
                if changed.path == path and changed.line == line:
                    return changed.content.strip()
        except (TypeError, ValueError):
            return ""
        return ""

    def rollback(
        self, skill_name: str, version: int, tenant_id: str = "default",
    ) -> bool:
        return self.store.activate_skill_artifact(skill_name, version, tenant_id)

    @staticmethod
    def _empty_metrics(cases: int) -> Dict[str, Any]:
        return {
            "schema_version": 2, "reviewer": "", "score": 0.0,
            "precision": 0.0, "recall": 0.0, "f1": 0.0,
            "severity_accuracy": 0.0, "high_severity_recall": 0.0,
            "clean_accuracy": 0.0, "cases": cases, "positive_cases": 0,
            "clean_cases": 0, "expected_findings": 0, "predicted_findings": 0,
            "successful_cases": 0, "success_rate": 0.0, "errors": [],
            "case_results": [],
        }

    @staticmethod
    def _public_version(value: dict) -> dict:
        return {
            key: value[key] for key in (
                "tenant_id", "skill_name", "version", "score", "active", "parent_version",
                "artifact_sha256", "created_at",
                "status", "origin", "repository_scope", "provenance",
            ) if key in value
        }
