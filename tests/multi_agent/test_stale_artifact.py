from evoagent.loop_agents.invalidation import invalidate_downstream
from evoagent.loop_agents.models import (
    AgentTaskNode, AgentTaskStatus, CoordinatorTaskGraph,
)


def test_old_verification_is_superseded_and_fix_cannot_consume_it():
    graph = CoordinatorTaskGraph("g")
    graph.add(AgentTaskNode("security-v1", "review.security", "s",
                            status=AgentTaskStatus.COMPLETED,
                            artifact_ids=["finding-v1"]))
    graph.add(AgentTaskNode("verifier-v1", "verify.findings", "v",
                            dependencies=["security-v1"],
                            status=AgentTaskStatus.COMPLETED,
                            artifact_ids=["verification-v1"]))
    graph.add(AgentTaskNode("fix-v1", "fix.generate", "f",
                            dependencies=["verifier-v1"],
                            status=AgentTaskStatus.COMPLETED,
                            artifact_ids=["fix-v1-artifact"]))
    artifacts = {
        "finding-v1": {"artifact_id": "finding-v1", "producer_node": "security-v1",
                       "task_type": "review.security", "status": "completed"},
        "verification-v1": {
            "artifact_id": "verification-v1", "producer_node": "verifier-v1",
            "task_type": "verify.findings", "status": "completed",
            "input_artifact_ids": ["finding-v1"]},
        "fix-v1-artifact": {
            "artifact_id": "fix-v1-artifact", "producer_node": "fix-v1",
            "task_type": "fix.generate", "status": "completed",
            "input_artifact_ids": ["verification-v1"]},
    }
    events = invalidate_downstream(graph, ["finding-v1"], artifacts)
    assert events
    assert artifacts["verification-v1"]["status"] == "superseded"
    assert graph.nodes["verifier-v1"].status == "superseded"
    # Propagate once more from stale verification to its Fix consumer.
    invalidate_downstream(graph, ["verification-v1"], artifacts)
    assert artifacts["fix-v1-artifact"]["status"] == "superseded"
    assert graph.nodes["fix-v1"].status == "superseded"
