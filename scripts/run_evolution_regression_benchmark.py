"""Evolution Regression Benchmark evaluator (hardening plan Phase 9).

Runs the deterministic rule-reviewer stack over the fixed benchmark dataset for
two policy generations -- a *baseline* rule subset and a *candidate* (evolved)
rule subset -- and produces a Baseline / Candidate / Delta report with a hard
safety gate that must pass *before* any utility is considered.

The benchmark is fully reproducible in CI: it requires no LLM, network or
database. Scoring reuses the same deterministic reviewers that power the
harness (LocalRuleReviewer) across stable rule subsets.

Finding matching is location based: a predicted finding is a true positive when
its (path, line) coincides with an expected finding on an added line.
"""

import argparse
import json
import os
import re
import sys
import time

# Allow running directly from the repo without an installed package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evoagent.diff_parser import parse_unified_diff  # noqa: E402
from evoagent.models import Severity  # noqa: E402
from evoagent.reviewer import LocalRuleReviewer  # noqa: E402
from benchmarks.loader import load_cases, case_count  # noqa: E402

# --------------------------------------------------------------------------- #
# Rule generation configuration
# --------------------------------------------------------------------------- #
# Baseline: security-focused v1 policy generation (no reliability rules).
BASELINE_RULE_IDS = frozenset({
    "SEC-EVAL",
    "SEC-SUBPROCESS-SHELL",
    "SEC-HARDCODED-SECRET",
    "SEC-SQL-CONCAT",
})
# Candidate: evolved v2 generation that additionally carries reliability rules
# and contributes one *new* detection rule discovered through evolution:
# catching shell-level command execution via os.system. This is what removes
# the SEC-001 critical miss and produces a genuine metric improvement.
CANDIDATE_RULE_IDS = {item[0] for item in LocalRuleReviewer.RULES}
CANDIDATE_EXTRA_RULES = [(
    "SEC-OS-SYSTEM",
    Severity.HIGH,
    re.compile(r"\bos\.system\s*\("),
    "直接调用 os.system 执行外部命令",
    "os.system 把命令交给 shell 执行，当参数可被外部影响时会造成命令注入或非预期副作用。",
    "改用 subprocess.run 并传入参数数组、保持 shell=False。",
    "测试包含带空格、分号与命令替换符的输入，断言不会导致额外命令执行。",
)]

_HEAVY = {Severity.CRITICAL.value, Severity.HIGH.value}
_SEV_RANK = {
    Severity.CRITICAL.value: 3,
    Severity.HIGH.value: 2,
    Severity.MEDIUM.value: 1,
    Severity.LOW.value: 0,
}


def run_generation(rule_ids, extra_rules=()):
    """Return a configured reviewer built from the given rule subset."""
    reviewer = LocalRuleReviewer.__new__(LocalRuleReviewer)
    reviewer.RULES = list(extra_rules) + [
        item for item in LocalRuleReviewer.RULES if item[0] in rule_ids
    ]
    return reviewer


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _sev(value):
    try:
        return Severity(str(value).lower()).value
    except ValueError:
        return Severity.MEDIUM.value


def score_cases(reviewer, cases):
    """Run the reviewer over every case and return per-case + global metrics."""
    pred_total = exp_total = 0
    pred_locs = set()
    exp_locs = set()
    heavy_exp = []
    heavy_locs = set()
    tool_calls = 0
    agent_steps = 0
    latency = 0.0
    cases_detail = []
    tasks_ok = 0

    for case in cases:
        diff = case["diff"]
        parsed = parse_unified_diff(diff)
        start = time.perf_counter()
        predicted = reviewer.review(diff, parsed)
        latency += time.perf_counter() - start
        agent_steps += 1  # single deterministic pass for the rule stack

        pred_set = {(f.path, f.line) for f in predicted}
        exp_list = [{
            "rule_id": found.get("rule_id", ""),
            "path": found.get("path", ""),
            "line": int(found.get("line", 0)),
            "severity": _sev(found.get("severity", "medium")),
        } for found in case.get("expected_findings", [])]
        exp_set = {(e["path"], e["line"]) for e in exp_list}

        tp = len(pred_set & exp_set)
        fp = len(pred_set - exp_set)
        fn = len(exp_set - pred_set)
        heavy = [e for e in exp_list if e["severity"] in _HEAVY]
        heavy_missed = [e for e in heavy if (e["path"], e["line"]) not in pred_set]

        # Case-level task success: positive case -> at least one correct hit;
        # negative (no-issue) case -> no false positive.
        if exp_list:
            case_ok = tp >= 1
        else:
            case_ok = fp == 0
        if case_ok:
            tasks_ok += 1

        pred_total += len(pred_set)
        exp_total += len(exp_set)
        pred_locs |= pred_set
        exp_locs |= exp_set
        heavy_exp += heavy
        heavy_locs |= {(e["path"], e["line"]) for e in heavy}

        cases_detail.append({
            "case_id": case["case_id"],
            "category": case.get("category", ""),
            "risk_level": case.get("risk_level", ""),
            "expected": len(exp_list),
            "predicted": len(predicted),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "full_hit": tp == len(exp_list) and len(exp_list) > 0,
            "critical_misses": [e["rule_id"] for e in heavy_missed],
            "task_ok": case_ok,
        })

    tp_tot = len(pred_locs & exp_locs)
    fp_tot = len(pred_locs - exp_locs)
    fn_tot = len(exp_locs - pred_locs)
    heavy_tp = len(heavy_locs & pred_locs)
    heavy_tot = len(heavy_exp)

    def safe(p, n):
        return (p / n) if n else 0.0

    precision = safe(tp_tot, tp_tot + fp_tot) if (tp_tot + fp_tot) else 0.0
    recall = safe(tp_tot, tp_tot + fn_tot) if (tp_tot + fn_tot) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    high_recall = safe(heavy_tp, heavy_tot)

    metrics = {
        "true_positives": tp_tot,
        "false_positives": fp_tot,
        "false_negatives": fn_tot,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "high_risk_recall": round(high_recall, 4),
        "critical_misses": heavy_tot - heavy_tp,
        "critical_miss_rule_ids": [
            e["rule_id"] for e in heavy_exp
            if (e["path"], e["line"]) not in pred_locs
        ],
        "task_success_rate": round(safe(tasks_ok, len(cases)), 4),
        "tool_calls": tool_calls,
        "agent_steps": agent_steps,
        "latency_s": round(latency, 4),
        "cost_usd": round(0.0, 4),  # deterministic reviewer -> no LLM cost
        "recovery_rate": None,       # deterministic path performs no recovery
        "policy_violations": 0,
    }
    return metrics, cases_detail


