"""Final report generation for Evaluation Harness V2 (plan Phases 11-12).

Builds the machine-readable ``evaluation-report.json`` (section 15 schema) and the
human-readable ``evaluation-report.md`` (sections 14.1-14.4).  Nothing here re-runs
any experiment; it only renders already-computed result dicts so the report is a
pure function of the persisted JSON artifacts.
"""
import json
import os
from typing import Any, Dict, List, Optional

SYSTEM_ORDER = ("single_agent", "legacy_multi_agent", "current_harness", "evolved_candidate")
SYSTEM_LABELS = {
    "single_agent": "Single Agent",
    "legacy_multi_agent": "Legacy Multi-Agent",
    "current_harness": "Current Harness",
    "evolved_candidate": "Self-Evolved",
}
DETECTION_ROWS = (
    ("precision", "Precision"),
    ("recall", "Recall"),
    ("f1", "F1"),
    ("high_risk_recall", "High-risk Recall"),
    ("clean_accuracy", "Clean Accuracy"),
    ("execution_success_rate", "Execution Success"),
)


def _det(system_result: Optional[dict]) -> Dict[str, Any]:
    """Pull the detection-metrics dict out of an experiment/CLEAN result."""
    if not system_result:
        return {}
    return (system_result.get("metrics") or {}).get("detection", {}) or {}


def _runtime(system_result: Optional[dict]) -> Dict[str, Any]:
    if not system_result:
        return {}
    return (system_result.get("metrics") or {}).get("runtime", {}) or {}


