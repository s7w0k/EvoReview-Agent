"""Version-aware stale propagation for dynamic review artifacts."""
from typing import Dict, Iterable, List

from .events import ARTIFACT_SUPERSEDED, RuntimeGraphEvent
from .models import AgentTaskStatus, CoordinatorTaskGraph


DOWNSTREAM_TYPES = {"critique.findings", "verify.findings", "fix.generate"}


def invalidate_downstream(
    graph: CoordinatorTaskGraph,
    changed_artifact_ids: Iterable[str],
    artifacts: Dict[str, dict],
) -> List[RuntimeGraphEvent]:
    """Mark completed downstream artifacts/nodes superseded, never delete them."""
    changed = set(changed_artifact_ids)
    if not changed:
        return []
    events: List[RuntimeGraphEvent] = []
    changed_nodes = {
        str(a.get("producer_node") or "") for a in artifacts.values()
        if a.get("artifact_id") in changed
    }
    for artifact in artifacts.values():
        if artifact.get("status") == AgentTaskStatus.SUPERSEDED:
            continue
        if artifact.get("task_type") not in DOWNSTREAM_TYPES:
            continue
        inputs = set(artifact.get("input_artifact_ids") or [])
        producer = graph.nodes.get(str(artifact.get("producer_node") or ""))
        depends_on_changed = bool(inputs & changed)
        if producer is not None:
            depends_on_changed = depends_on_changed or bool(
                set(producer.dependencies) & changed_nodes)
        if not depends_on_changed:
            continue
        artifact["status"] = AgentTaskStatus.SUPERSEDED
        if producer is not None and producer.status == AgentTaskStatus.COMPLETED:
            producer.status = AgentTaskStatus.SUPERSEDED
        events.append(RuntimeGraphEvent(
            ARTIFACT_SUPERSEDED,
            node_id=producer.node_id if producer else "",
            artifact_id=str(artifact.get("artifact_id") or ""),
            detail={"changed_artifact_ids": sorted(changed)},
        ))
    return events


__all__ = ["invalidate_downstream", "DOWNSTREAM_TYPES"]
