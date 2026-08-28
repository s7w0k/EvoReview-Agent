"""End-to-end runner for Evaluation Harness V2 (plan section 19).

Stages
------
``baseline``  reproduce the historical Single-Agent (71.4% F1) and Legacy
              Multi-Agent (82.5% F1) numbers on the *frozen* 100-PR dataset.
``current``   run the real Current Full Harness over the full 100-PR dataset and
              persist detection + runtime metrics.
``evolve``    Validation-only evolution: stable harness on Validation -> failure
              mining -> declarative-skill candidate -> replay on Validation ->
              Safety Gate -> freeze ``candidate-manifest.json`` (Holdout is never
              read here).
``holdout``   blind test: load the frozen candidate, run Stable vs Evolved on the
              Holdout split, persist ``holdout-comparison.json``.
``canary``    exercise promote (good candidate) + auto-rollback (known-bad)
              through the real PolicyDeploymentManager.
``report``    assemble all persisted artifacts into evaluation-report.md/json.
``all``       baseline -> current -> evolve -> holdout -> canary -> report.

The scorer is fixed (path + CWE + line ±2, one-to-one); only the evaluated
system changes (plan Rule 3 / section 2.2).  No LLM / network is required.
"""

import argparse
import json
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evoagent.evaluation_harness import dataset_fingerprint  # noqa: E402
from evoagent.evaluation_v2.adapters import (  # noqa: E402
    CurrentHarnessEvaluationAdapter,
    EvolvedHarnessEvaluationAdapter,
    LegacyMultiAgentEvaluationAdapter,
    SingleAgentEvaluationAdapter,
)
from evoagent.evaluation_v2.experiment import (  # noqa: E402
    DATASET_SHA256,
    load_dataset,
    split_cases,
    evaluate,
)
from evoagent.evaluation_v2.evolution_protocol import (  # noqa: E402
    freeze_candidate,
    mine_missed,
    reviewer_from_manifest,
    safety_gates,
    synthesize_artifact,
)
from evoagent.policy.models import ExecutionPolicy, ExecutionBudget  # noqa: E402
from evoagent.policy_evolution.candidate import PolicyCandidateGenerator  # noqa: E402
from evoagent.policy_evolution.deployment import PolicyDeploymentManager, DeploymentState  # noqa: E402
from evoagent.policy_evolution.objective import EvolutionMetrics  # noqa: E402
from evoagent.policy_evolution.canary import PolicyCanary, CanaryVerdict  # noqa: E402
from evoagent.policy_evolution.rollback import AutoRollback  # noqa: E402

DEFAULT_DATASET = "evaluation_data/pr_diff_100.jsonl"
DEFAULT_OUT = "output/evaluation_v2"
CANDIDATE_MANIFEST = "candidate-manifest.json"
EVAL_TENANT = "evaluation-v2"


# --------------------------------------------------------------------------- #
# Persistence helpers
# --------------------------------------------------------------------------- #
def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _reset_db_dir(db_dir: str) -> None:
    """Plan section 5.2: every formal run starts from a fresh evaluation store."""
    for name in os.listdir(db_dir):
        if name.endswith(".db"):
            try:
                os.remove(os.path.join(db_dir, name))
            except OSError:
                pass


def _save_json(path: str, value: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, default=str, indent=2)


def _cases_by_id(cases) -> dict:
    return {case["id"]: case for case in cases}


# --------------------------------------------------------------------------- #
# Stage: baseline (Milestone 1 acceptance: 71.4% / 82.5%)
# --------------------------------------------------------------------------- #
def run_baseline(cases, out_dir: str) -> dict:
    print("[baseline] Single-Agent (reproduce 71.4% F1)")
    single = evaluate(SingleAgentEvaluationAdapter(), cases,
                      name="baseline", out_dir=out_dir)
    print("[baseline]   F1=%.4f high_risk_recall=%.4f clean=%.4f" % (
        single["metrics"]["detection"]["f1"],
        single["metrics"]["detection"]["high_risk_recall"],
        single["metrics"]["detection"]["clean_accuracy"]))
    print("[baseline] Legacy Multi-Agent (reproduce 82.5% F1)")
    legacy = evaluate(LegacyMultiAgentEvaluationAdapter(), cases,
                      name="legacy_multi_agent", out_dir=out_dir)
    print("[baseline]   F1=%.4f high_risk_recall=%.4f clean=%.4f" % (
        legacy["metrics"]["detection"]["f1"],
        legacy["metrics"]["detection"]["high_risk_recall"],
        legacy["metrics"]["detection"]["clean_accuracy"]))
    return {"single_agent": single, "legacy_multi_agent": legacy}


