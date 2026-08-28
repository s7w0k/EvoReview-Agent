"""Command line entry for Evaluation V4 (plan §4.1/§4.2/§9.7).

The default runner is the **real runtime** (``--runner runtime``) which drives
SixAgentReviewer -> CoordinatorAgent -> AgentLoop -> A2A -> real artifacts.  The
old deterministic placeholder is only kept as a demo and requires an explicit
``--runner synthetic``.

Usage:
    python -m evoagent.evaluation_v4 --corpus evaluation_data/multi_agent_scenarios.jsonl \
        --out report.md
    python -m evoagent.evaluation_v4 --runner synthetic --scenarios 40
"""
import argparse
import json
import os
import sys
from typing import Any, Callable, Dict

from .ablation import AblationRunner
from .report import build_report, render_markdown
from .runtime_runner import build_runtime_runner
from .scenarios import (
    CATEGORY_SIZES,
    DEFAULT_FULL_CORPUS_FILE, load_scenarios, sample_scenarios,
    write_default_corpus, write_full_corpus,
)


def _synthetic_runner(diff: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic demo placeholder -- kept ONLY behind ``--runner synthetic``
    (plan §4.1 explicitly forbids it for the default evaluation)."""
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
        "synthetic": True,
    }


def _pick_runner(runner_name: str) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    if runner_name == "synthetic":
        return _synthetic_runner
    return build_runtime_runner()


def run() -> int:
    parser = argparse.ArgumentParser(prog="eval_v4")
    parser.add_argument("--corpus", default=DEFAULT_FULL_CORPUS_FILE)
    parser.add_argument("--scenarios", type=int, default=None)
    parser.add_argument("--out", default="output/evaluation_v4/evaluation_v4_real.md")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--enforce-gates", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--runner", default="runtime",
                        choices=("runtime", "synthetic"),
                        help="default=runtime (real stack); synthetic is demo only")
    args = parser.parse_args()

    # Phase 9: the default corpus is the persisted 60-case set.  We never
    # clobber an existing corpus -- only seed it (or the 8-fixture default)
    # when the file is absent, so a hand-curated corpus is preserved across runs.
    if not os.path.exists(args.corpus):
        if args.corpus == DEFAULT_FULL_CORPUS_FILE:
            write_full_corpus(args.corpus)
        else:
            write_default_corpus(args.corpus)
    scenarios = load_scenarios(args.corpus)
    if (args.corpus == DEFAULT_FULL_CORPUS_FILE
            and len(scenarios) != sum(CATEGORY_SIZES.values())):
        write_full_corpus(args.corpus)
        scenarios = load_scenarios(args.corpus)
    if args.scenarios is not None and args.scenarios < len(scenarios):
        scenarios = sample_scenarios(scenarios, args.scenarios, seed=args.seed)
    run_scenario = _pick_runner(args.runner)
    results = AblationRunner(run_scenario).run(scenarios)
    report = build_report(results)
    out_parent = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    json_out = args.json_out or os.path.splitext(args.out)[0] + ".json"
    os.makedirs(os.path.dirname(os.path.abspath(json_out)), exist_ok=True)
    with open(json_out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("wrote %s (%d scenarios x %d variants, runner=%s)" % (
        args.out, len(scenarios), len(report["order"]), args.runner))
    for row in report["variants"]:
        print("  %-24s overall=%.4f" % (row["name"], row["metrics"]["overall"]))
    print("  hard-gates=%s -> %s" % (
        "PASS" if report["hard_gates"]["passed"] else "FAIL", json_out))
    return 1 if args.enforce_gates and not report["hard_gates"]["passed"] else 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
