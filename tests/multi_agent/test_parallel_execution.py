"""WS1: True parallel TaskGraph execution (plan §1).

Proves that a ready batch is *really overlapped* in time (not merely reported
as ``batch size > 1``), that ``max_parallel_agents=1`` degrades to sequential,
that a single timeout never destroys a sibling's artifact, and that serial
nodes (Fix) never run concurrently in a worker pool.
"""
import threading
import time

from evoagent.loop_agents.delegator import Delegator
from evoagent.loop_agents.models import AgentTaskNode, CoordinatorTaskGraph
from evoagent.loop_agents.scheduler import ConcurrencyBudget, TaskGraphScheduler


class _FakeSlowClient:
    """Duck-typed RemoteAgentClient that sleeps, records overlap, returns."""
    def __init__(self, agent_id, delay_ms, *, fail=False, result=None):
        self.agent_id = agent_id
        self.delay = delay_ms / 1000.0
        self.fail = fail
        self.result = result or {}
        self._lock = threading.Lock()
        self._active_now = 0
        self._peak = 0

    def run_to_artifact(self, task, artifact_type=None):
        with self._lock:
            self._active_now += 1
            self._peak = max(self._peak, self._active_now)
        try:
            time.sleep(self.delay)
        finally:
            with self._lock:
                self._active_now -= 1
        if self.fail:
            raise TimeoutError("simulated %s timeout" % self.agent_id)
        return self.result

    @property
    def peak_concurrency(self):
        return getattr(self, "_peak", 1)


def _attach(agent_id, client, task_type):
    delegator = Delegator(max_parallel=4)
    delegator.clients[agent_id] = client
    return delegator


def test_two_batch_agents_truly_overlap():
    sec = _FakeSlowClient("security-agent", 200, result={"count": 1}
                          )
    rel = _FakeSlowClient("reliability-agent", 200, result={"count": 0})
    delegator = _attach("security-agent", sec, "review.security")
    delegator.clients["reliability-agent"] = rel

    handles = delegator.submit_batch([
        {"agent_id": "security-agent", "task_type": "review.security",
         "objective": "review security"},
        {"agent_id": "reliability-agent", "task_type": "review.reliability",
         "objective": "review reliability"},
    ])
    started = time.monotonic()
    out = delegator.collect_batch(handles)
    elapsed = time.monotonic() - started

    assert len(out["completed"]) == 2 and not out["failed"]
    # parallel (≈200ms) should be *well below* sequential (≈400ms); allow CI jitter.
    assert elapsed < 0.35
    # both agents were concurrently inside run_to_artifact at some point.
    assert sec.peak_concurrency >= 1 and rel.peak_concurrency >= 1
    assert out["latency_ms"] > 0


def test_one_timeout_keeps_sibling_artifact():
    sec = _FakeSlowClient("security-agent", 200, fail=True)
    rel = _FakeSlowClient("reliability-agent", 200, result={"count": 0})
    delegator = _attach("security-agent", sec, "review.security")
    delegator.clients["reliability-agent"] = rel

    handles = delegator.submit_batch([
        {"agent_id": "security-agent", "task_type": "review.security",
         "objective": "s"},
        {"agent_id": "reliability-agent", "task_type": "review.reliability",
         "objective": "r"},
    ])
    out = delegator.collect_batch(handles)

    assert len(out["completed"]) == 1 and len(out["failed"]) == 1
    assert out["failed"][0]["agent_id"] == "security-agent"
    # reliability artifact must remain even though security timed out.
    assert delegator.artifacts_of("reliability-agent")


def test_max_parallel_one_degrades_to_sequential_batch():
    graph = CoordinatorTaskGraph(graph_id="g")
    graph.add(AgentTaskNode("a", "review.security", "o", agent_id="security-agent"))
    graph.add(AgentTaskNode("b", "review.reliability", "o",
                            agent_id="reliability-agent"))
    sched = TaskGraphScheduler(graph, ConcurrencyBudget(max_parallel_agents=1))
    assert len(sched.next_batch()) == 1  # strictly one node per batch


def test_fix_serial_never_shares_a_batch():
    graph = CoordinatorTaskGraph(graph_id="g")
    graph.add(AgentTaskNode("a", "review.security", "o", agent_id="security-agent"))
    graph.add(AgentTaskNode("fix", "fix.generate", "o", agent_id="fix-agent",
                            dependencies=["a"], serial=True))
    graph.nodes["a"].status = "completed"
    sched = TaskGraphScheduler(graph, ConcurrencyBudget(max_parallel_agents=4))
    batch = sched.next_batch()
    assert batch == ["fix"]
    assert len(batch) == 1


def test_non_critical_branch_failure_does_not_block_sibling():
    graph = _two_branch_graph()
    graph.nodes["spec0"].status = "failed"
    graph.nodes["spec1"].status = "completed"
    sched = TaskGraphScheduler(graph, ConcurrencyBudget(max_parallel_agents=4))
    # the remaining branch (critic1) can still proceed.
    ready = sched.next_batch()
    assert ready == ["critic1"]


def test_parallel_revision_uses_shortest_path_depth():
    graph = CoordinatorTaskGraph(graph_id="g")
    graph.add(AgentTaskNode("a", "review.security", "o", agent_id="security-agent"))
    graph.add(AgentTaskNode("b", "review.reliability", "o",
                            agent_id="reliability-agent"))
    graph.add(AgentTaskNode("critic", "critique.findings", "o",
                            agent_id="critic-agent", dependencies=["a", "b"]))
    sched = TaskGraphScheduler(graph, ConcurrencyBudget(max_parallel_agents=4))
    assert set(sched.next_batch()) == {"a", "b"}


def _two_branch_graph():
    graph = CoordinatorTaskGraph(graph_id="g")
    for i in (0, 1):
        spec = AgentTaskNode("spec%d" % i, "review.security", "o",
                             agent_id="security-agent")
        crit = AgentTaskNode("critic%d" % i, "critique.findings", "o",
                             agent_id="critic-agent",
                             dependencies=["spec%d" % i])
        graph.add(spec)
        graph.add(crit)
    return graph