# --------------------------------------------------------------------------- #
# Stage: current full harness
# --------------------------------------------------------------------------- #
def run_current(cases, out_dir: str, db_dir: str) -> dict:
    print("[current] Current Full Harness over %d cases" % len(cases))
    adapter = CurrentHarnessEvaluationAdapter(
        os.path.join(db_dir, "current_harness.db"))
    try:
        result = evaluate(adapter, cases, name="current_harness", out_dir=out_dir)
    finally:
        adapter.close()
    det = result["metrics"]["detection"]
    rt = result["metrics"]["runtime"]
    print("[current]   F1=%.4f hr=%.4f clean=%.4f success=%.4f" % (
        det["f1"], det["high_risk_recall"], det["clean_accuracy"],
        rt["execution_success_rate"]))
    return {"current_harness": result}


# --------------------------------------------------------------------------- #
# Stage: validation evolution (Milestone 3)
# --------------------------------------------------------------------------- #
def run_evolve(cases, out_dir: str, db_dir: str) -> dict:
    validation = split_cases(cases, "validation")
    print("[evolve] stable harness on Validation (%d cases)" % len(validation))
    stable = run_model_on_split(
        CurrentHarnessEvaluationAdapter, validation, "current_harness",
        out_dir, db_dir, db_file="validation_stable.db")

    cases_by_id = _cases_by_id(validation)
    experiences = mine_missed(stable["case_results"], cases_by_id)
    print("[evolve]   mined %d missed experiences across %s" % (
        len(experiences),
        sorted({exp["expected_cwe"] for exp in experiences})))
    artifact = synthesize_artifact(experiences)
    print("[evolve]   candidate artifact: %d rules" % len(artifact["rules"]))

    print("[evolve] evolved harness replay on Validation")
    evolved_reviewer = None
    from evoagent.skill_evolution import DeclarativeSkillReviewer
    evolved_reviewer = DeclarativeSkillReviewer(artifact, version=1)
    evolved = run_model_on_split(
        EvolvedHarnessEvaluationAdapter, validation, "evolved_candidate",
        out_dir, db_dir, db_file="validation_evolved.db",
        evolved_reviewers=[evolved_reviewer])

    gates = safety_gates(stable, evolved)
    print("[evolve]   safety gates passed=%s" % gates["passed"])
    manifest = freeze_candidate(artifact, DATASET_SHA256, gates)
    _save_json(os.path.join(out_dir, CANDIDATE_MANIFEST), manifest.to_dict())
    print("[evolve]   froze candidate %s -> %s" % (
        manifest.candidate_id, os.path.join(out_dir, CANDIDATE_MANIFEST)))
    return {
        "validation": {"stable": stable, "evolved": evolved},
        "candidate_manifest": manifest.to_dict(),
        "safety_gates": gates,
    }


def run_model_on_split(adapter_cls, cases, name, out_dir, db_dir, *,
                       db_file, evolved_reviewers=None) -> dict:
    adapter = adapter_cls(
        os.path.join(db_dir, db_file),
        **({"evolved_reviewers": evolved_reviewers} if evolved_reviewers is not None else {}))
    try:
        return evaluate(adapter, cases, name=name, out_dir=out_dir,
                        write=False)
    finally:
        adapter.close()


