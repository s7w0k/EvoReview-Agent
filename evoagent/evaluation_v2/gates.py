"""Deterministic CI hard gates for the Evaluation V2 wiring contract."""
from typing import Any, Dict, Iterable, List

from .diagnostics import finding_schema_violations, produced_rule_mapping_coverage
from .experiment import DATASET_SHA256


def _cases(system: dict) -> List[dict]:
    return list((system or {}).get("case_results") or [])


def _detection(system: dict) -> dict:
    return ((system or {}).get("metrics") or {}).get("detection") or {}


def _runtime(system: dict) -> dict:
    return ((system or {}).get("metrics") or {}).get("runtime") or {}


def _runtime_signature(result: dict) -> dict:
    return {
        "architecture": result.get("architecture"),
        "feature_flags": result.get("feature_flags") or {},
        "resolved_policy": result.get("resolved_policy") or {},
    }


def _configs_identical(stable: Iterable[dict], evolved: Iterable[dict]) -> bool:
    evolved_by_id = {item.get("id"): item for item in evolved}
    compared = 0
    for item in stable:
        peer = evolved_by_id.get(item.get("id"))
        if peer is None:
            continue
        compared += 1
        if _runtime_signature(item) != _runtime_signature(peer):
            return False
    return compared > 0


def build_ci_hard_gates(
    dataset_info: Dict[str, Any], systems: Dict[str, dict],
    evolution: Dict[str, Any],
) -> Dict[str, Any]:
    single = systems.get("single_agent") or {}
    legacy = systems.get("legacy_multi_agent") or {}
    current = systems.get("current_harness") or {}
    evolved = systems.get("evolved_candidate") or {}
    current_cases = _cases(current)
    evolved_cases = _cases(evolved)
    manifest = evolution.get("candidate_manifest") or {}
    candidate_id = str(manifest.get("candidate_id") or "")

    validation = evolution.get("validation") or {}
    holdout = evolution.get("holdout") or {}
    stable_runs = (_cases(validation.get("stable") or {})
                   + _cases(holdout.get("stable") or {}))
    evolved_runs = (_cases(validation.get("evolved") or {})
                    + _cases(holdout.get("evolved") or {}))

    schema_violations = [
        {"case_id": item.get("id"), "violations": errors}
        for item in current_cases + evolved_cases
        for errors in [finding_schema_violations(
            item.get("prediction_details") or [])]
        if errors
    ]
    mapping = produced_rule_mapping_coverage(current_cases + evolved_cases)
    candidate_invocations = sum(
        int((item.get("skill_invocations") or {}).get(candidate_id, 0))
        for item in evolved_cases) if candidate_id else 0

    def gate(passed: bool, detail: str, value: Any = None) -> Dict[str, Any]:
        result = {"passed": bool(passed), "detail": detail}
        if value is not None:
            result["value"] = value
        return result

    single_f1 = _detection(single).get("f1")
    legacy_f1 = _detection(legacy).get("f1")
    current_tp = int(_detection(current).get("tp") or 0)
    current_success = float(_runtime(current).get("execution_success_rate") or 0.0)
    current_wiring = bool(current_cases) and all(
        item.get("architecture") == "six-agent-v2"
        and item.get("graph_shapes") and item.get("called_agents")
        for item in current_cases)
    evolved_wiring = bool(evolved_cases) and all(
        item.get("architecture") == "six-agent-v2"
        and item.get("graph_shapes") and item.get("called_agents")
        for item in evolved_cases)

    gates = {
        "Dataset SHA unchanged": gate(
            dataset_info.get("sha256") == DATASET_SHA256,
            "frozen canonical dataset fingerprint", dataset_info.get("sha256")),
        "Single baseline reproduced": gate(
            single_f1 == 0.7143, "Single Agent F1 must remain 0.7143", single_f1),
        "Legacy baseline reproduced": gate(
            legacy_f1 == 0.825, "Legacy Multi-Agent F1 must remain 0.8250", legacy_f1),
        "Current runtime is six-agent-v2": gate(
            current_wiring, "all Current cases expose architecture, graph and agents"),
        "Evolved runtime is six-agent-v2": gate(
            evolved_wiring, "all Evolved cases expose architecture, graph and agents"),
        "Stable/Evolved runtime config identical": gate(
            _configs_identical(stable_runs, evolved_runs),
            "candidate skill is the only runtime configuration difference"),
        "Candidate skill invocation confirmed": gate(
            candidate_invocations > 0,
            "%s invocation count" % (candidate_id or "candidate"),
            candidate_invocations),
        "Finding schema valid": gate(
            not schema_violations, "path/line/rule/severity contract", schema_violations),
        "Produced Rule IDs mapping coverage": gate(
            mapping["coverage"] == 1.0, "all produced Rule IDs map to CWE", mapping),
        "Current Harness TP positive": gate(
            current_tp > 0, "wiring regression guard TP > 0", current_tp),
        "Current Harness execution success": gate(
            current_success == 1.0, "execution success must be 100%", current_success),
        "Holdout isolation intact": gate(
            manifest.get("created_from_split") == "validation"
            and manifest.get("validation_dataset_sha256") == DATASET_SHA256,
            "candidate frozen from Validation before blind Holdout"),
    }
    return {"passed": all(item["passed"] for item in gates.values()), "gates": gates}


__all__ = ["build_ci_hard_gates"]
