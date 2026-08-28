"""Evaluation V4 report builder (plan §9.7)."""
from typing import Any, Dict, List

from .ablation import build_ablation_matrix
from .metrics import aggregate_metrics, evaluate_run


def _score_row(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    return aggregate_metrics([evaluate_run(r) for r in records])


def build_report(results: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Turn per-variant records into a report with an ablation summary."""
    matrix = {r["variant"]: r["name"] for r in build_ablation_matrix()}
    per_variant: Dict[str, Dict[str, Any]] = {}
    for variant, records in results.items():
        per_variant[variant] = {
            "name": matrix.get(variant, variant),
            "records": len(records),
            "metrics": _score_row(records),
        }
    order = [v for v in ("A", "B", "C", "D", "E", "F", "G") if v in per_variant]
    baseline = per_variant.get("A")
    deltas: Dict[str, Dict[str, float]] = {}
    if baseline:
        for variant, row in per_variant.items():
            if variant == "A":
                continue
            flat = row["metrics"]
            base_flat = baseline["metrics"]
            deltas[variant] = {
                dim: round(flat.get(dim, 0.0) - base_flat.get(dim, 0.0), 4)
                for dim in ("planning_quality", "replan_quality",
                            "collaboration_quality", "loop_quality", "efficiency")
            }
    return {
        "variants": [per_variant[v] for v in order],
        "order": order,
        "baseline_variant": "A",
        "ablation_deltas": deltas,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = ["# Multi-Agent Value Evaluation V4", ""]
    dims = ("planning_quality", "replan_quality", "collaboration_quality",
            "loop_quality", "efficiency", "overall")
    header = "| Variant | " + " | ".join(dims) + " |"
    sep = "|" + "---|" * (len(dims) + 1)
    lines += [header, sep]
    for row in report["variants"]:
        m = row["metrics"]
        cells = " | ".join(str(m.get(d, 0.0)) for d in dims)
        lines.append("| %s | %s |" % (row["name"], cells))
    lines += ["", "## Ablation deltas vs baseline (A)", "", 
              "| Variant | delta overall |", "|---|---|"]
    for variant, delta in sorted(report.get("ablation_deltas", {}).items(),
                                 key=lambda kv: kv[0]):
        overall = sum(delta.values()) / len(delta) if delta else 0.0
        lines.append("| %s | %.4f |" % (variant, round(overall, 4)))
    return "\n".join(lines) + "\n"


__all__ = ["build_report", "render_markdown"]