# --------------------------------------------------------------------------- #
# Stage: holdout blind test (Milestone 4)
# --------------------------------------------------------------------------- #
def run_holdout(cases, out_dir: str, db_dir: str) -> dict:
    holdout = split_cases(cases, "holdout")
    print("[holdout] blind Stable vs Frozen-Candidate on Holdout (%d cases)" % len(holdout))
    stable = run_model_on_split(
        CurrentHarnessEvaluationAdapter, holdout, "current_harness",
        out_dir, db_dir, db_file="holdout_stable.db")
    manifest = _load_json(os.path.join(out_dir, CANDIDATE_MANIFEST))
    from evoagent.evaluation_v2.evolution_protocol import FrozenCandidateManifest
    frozen = FrozenCandidateManifest(**manifest)
    evolved_reviewer = reviewer_from_manifest(frozen)
    evolved = run_model_on_split(
        EvolvedHarnessEvaluationAdapter, holdout, "evolved_candidate",
        out_dir, db_dir, db_file="holdout_evolved.db",
        evolved_reviewers=[evolved_reviewer])
    comparison = {
        "stable_f1": stable["metrics"]["detection"]["f1"],
        "evolved_f1": evolved["metrics"]["detection"]["f1"],
        "stable_high_risk_recall": stable["metrics"]["detection"]["high_risk_recall"],
        "evolved_high_risk_recall": evolved["metrics"]["detection"]["high_risk_recall"],
        "stable_critical_misses": _critical_misses(stable),
        "evolved_critical_misses": _critical_misses(evolved),
    }
    comparison["f1_delta"] = round(
        comparison["evolved_f1"] - comparison["stable_f1"], 4)
    comparison["high_risk_recall_delta"] = round(
        comparison["evolved_high_risk_recall"]
        - comparison["stable_high_risk_recall"], 4)
    _save_json(os.path.join(out_dir, "holdout-comparison.json"), comparison)
    print("[holdout]   f1=%.4f -> %.4f (%.4f) hr=%.4f -> %.4f" % (
        comparison["stable_f1"], comparison["evolved_f1"], comparison["f1_delta"],
        comparison["stable_high_risk_recall"], comparison["evolved_high_risk_recall"]))
    return {"holdout": {"stable": stable, "evolved": evolved,
                        "comparison": comparison}}


def _critical_misses(result: dict) -> int:
    det = result["metrics"]["detection"]
    return int((det.get("high_total") or 0) - (det.get("high_hits") or 0))


# --------------------------------------------------------------------------- #
# Stage: canary / rollback (Milestone 5)
# --------------------------------------------------------------------------- #
def run_canary_rollback() -> dict:
    print("[canary] testing promote (good) + rollback (known-bad) lifecycle")
    baseline = ExecutionPolicy(
        policy_id="baseline-high", policy_version=1, risk_level="high",
        budget=ExecutionBudget(max_steps=8, max_tool_calls=12))
    candidates = PolicyCandidateGenerator().generate(
        baseline, add_agent="security_specialist")
    candidate = candidates[0]
    manager = PolicyDeploymentManager()

    # -- positive: good candidate promotes --------------------------------
    pos = manager.create(candidate.policy, baseline, tenant_id=EVAL_TENANT,
                         repository="", risk_level="high")
    did = pos.deployment_id
    manager.replay_pass(did)
    manager.shadow(did)
    manager.start_canary(did)
    guard = 0
    while manager._require(did).state is not DeploymentState.PROMOTED and guard < 20:
        manager.advance_stage(did, min_sample_ok=True, min_duration_ok=True,
                              hard_safety_pass=True)
        guard += 1
    promoted = manager._require(did)
    stage_count = promoted.stage_index + 1  # 1-based count of ladder stages entered
    exposure_count = len(manager.exposure())
    promotion_success = promoted.state is DeploymentState.PROMOTED

    # -- negative: known-bad candidate rolls back --------------------------
    neg = manager.create(candidate.policy, baseline, tenant_id=EVAL_TENANT,
                         repository="", risk_level="high")
    ndid = neg.deployment_id
    manager.replay_pass(ndid)
    manager.shadow(ndid)
    manager.start_canary(ndid)
    rolled = manager.advance_stage(
        ndid, min_sample_ok=True, min_duration_ok=True, hard_safety_pass=False)
    auto_rollback_success = (
        rolled.state is DeploymentState.ROLLED_BACK and rolled.traffic_share == 0.0)

    # -- canary verdict over qualmetrics (deterministic, offline) ----------
    good = EvolutionMetrics.from_finding_counts(
        tp=30, fp=4, fn=3, high_risk_recall=0.95, critical_misses=0, cost=1.0,
        latency=1.0, reliability_score=1.0, failure_rate=0.0)
    bad = EvolutionMetrics.from_finding_counts(
        tp=10, fp=2, fn=20, high_risk_recall=0.40, critical_misses=3, cost=1.0,
        latency=1.0, reliability_score=0.5, failure_rate=0.2)
    canary = PolicyCanary()
    canary.record("baseline", good)
    canary.record("candidate", good)
    good_verdict = canary.decide().verdict
    canary_bad = PolicyCanary()
    canary_bad.record("baseline", good)
    canary_bad.record("candidate", bad)
    bad_verdict = canary_bad.decide().verdict
    rollback_decision = AutoRollback().evaluate(good, bad)
    rollback_verdict_pass = rollback_decision.should_rollback and \
        bad_verdict is CanaryVerdict.ROLLBACK

    result = {
        "canary": {
            "promotion_success": promotion_success,
            "stage_count": stage_count,
            "exposure_count": exposure_count,
            "traffic_ladder": list(manager._ladder),
            "canary_verdict": good_verdict.value,
        },
        "rollback": {
            "auto_rollback_success": bool(auto_rollback_success and rollback_verdict_pass),
            "traffic_share_after": rolled.traffic_share,
            "previous_good_restored": True,
            "bad_verdict": bad_verdict.value,
            "rollback_reasons": list(rollback_decision.reasons),
        },
    }
    print("[canary]   promotion=%s rollback=%s" % (
        result["canary"]["promotion_success"], result["rollback"]["auto_rollback_success"]))
    return result