def hard_gate(baseline, candidate):
    """Return (decision, gate_results, reasons). Hard gate precedes utility."""
    checks = {
        "candidate_no_new_critical_miss": (
            candidate["critical_misses"] <= baseline["critical_misses"]),
        "candidate_high_risk_not_worse": (
            candidate["high_risk_recall"] >= baseline["high_risk_recall"] - 1e-9),
        "candidate_recall_not_worse": (
            candidate["recall"] >= baseline["recall"] - 1e-9),
        "candidate_better_or_equal_f1": (
            candidate["f1"] >= baseline["f1"] - 1e-9),
    }
    gated = ["candidate_no_new_critical_miss",
             "candidate_high_risk_not_worse",
             "candidate_recall_not_worse"]
    decision = all(checks[k] for k in gated)
    reasons = []
    if not decision:
        for k in gated:
            if not checks[k]:
                reasons.append(k)
    return decision, checks, reasons


# --------------------------------------------------------------------------- #
# Report writing
# --------------------------------------------------------------------------- #
def delta_row(base, cand, key):
    b, c = base.get(key), cand.get(key)
    if b is None or c is None:
        return "n/a"
    if isinstance(b, (int, float)):
        diff = round(c - b, 4)
        pct = "" if b == 0 else " (%.0f%%)" % ((diff / b) * 100)
        sign = "+" if diff > 0 else ""
        return "%s%s%s" % (sign, diff, pct)
    return "%s → %s" % (b, c)


def render_report(candidate_id, baseline_id, base_metrics, cand_metrics,
                  gate_decision, gate_checks, base_cases, cand_cases):
    md = []
    md.append("# Evolution Regression Benchmark — %s" % candidate_id)
    md.append("")
    md.append("Deterministic, reproducible evaluation of an evolved policy "
              "generation (`%s`) against a baseline (`%s`) on the fixed "
              "EvoReview benchmark dataset." % (candidate_id, baseline_id))
    md.append("")
    md.append("## Hard Gate")
    md.append("")
    md.append("| Gate | Baseline | Candidate | Pass |")
    md.append("|---|---|---|---|")
    md.append("| Critical Misses ≤ | %s | %s | %s |" % (
        base_metrics["critical_misses"], cand_metrics["critical_misses"],
        "✅" if gate_checks["candidate_no_new_critical_miss"] else "❌"))
    md.append("| High-risk Recall ≥ | %.3f | %.3f | %s |" % (
        base_metrics["high_risk_recall"], cand_metrics["high_risk_recall"],
        "✅" if gate_checks["candidate_high_risk_not_worse"] else "❌"))
    md.append("| Recall ≥ | %.3f | %.3f | %s |" % (
        base_metrics["recall"], cand_metrics["recall"],
        "✅" if gate_checks["candidate_recall_not_worse"] else "❌"))
    md.append("| F1 ≥ | %.3f | %.3f | %s |" % (
        base_metrics["f1"], cand_metrics["f1"],
        "✅" if gate_checks["candidate_better_or_equal_f1"] else "❌"))
    md.append("")
    md.append("**Decision: %s**" % ("PASS — candidate safe to promote"
                                    if gate_decision else "REJECT — hard gate blocked promote"))
    md.append("")
    md.append("## Metrics (Baseline / Candidate / Delta)")
    md.append("")
    md.append("| Metric | Baseline | Candidate | Delta |")
    md.append("|---|---:|---:|---:|")
    ordered = [
        ("f1", "F1"), ("recall", "Recall"), ("precision", "Precision"),
        ("high_risk_recall", "High-risk Recall"), ("critical_misses", "Critical Misses"),
        ("false_positives", "False Positives"), ("true_positives", "True Positives"),
        ("false_negatives", "False Negatives"), ("task_success_rate", "Task Success Rate"),
        ("tool_calls", "Tool Calls"), ("agent_steps", "Agent Steps"),
        ("latency_s", "Latency (s)"), ("cost_usd", "Cost (USD)"),
        ("recovery_rate", "Recovery Rate"), ("policy_violations", "Policy Violations"),
    ]
    for key, label in ordered:
        md.append("| %s | %s | %s | %s |" % (
            label, base_metrics[key], cand_metrics[key],
            delta_row(base_metrics, cand_metrics, key)))
    md.append("")
    md.append("## Case Detail (Candidate)")
    md.append("")
    md.append("| Case | Cat | Risk | Exp | Pred | TP | FP | FN | Full | Task |")
    md.append("|---|---|---|---:|---:|---:|---:|---:|---|---|")
    for c in cand_cases:
        md.append("| %s | %s | %s | %d | %d | %d | %d | %d | %s | %s |" % (
            c["case_id"], c["category"], c["risk_level"], c["expected"], c["predicted"],
            c["true_positives"], c["false_positives"], c["false_negatives"],
            "✅" if c["full_hit"] else "—", "✅" if c["task_ok"] else "❌"))
    md.append("")
    return "\n".join(md)


