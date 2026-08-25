"""Replay report rendering."""
from typing import Any, Dict


def render_report(run: Dict[str, Any]) -> str:
    lines = [
        "Replay Report",
        "-------------",
        "Candidate: %s" % run.get("candidate_label", ""),
        "Baseline:  %s" % run.get("baseline_label", ""),
        "Decision:  %s" % run.get("decision", "PENDING"),
        "",
        "Metric                     Baseline    Candidate        Delta",
    ]
    metrics = run.get("metrics", {})
    baseline = metrics.get("baseline", {})
    candidate = metrics.get("candidate", {})
    deltas = metrics.get("deltas", {})
    keys = sorted(set(baseline) | set(candidate))
    for key in keys:
        b = baseline.get(key)
        c = candidate.get(key)
        d = deltas.get(key)
        line = "%-24s" % key
        line += "%10s" % ("%.4f" % (b or 0.0) if isinstance(b, (int, float)) else "-")
        line += "%16s" % ("%.4f" % (c or 0.0) if isinstance(c, (int, float)) else "-")
        line += "%16s" % ("%.4f" % (d or 0.0) if isinstance(d, (int, float)) else "-")
        lines.append(line)
    if run.get("reason"):
        lines += ["", "Reason: %s" % run["reason"]]
    return "\n".join(lines)