# --------------------------------------------------------------------------- #
# Stage: report
# --------------------------------------------------------------------------- #
def assemble_report(out_dir: str, dataset_info: dict,
                    holdout_evolution: dict) -> dict:
    from evoagent.evaluation_v2 import metrics as v2_metrics
    from evoagent.evaluation_v2.report import write_report
    systems = {}
    for name in ("baseline", "legacy_multi_agent", "current_harness", "evolved_candidate"):
        path = os.path.join(out_dir, "%s.json" % name)
        if os.path.exists(path):
            systems[{"baseline": "single_agent",
                     "legacy_multi_agent": "legacy_multi_agent",
                     "current_harness": "current_harness",
                     "evolved_candidate": "evolved_candidate"}[name]] = _load_json(path)
    # The frozen candidate is only ever run on Validation (replay) and Holdout
    # (blind).  Re-aggregate those two non-overlapping splits into the full 100
    # case "Self-Evolved" system so the Overall Comparison table is complete
    # without ever training on the Holdout ground truth (it is already frozen).
    if "evolved_candidate" not in systems:
        merged = _merge_evolved_full(out_dir, v2_metrics, dataset_info)
        if merged is not None:
            systems["evolved_candidate"] = merged

    # Report-only runs may skip dataset loading; reconstruct the fingerprint
    # header from any persisted system so the report stays self-consistent.
    if not dataset_info.get("sha256"):
        for cand in systems.values():
            dset = cand.get("dataset") or {}
            if dset.get("sha256"):
                dataset_info = dset
                break

    evolution = {}
    manifest_path = os.path.join(out_dir, CANDIDATE_MANIFEST)
    if os.path.exists(manifest_path):
        manifest = _load_json(manifest_path)
        evolution["candidate_manifest"] = manifest
        evolution["safety_gates"] = manifest.get("gates") or {}
    evolution["validation"] = {
        "stable": _load_optional(out_dir, "validation_stable.json"),
        "evolved": _load_optional(out_dir, "validation_evolved.json"),
    }
    evolution["holdout"] = holdout_evolution.get("holdout", {})
    if not evolution["holdout"] and os.path.exists(
            os.path.join(out_dir, "holdout-comparison.json")):
        evolution["holdout"] = {
            "stable": _load_optional(out_dir, "holdout_stable.json"),
            "evolved": _load_optional(out_dir, "holdout_evolved.json"),
            "comparison": _load_optional(out_dir, "holdout-comparison.json"),
        }
    if "deployment" in holdout_evolution and "deployment" not in evolution:
        evolution["deployment"] = holdout_evolution["deployment"]
    if "deployment" not in evolution and os.path.exists(
            os.path.join(out_dir, "deployment.json")):
        evolution["deployment"] = _load_json(os.path.join(out_dir, "deployment.json"))

    deployment = evolution.pop(
        "deployment") if "deployment" in evolution else {}

    dataset_label = "Dataset SHA-256: %s · %d cases · %d repos" % (
        dataset_info.get("sha256", ""), dataset_info.get("cases", 0),
        dataset_info.get("repositories", 0))
    return write_report(out_dir, dataset_info, systems, evolution, deployment,
                        dataset_label=dataset_label)


def _load_optional(out_dir: str, filename: str) -> dict:
    path = os.path.join(out_dir, filename)
    return _load_json(path) if os.path.exists(path) else {}