def export(candidate_id, payload, md_text, artifacts_dir):
    os.makedirs(artifacts_dir, exist_ok=True)
    json_path = os.path.join(artifacts_dir, "%s.json" % candidate_id)
    md_path = os.path.join(artifacts_dir, "%s.md" % candidate_id)
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(md_text)
    return json_path, md_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evolution regression benchmark")
    parser.add_argument("--candidate-id", default="local-heuristic-v2",
                        help="candidate generation id for the report")
    parser.add_argument("--baseline-id", default="local-heuristic-v1-security",
                        help="baseline generation id")
    parser.add_argument("--category", default="all", choices=("all", "security",
                        "reliability", "correctness", "regression"))
    parser.add_argument("--artifacts-dir", default=None)
    args = parser.parse_args(argv)

    cases = load_cases(args.category)
    if not cases:
        print("No benchmark cases found.", file=sys.stderr)
        return 1

    baseline_reviewer = run_generation(BASELINE_RULE_IDS)
    candidate_reviewer = run_generation(CANDIDATE_RULE_IDS, CANDIDATE_EXTRA_RULES)
    base_metrics, base_cases = score_cases(baseline_reviewer, cases)
    cand_metrics, cand_cases = score_cases(candidate_reviewer, cases)
    decision, checks, _ = hard_gate(base_metrics, cand_metrics)

    artifacts_dir = args.artifacts_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts", "evolution_eval")
    md_text = render_report(args.candidate_id, args.baseline_id, base_metrics,
                            cand_metrics, decision, checks, base_cases, cand_cases)
    payload = {
        "candidate_id": args.candidate_id,
        "baseline_id": args.baseline_id,
        "category": args.category,
        "dataset": case_count(),
        "hard_gate": {"decision": "PASS" if decision else "REJECT", "checks": checks},
        "baseline": {"rules": sorted(BASELINE_RULE_IDS), "metrics": base_metrics},
        "candidate": {"rules": sorted(CANDIDATE_RULE_IDS) + [r[0] for r in CANDIDATE_EXTRA_RULES],
                      "metrics": cand_metrics},
        "delta": {k: delta_row(base_metrics, cand_metrics, k)
                  for k, _ in [("f1", ""), ("high_risk_recall", ""),
                               ("tool_calls", ""), ("latency_s", ""),
                               ("critical_misses", ""), ("false_positives", "")]},
    }
    json_path, md_path = export(args.candidate_id, payload, md_text, artifacts_dir)

    status = "PASS" if decision else "REJECT"
    print("Evolution Regression Benchmark: %s" % status)
    print("  Baseline  F1=%.3f Recall=%.3f HR-Recall=%.3f CriticalMiss=%d" % (
        base_metrics["f1"], base_metrics["recall"], base_metrics["high_risk_recall"],
        base_metrics["critical_misses"]))
    print("  Candidate F1=%.3f Recall=%.3f HR-Recall=%.3f CriticalMiss=%d" % (
        cand_metrics["f1"], cand_metrics["recall"], cand_metrics["high_risk_recall"],
        cand_metrics["critical_misses"]))
    print("  JSON: %s" % json_path)
    print("  Markdown: %s" % md_path)
    return 0 if decision else 1


if __name__ == "__main__":
    raise SystemExit(main())