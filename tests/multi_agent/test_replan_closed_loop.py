"""Final E2E proof for pre-verifier targeted replan and graph mutation."""
from evoagent.evaluation_v4.runtime_runner import RuntimeScenarioRunner
from evoagent.evaluation_v4.scenarios import build_full_corpus


def _scenario(category):
    return next(s for s in build_full_corpus() if s["category"] == category)


def test_replan_closed_loop_consumes_latest_finding_version():
    record = RuntimeScenarioRunner().run(_scenario("replan"), {})
    artifact = record["artifact"]
    shapes = artifact["graph_shapes"]
    runtime_artifacts = artifact["runtime_artifacts"]

    assert artifact["replan_count"] == 1
    assert artifact["graph_revision"] >= 2
    security_runs = [a for a in runtime_artifacts
                     if a["task_type"] == "review.security"]
    assert len(security_runs) == 2
    assert max(artifact["finding_versions"].values()) == 2

    verifier = next(n for n in shapes if n["task_type"] == "verify.findings")
    recheck = next(n for n in shapes if "recheck" in n["node_id"])
    assert verifier["dependencies"] == [recheck["node_id"]]
    verifier_artifact = next(a for a in runtime_artifacts
                             if a["task_type"] == "verify.findings")
    assert recheck["artifact_ids"][0] in verifier_artifact["input_artifact_ids"]
    # Replan is handled before verifier insertion, so no stale v1 verifier can
    # reach arbitration/fix; the old specialist/critic evidence is superseded.
    assert artifact["superseded_artifacts"]
    assert artifact["count"] == 1


def test_replan_flag_off_produces_no_graph_mutation_or_recheck():
    record = RuntimeScenarioRunner().run(
        _scenario("replan"), {"replan": False})
    artifact = record["artifact"]
    assert artifact["replan_count"] == 0
    assert not any("recheck" in n["node_id"] for n in artifact["graph_shapes"])
