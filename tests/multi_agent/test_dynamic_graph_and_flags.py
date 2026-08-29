from evoagent.evaluation_v4.gates import evaluate_hard_gates
from evoagent.evaluation_v4.runtime_runner import RuntimeScenarioRunner
from evoagent.evaluation_v4.scenarios import build_full_corpus


def _one(category):
    return next(s for s in build_full_corpus() if s["category"] == category)


def _types(record):
    return [n["task_type"] for n in record["artifact"]["graph_shapes"]]


def test_runtime_produces_distinct_dynamic_graph_shapes():
    runner = RuntimeScenarioRunner()
    clean = runner.run(next(s for s in build_full_corpus()
                            if s["kind"] == "clean"), {})
    security = runner.run(_one("planning"), {"critic": False})
    parallel = runner.run(_one("parallel"), {})
    replan = runner.run(_one("replan"), {})
    fix = runner.run(_one("fix"), {"kind": "fix-success"})

    assert _types(clean) == ["review.reliability"]
    # Plan §Phase 2: a high-risk security diff is dual-routed, so the security
    # specialist AND the reliability specialist both get a chance to run.
    assert _types(security) == ["review.security", "review.reliability",
                                "verify.findings"]
    assert {"review.security", "review.reliability", "critique.findings",
            "verify.findings"} <= set(_types(parallel))
    assert any("recheck" in n["node_id"]
               for n in replan["artifact"]["graph_shapes"])
    assert _types(fix)[-1] == "fix.generate"


def test_every_feature_flag_changes_the_real_trace():
    runner = RuntimeScenarioRunner()
    cases = {
        "planner": (_one("planning"), {"planner": False}),
        "replan": (_one("replan"), {"replan": False}),
        "critic": (_one("critic"), {"critic": False}),
        "verifier": (_one("verifier"), {"verifier": False}),
        "scheduler": (_one("parallel"), {"scheduler": False}),
        "deep_loop": (_one("deep_loop"), {"deep_loop": False}),
    }
    for name, (scenario, off) in cases.items():
        full = runner.run(scenario, {})["artifact"]
        disabled = runner.run(scenario, off)["artifact"]
        signature = lambda a: (
            a["called_agents"], a["replan_count"], a["parallel_batches"],
            a["loop_steps_by_agent"], a["count"])
        assert signature(full) != signature(disabled), name