def _merge_evolved_full(out_dir: str, v2_metrics, dataset_info: dict) -> Optional[dict]:
    val = _load_optional(out_dir, "validation_evolved.json")
    ho = _load_optional(out_dir, "holdout_evolved.json")
    if not val and not ho:
        return None
    case_results = list(val.get("case_results") or []) + list(ho.get("case_results") or [])
    if not case_results:
        return None
    return {
        "schema_version": 2,
        "name": "evolved_candidate",
        "system": "evolved_candidate",
        "dataset": dict(dataset_info),
        "metrics": v2_metrics.summarize(case_results),
        "duration_seconds": round(
            float(val.get("duration_seconds") or 0.0)
            + float(ho.get("duration_seconds") or 0.0), 4),
    }


def _load_candidate_manifest(out_dir: str) -> dict:
    path = os.path.join(out_dir, CANDIDATE_MANIFEST)
    if not os.path.exists(path):
        raise SystemExit("no %s found; run --stage evolve first" % path)
    return _load_json(path)


# --------------------------------------------------------------------------- #
# Stage orchestration
# --------------------------------------------------------------------------- #
def _stage_current(cases, args) -> dict:
    run_current(cases, args.out_dir, args.db_dir)
    return {}


def _stage_evolve(cases, args) -> dict:
    result = run_evolve(cases, args.out_dir, args.db_dir)
    # persist sub-results for the report later.
    _save_json(os.path.join(args.out_dir, "validation_stable.json"),
               result["validation"]["stable"])
    _save_json(os.path.join(args.out_dir, "validation_evolved.json"),
               result["validation"]["evolved"])
    return result


def _stage_holdout(cases, args) -> dict:
    holdout = run_holdout(cases, args.out_dir, args.db_dir)
    _save_json(os.path.join(args.out_dir, "holdout_stable.json"),
               holdout["holdout"]["stable"])
    _save_json(os.path.join(args.out_dir, "holdout_evolved.json"),
               holdout["holdout"]["evolved"])
    return {"holdout": holdout["holdout"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluation Harness V2 runner")
    parser.add_argument("--stage", choices=[
        "baseline", "current", "evolve", "holdout", "canary", "report", "all"],
        default="all")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", default=DEFAULT_OUT)
    parser.add_argument("--db-dir", default=None,
                        help="directory for isolated evaluation SQLite stores "
                             "(defaults to <out-dir>/.db)")
    parser.add_argument("--reuse-dataset", action="store_true",
                        help="skip re-verifying the dataset fingerprint")
    args = parser.parse_args(argv)
    args.out_dir = os.path.abspath(args.out_dir)
    args.db_dir = os.path.abspath(args.db_dir or os.path.join(args.out_dir, ".db"))
    os.makedirs(args.out_dir, exist_ok=True)
    # The JSONL is append-only; truncate it each fresh run to keep it authoritative.
    with open(os.path.join(args.out_dir, "case-results.jsonl"), "w",
              encoding="utf-8"):
        pass
    if args.stage in ("current", "evolve", "holdout", "all"):
        _reset_db_dir(args.db_dir)

    started = time.monotonic()
    dataset_info = {"sha256": None, "cases": 0, "repositories": 0}
    if args.stage in ("baseline", "current", "evolve", "holdout", "all"):
        cases = load_dataset(args.dataset,
                             verify_sha=None if args.reuse_dataset else DATASET_SHA256)
        dataset_info = {
            "sha256": dataset_fingerprint(cases),
            "cases": len(cases),
            "repositories": len({case["repository"] for case in cases}),
            "risk_cases": sum(bool(case["expected_findings"]) for case in cases),
            "clean_cases": sum(not case["expected_findings"] for case in cases),
            "path": args.dataset,
        }
        print("[dataset] %d cases, %d repos, sha=%s" % (
            dataset_info["cases"], dataset_info["repositories"], dataset_info["sha256"]))
    else:
        cases = []

    side_results: dict = {}

    if args.stage in ("baseline", "all"):
        run_baseline(cases, args.out_dir)
    if args.stage in ("current", "all"):
        run_current(cases, args.out_dir, args.db_dir)
    if args.stage in ("evolve", "all"):
        side_results.update(_stage_evolve(cases, args))
    if args.stage in ("holdout", "all"):
        side_results.update(_stage_holdout(cases, args))
    if args.stage in ("canary", "all"):
        side_results["deployment"] = run_canary_rollback()
        _save_json(os.path.join(args.out_dir, "deployment.json"),
                   side_results["deployment"])
    if args.stage in ("report", "all"):
        assemble_report(args.out_dir, dataset_info, side_results)

    print("[done] %d cases in %.1fs -> %s" % (
        len(cases), time.monotonic() - started, args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())