def _pct(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    return "%.*f%%" % (digits, value * 100)


def _num(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return ("%.*f" % (digits, value))


def _int(value: Optional[int]) -> str:
    return "—" if value is None else str(int(value))


def _list_agents(value) -> str:
    """Render a list of specialist agent names compactly."""
    if not value:
        return "—"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _fmt_harness_cell(system_result, runtime: Dict[str, Any], key: str,
                      fmt) -> str:
    """Render one Harness-Engineering cell, tolerating absent systems.

    Returns ``—`` when the system did not run, or when a recovery rate is
    requested but the system recorded no recovery attempts (division-by-zero
    guard), so the report never shows a spurious ``0%``.
    """
    if not system_result:
        return "—"
    if key == "recovery_success_rate":
        attempts = int(runtime.get("recovery_attempts") or 0)
        if attempts == 0:
            return "—"
    return fmt(runtime.get(key))


# --------------------------------------------------------------------------- #
# JSON schema (plan section 15)
# --------------------------------------------------------------------------- #
def build_report(
    dataset_info: Dict[str, Any],
    systems: Dict[str, dict],
    evolution: Dict[str, Any],
    deployment: Dict[str, Any],
    ci_hard_gates: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the final report in the section-15 JSON shape.

    ``systems`` is keyed by the four ``SYSTEM_ORDER`` ids.  Full per-case lists are
    omitted here (they already live in ``case-results.jsonl``) to keep the report
    lean for CI parsing.
    """
    return {
        "schema_version": 2,
        "dataset": dataset_info,
        "systems": {
            key: _strip_case_results(systems[key]) if systems.get(key) else {}
            for key in SYSTEM_ORDER if key in systems
        },
        "evolution": evolution,
        "deployment": deployment,
        "ci_hard_gates": dict(ci_hard_gates or {}),
    }


def _strip_case_results(system_result: dict) -> dict:
    return {
        key: value for key, value in system_result.items()
        if key != "case_results"
    }


# --------------------------------------------------------------------------- #
# Markdown rendering (plan sections 14.1-14.4)
# --------------------------------------------------------------------------- #
def render_markdown(report: Dict[str, Any], dataset_label: str = "") -> str:
    lines: List[str] = []
    systems = report["systems"]
    evolution = report["evolution"]
    deployment = report["deployment"]
    ci_hard_gates = report.get("ci_hard_gates") or {}

    lines.append("# EvoReview-Agent — Evaluation Harness V2 Report")
    lines.append("")

    # ---- Architecture proof -------------------------------------------------
    lines.append("## Architecture Proof")
    lines.append("")
    lines.append("| System | Runtime | Candidate Skill |")
    lines.append("|---|---|---|")
    architecture_rows = (
        ("Single Agent", "LocalRuleReviewer", "No"),
        ("Legacy Multi-Agent", "Legacy MultiAgentCoordinator", "No"),
        ("Current", _runtime(systems.get("current_harness") or {}).get(
            "architecture") or "—", "No"),
        ("Self-Evolved", _runtime(systems.get("evolved_candidate") or {}).get(
            "architecture") or "—", str((evolution.get("candidate_manifest") or {}).get(
                "candidate_id") or "—")),
    )
    for row in architecture_rows:
        lines.append("| %s | %s | %s |" % row)
    lines.append("")
    lines.append("- Current architecture = `%s`" % architecture_rows[2][1])
    lines.append("- Self-Evolved architecture = `%s`" % architecture_rows[3][1])
    lines.append("")
    if dataset_label:
        lines.append("> %s" % dataset_label)
        lines.append("")
    lines.append(
        "> 100 受控 PR Diff Benchmark（40 风险 / 60 干净，Validation 80 / Holdout 20）。"
        "评分器固定（Path + CWE + line ±2，one-to-one），本轮只改变被评测系统。"
    )
    lines.append("")

    # ---- 14.1 Overall Comparison --------------------------------------------
    lines.append("## Overall Comparison")
    lines.append("")
    header = ["Metric"] + list(SYSTEM_LABELS[k] for k in SYSTEM_ORDER if k in systems)
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for key, label in DETECTION_ROWS:
        row = [label]
        for skey in SYSTEM_ORDER:
            if skey not in systems:
                continue
            det = _det(systems[skey])
            value = det.get(key)
            if key == "clean_accuracy":
                cell = _pct(value)
            elif key == "high_risk_recall":
                cell = _pct(value)
            else:
                cell = _pct(value)
            row.append(cell)
        lines.append("| " + " | ".join(row) + " |")
    # Explicit severity accounting prevents high-risk misses from being
    # mislabeled as critical misses.
    for key, label in (
        ("high_risk_total", "High-risk Total"),
        ("high_risk_hits", "High-risk Hits"),
        ("high_risk_misses", "High-risk Misses"),
        ("critical_total", "Critical Total"),
        ("critical_hits", "Critical Hits"),
        ("critical_misses", "Critical Misses"),
    ):
        row = [label]
        for skey in SYSTEM_ORDER:
            if skey in systems:
                row.append(_int(_det(systems[skey]).get(key)))
        lines.append("| " + " | ".join(row) + " |")
    p95_row = ["P95 Latency (ms)"]
    for skey in SYSTEM_ORDER:
        if skey not in systems:
            continue
        rt = _runtime(systems[skey])
        p95 = rt.get("p95_latency_ms")
        p95_row.append("—" if skey == "single_agent" else _num(p95, 1))
    lines.append("| " + " | ".join(p95_row) + " |")
    lines.append("")

    # ---- 14.2 Generalization ------------------------------------------------
    gen = evolution.get("holdout", {})
    stable = gen.get("stable") or {}
    evolved = gen.get("evolved") or {}
    if stable or evolved:
        lines.append("## Generalization (Holdout, unseen repositories)")
        lines.append("")
        header2 = ["Metric", "Stable Validation", "Evolved Validation",
                   "Stable Holdout", "Evolved Holdout"]
        lines.append("| " + " | ".join(header2) + " |")
        lines.append("|" + "---|" * len(header2))
        ev_val = evolution.get("validation", {})
        stable_val = _det(ev_val.get("stable") or {})
        evolved_val = _det(ev_val.get("evolved") or {})
        stable_ho = _det(stable)
        evolved_ho = _det(evolved)
        for key, label in DETECTION_ROWS:
            if key == "execution_success_rate":
                continue
            row = [label,
                   _pct(stable_val.get(key)), _pct(evolved_val.get(key)),
                   _pct(stable_ho.get(key)), _pct(evolved_ho.get(key))]
            lines.append("| " + " | ".join(row) + " |")
        for key, label in (
            ("high_risk_misses", "High-risk Misses"),
            ("critical_misses", "Critical Misses"),
        ):
            row = [label, _int(stable_val.get(key)), _int(evolved_val.get(key)),
                   _int(stable_ho.get(key)), _int(evolved_ho.get(key))]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # ---- Holdout Deltas (resume value) --------------------------------------
    if stable and evolved:
        s_f1 = _det(stable).get("f1")
        e_f1 = _det(evolved).get("f1")
        s_hr = _det(stable).get("high_risk_recall")
        e_hr = _det(evolved).get("high_risk_recall")
        s_crit = _critical(_det(stable))
        e_crit = _critical(_det(evolved))
        lines.append("### Holdout Deltas")
        lines.append("")
        if s_f1 is not None and e_f1 is not None:
            lines.append("- **Holdout F1**: %s → %s（%+.1f pp）" % (
                _pct(s_f1), _pct(e_f1), (e_f1 - s_f1) * 100))
        if s_hr is not None and e_hr is not None:
            lines.append("- **Holdout High-risk Recall**: %s → %s（%+.1f pp）" % (
                _pct(s_hr), _pct(e_hr), (e_hr - s_hr) * 100))
        lines.append("- **Critical Misses**: %s → %s" % (_int(s_crit), _int(e_crit)))
        lines.append("")

    # ---- 14.3 Harness Engineering -------------------------------------------
    cur = systems.get("current_harness")
    evo = systems.get("evolved_candidate")
    if cur or evo:
        lines.append("## Harness Engineering")
        lines.append("")
        header3 = ["Metric", "Current Harness", "Evolved"]
        lines.append("| " + " | ".join(header3) + " |")
        lines.append("|" + "---|" * len(header3))
        rows_def = (
            ("execution_success_rate", "Execution Success Rate", _pct),
            ("recovery_success_rate", "Recovery Success Rate", _pct),
            ("collaboration_detected", "Multi-Agent DAG Executed", _pct),
            ("activated_agents", "Specialist Agents Active", _list_agents),
            ("collaboration_rounds", "Collaboration Rounds (avg)", _num),
            ("collaboration_messages", "Collaboration Messages (avg)", _num),
            ("avg_agent_steps", "Avg Agent Tool-Steps", _num),
            ("avg_tool_calls", "Avg Tool Calls", _num),
            ("tool_denials", "Tool Denials", _int),
            ("p50_latency_ms", "P50 Latency (ms)", _num),
            ("p95_latency_ms", "P95 Latency (ms)", _num),
            ("decision_trace_coverage", "Trace Coverage", _pct),
            ("replay_snapshot_coverage", "Replay Snapshot Coverage", _pct),
        )
        for key, label, fmt in rows_def:
            cur_rt = _runtime(cur or {})
            evo_rt = _runtime(evo or {})
            if not cur and not evo:
                continue
            cur_cell = _fmt_harness_cell(cur, cur_rt, key, fmt)
            evo_cell = _fmt_harness_cell(evo, evo_rt, key, fmt)
            lines.append("| %s | %s | %s |" % (label, cur_cell, evo_cell))
        lines.append("")

    # ---- 14.4 Evolution Safety ----------------------------------------------
    gates = evolution.get("safety_gates") or {}
    gate_rows = gates.get("gates") or {}
    if gate_rows:
        lines.append("## Evolution Safety")
        lines.append("")
        header4 = ["Gate", "Result"]
        lines.append("| " + " | ".join(header4) + " |")
        lines.append("|---:|---|")
        order = [
            "Validation Improvement", "High-risk Non-regression",
            "Critical Miss Non-regression", "Clean Accuracy Non-regression",
            "Catastrophic Forgetting", "Runtime Safety", "Generalization",
        ]
        rows = list(gate_rows.items())
        rows.sort(key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
        for name, spec in rows:
            passed = spec.get("passed")
            status = "PASS" if passed else "FAIL"
            detail = spec.get("detail", "")
            lines.append("| %s | **%s** · %s |" % (name, status, detail))
        lines.append("")
        if gates.get("passed") is not None:
            lines.append("**Overall Safety Gate: %s**" %
                         ("PASS" if gates["passed"] else "FAIL"))
            lines.append("")

    # ---- Deployment (Canary / Rollback) -------------------------------------
    canary = deployment.get("canary") or {}
    rollback = deployment.get("rollback") or {}
    if canary or rollback:
        lines.append("## Canary / Rollback")
        lines.append("")
        if canary:
            lines.append("- **Canary promotion**: %s" % (
                canary.get("promotion_success") is not False and
                canary.get("stage_count") is not None and "PASS" or "FAIL"))
            lines.append("  - stages advanced: %s · exposure count: %s" % (
                canary.get("stage_count"), canary.get("exposure_count")))
        if rollback:
            lines.append("- **Auto rollback**: %s" % (
                "PASS" if rollback.get("auto_rollback_success") else "FAIL"))
            lines.append("  - traffic share after rollback: %s · previous-good restored: %s" % (
                rollback.get("traffic_share_after"), rollback.get("previous_good_restored")))
        lines.append("")
    if ci_hard_gates:
        lines.append("## Evaluation V2 CI Hard Gates")
        lines.append("")
        lines.append("| Gate | Result | Detail |")
        lines.append("|---|---:|---|")
        for name, spec in (ci_hard_gates.get("gates") or {}).items():
            lines.append("| %s | **%s** | %s |" % (
                name, "PASS" if spec.get("passed") else "FAIL",
                spec.get("detail") or ""))
        lines.append("")
        lines.append("**Overall CI Hard Gate: %s**" % (
            "PASS" if ci_hard_gates.get("passed") else "FAIL"))
        lines.append("")
    return "\n".join(lines)


def _critical(det: Dict[str, Any]) -> int:
    if "critical_misses" in det:
        return int(det.get("critical_misses") or 0)
    return int((det.get("critical_total") or 0) - (det.get("critical_hits") or 0))


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def write_report(
    out_dir: str,
    dataset_info: Dict[str, Any],
    systems: Dict[str, dict],
    evolution: Dict[str, Any],
    deployment: Dict[str, Any],
    dataset_label: str = "",
    ci_hard_gates: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    report = build_report(
        dataset_info, systems, evolution, deployment,
        ci_hard_gates=ci_hard_gates)
    with open(os.path.join(out_dir, "evaluation-report.json"), "w",
              encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, default=str, indent=2)
    markdown = render_markdown(report, dataset_label=dataset_label)
    with open(os.path.join(out_dir, "evaluation-report.md"), "w",
              encoding="utf-8") as handle:
        handle.write(markdown)
    return report


__all__ = [
    "build_report",
    "render_markdown",
    "write_report",
    "SYSTEM_ORDER",
    "SYSTEM_LABELS",
]
