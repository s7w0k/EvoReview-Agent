"""Run orchestration for Evaluation Harness V2.

``evaluate`` drives one adapter over one dataset, producing per-case results in the
V1 ``_run_case`` shape (so scoring stays identical) plus aggregate detection/runtime
metrics, and writes ``{name}.json`` and appends to ``case-results.jsonl``.
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

from evoagent.evaluation_harness import dataset_fingerprint, load_jsonl, validate_case
from .adapters import BaseEvaluationAdapter, EvaluationExecutionResult
from . import metrics

DATASET_PATH = "evaluation_data/pr_diff_100.jsonl"
DATASET_SHA256 = "88831bb19264f9fc15433de7801b623aad38b80076f5d5b085d0299fd40cc115"


def load_dataset(path: str = DATASET_PATH, verify_sha: Optional[str] = None) -> List[dict]:
    """Load and, optionally, verify the frozen dataset fingerprint."""
    cases = load_jsonl(path)
    fingerprint = dataset_fingerprint(cases)
    if verify_sha and fingerprint != verify_sha:
        raise ValueError(
            "dataset fingerprint mismatch: expected %s got %s" % (verify_sha, fingerprint))
    return cases


def split_cases(cases: List[dict], split: Optional[str]) -> List[dict]:
    if not split:
        return cases
    selected = [case for case in cases if case.get("split") == split]
    if not selected:
        raise ValueError("no cases found for split=%r" % split)
    return selected


def evaluate(
    adapter: BaseEvaluationAdapter,
    cases: List[dict],
    name: str = "",
    out_dir: str = "output/evaluation_v2",
    write: bool = True,
) -> Dict[str, Any]:
    """Run ``adapter`` over ``cases``; return (and optionally persist) results."""
    started = time.monotonic()
    case_results: List[Dict[str, Any]] = []
    for case in cases:
        validate_case(case)
        execution = adapter.review_case(case)
        if not isinstance(execution, EvaluationExecutionResult):
            execution = EvaluationExecutionResult(**execution)
        case_results.append(metrics.score_case(case, execution))
    result = {
        "schema_version": 2,
        "name": name or adapter.name,
        "system": adapter.name,
        "dataset": {
            "cases": len(cases),
            "repositories": len({case["repository"] for case in cases}),
            "risk_cases": sum(bool(case["expected_findings"]) for case in cases),
            "clean_cases": sum(not case["expected_findings"] for case in cases),
            "sha256": dataset_fingerprint(cases),
        },
        "metrics": metrics.summarize(case_results),
        "duration_seconds": round(time.monotonic() - started, 4),
        "case_results": case_results,
    }
    if write:
        _write_result(result, out_dir)
    return result


def _write_result(result: Dict[str, Any], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    name = result["name"]
    with open(os.path.join(out_dir, "%s.json" % name), "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, default=str, indent=2)
    with open(os.path.join(out_dir, "case-results.jsonl"), "a", encoding="utf-8") as handle:
        for case_result in result["case_results"]:
            handle.write(json.dumps(case_result, ensure_ascii=False, default=str) + "\n")


__all__ = [
    "DATASET_PATH",
    "DATASET_SHA256",
    "load_dataset",
    "split_cases",
    "evaluate",
]