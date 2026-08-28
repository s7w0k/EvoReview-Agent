"""Command line entry for Evaluation V4 (plan §9.7).

Usage:
    python -m evoagent.evaluation_v4 --corpus multi_agent_scenarios.jsonl \
        --scenarios 40 --out report.md
"""
import argparse
import sys
from typing import Any, Dict

from .ablation import AblationRunner
from .report import build_report, render_markdown
from .scenarios import load_scenarios, sample_scenarios, write_default_corpus


def _synthetic_runner(diff: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic placeholder runner so the CLI works without live agents."""
    kind = config.get("kind", "clean")
    planner = config.get("planner", True)
    replan = config.get("replan", True)
    expected = 2 if kind == "both" else (1 if "only" in kind or kind in (
        "verifier-conflict", "fix-success", "fix-failure") else 0)
    rationale = ["AUTH_CHANGE", "HIGH_RISK"] if planner and expected else (
        ["CLEAN_BASELINE"] if not expected else ["HIGH_RISK"])
    return {
        "artifact": {
            "count": expected,
            "rationale_codes": rationale,
            "graph_revision": 2 if replan else 1,
            "replan_count": 1 if replan and expected else 0,
            "steps": len(rationale) + 1,
            "delegated_tasks": expected + (2 if expected else 1),
        },
        "tool_calls": expected * 2,
        "a2a_calls": expected + 1,
        "collaborations": ["critic", "verifier"] if expected else [],
        "loop_sizes": [2, 2, 3] if expected else [1],
    }


def run() -> int:
    parser = argparse.ArgumentParser(prog="eval_v4")
    parser.add_argument("--corpus", default="multi_agent_scenarios.jsonl")
    parser.add_argument("--scenarios", type=int, default=8)
    parser.add_argument("--out", default="evaluation_v4_report.md")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    write_default_corpus(args.corpus)
    scenarios = sample_scenarios(load_scenarios(args.corpus), args.scenarios,
                                 seed=args.seed)
    results = AblationRunner(_synthetic_runner).run(scenarios)
    report = build_report(results)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    print("wrote %s (%d scenarios x %d variants)" % (
        args.out, len(scenarios), len(report["order"])))
    for row in report["variants"]:
        print("  %-24s overall=%.4f" % (row["name"], row["metrics"]["overall"]))
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()