"""Evaluation V4 -- real-runtime scenario runner (plan §4.2).

Unlike the synthetic placeholder, :class:`RuntimeScenarioRunner` drives the true
stack end-to-end:

    SixAgentReviewer -> CoordinatorAgent -> AgentLoop -> A2A -> real artifacts

and reports metrics derived from what actually ran (plan §4.1/§4.2).  The
ablation ``config`` (from :data:`evoagent.evaluation_v4.ablation`) is projected
onto :class:`MultiAgentFeatureFlags` so every variant genuinely changes the
runtime behaviour (plan §4.4), then the reviewer runs the real coordinator loop.

Phase 12: :func:`attribute_runtime` derives evolution-attribution codes
(:data:`evoagent.loop_agents.attribution.FAILURE_ATTRIBUTION`) from the
gold-vs-actual gap on the *real* record, so a degraded run can be traced to the
component responsible (plan §12).

The public entrypoint is :func:`build_runtime_runner`, a ``(diff, config) -> record``
callable accepted by :class:`AblationRunner`.
"""
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..loop_agents.attribution import FAILURE_ATTRIBUTION
from ..loop_agents.coordinator import CoordinatorAgent
from ..loop_agents.feature_flags import MultiAgentFeatureFlags
from ..loop_agents.reviewer import SixAgentReviewer


def collect_real_runtime_metrics(
    reviewer: SixAgentReviewer, diff: str, objective: str = "review",
) -> Dict[str, Any]:
    """Run one review against the real stack and derive an outcome record.

    Mirrors :meth:`SixAgentReviewer.review` but keeps the whole coordinator
    outcome (artifact + observations) so the metrics reflect real execution.
    """
    delegator = (reviewer._http_delegator if reviewer.mode == "http"
                 else reviewer._inprocess_delegator)(diff)
    coordinator = CoordinatorAgent(delegator, **reviewer.coordinator_kwargs)
    task: Dict[str, Any] = {
        "task_id": "%032x" % int(time.time() * 1000000),
        "task_type": "review.coordinate",
        "objective": objective,
        "input": {"diff": diff, "objective": objective},
    }
    outcome = coordinator.run(task)
    artifact = dict(outcome.get("artifact") or {})
    observations = outcome.get("observations") or []
    tool_calls = sum(1 for o in observations
                     if isinstance(o, dict) and o.get("tool"))
    a2a_calls = artifact.get("delegated_tasks") or 0
    return {
        "artifact": {
            "count": artifact.get("count", 0),
            "accepted_count": artifact.get("count", 0),
            "rejected_count": artifact.get("rejected_count", 0),
            "rationale_codes": artifact.get("rationale_codes", []),
            "graph_revision": artifact.get("graph_revision", 1),
            "replan_count": artifact.get("replan_count", 0),
            "steps": artifact.get("steps", len(observations)),
            "delegated_tasks": a2a_calls,
            "architecture": artifact.get("architecture", "six-agent"),
        },
        "tool_calls": tool_calls,
        "a2a_calls": a2a_calls,
        "collaborations": list(artifact.get("collaborations", [])),
        "loop_sizes": [len(observations)] if observations else [1],
        "graph_mutations": list(artifact.get("graph_mutations", [])),
        "expected_count": 0,
        "diff_len": len(diff or ""),
        "ran_real_runtime": True,
        "synthetic": False,
        "stop_reason": outcome.get("stop_reason", "final"),
    }


def attribute_runtime(record: Dict[str, Any]) -> List[str]:
    """Derive evolution-attribution codes from the gold-vs-actual gap (plan §12).

    Reads the *real* outcome and the scenario's gold, then maps the mismatch to
    the specific failing component.  Only stable codes from
    :data:`~evoagent.loop_agents.attribution.FAILURE_ATTRIBUTION` are emitted.
    """
    codes: List[str] = []
    artifact = record.get("artifact") or {}
    expected = int(record.get("expected_count") or 0)
    accepted = int(artifact.get("count") or 0)
    replan_count = int(artifact.get("replan_count") or record.get("replan_count") or 0)
    expected_replan = bool(record.get("expected_replan"))
    expected_target = record.get("expected_replan_target")

    # Detection gap on a diff that genuinely has findings -> specialist loop
    # or the critic/verifier dropped a real finding.
    if expected > 0 and accepted == 0:
        if replan_count == 0:
            codes.append("SPECIALIST_LOOP_TOO_SHALLOW")
        else:
            codes.append("CRITIC_MISS")
    elif expected > 0 and accepted < expected:
        codes.append("VERIFIER_MISS")

    # Replan was required by the gold but never produced, or did not recover.
    if expected_replan and replan_count == 0:
        codes.append("REPLAN_INSUFFICIENT")
    elif expected_replan and expected > 0 and accepted == 0:
        codes.append("REPLAN_INSUFFICIENT")

    # Replan target diverged from the gold target.
    if expected_replan and expected_target and replan_count > 0:
        actual_targets = (record.get("collaborations") or [])
        if expected_target not in actual_targets:
            codes.append("REPLAN_TARGET_ERROR")

    # False positive on a clean diff -> wasteful routing / unverified accept.
    if expected == 0 and accepted > 0:
        codes.append("PLANNER_OVER_ROUTING")

    # Only report codes we can explain.
    return [c for c in codes if c in FAILURE_ATTRIBUTION]


@dataclass
class RuntimeScenarioRunner:
    """Runs a scenario through the *real* six-agent runtime.

    ``config`` is the enabled-boolean dict the ablation runner feeds us
    (keys: planner/replan/critic/verifier/scheduler/deep_loop).  We map it onto
    :class:`MultiAgentFeatureFlags` and hand it to the reviewer.
    """

    architecture: str = "six-agent-v2"
    coordinator_kwargs: Optional[Dict[str, Any]] = None

    def _flags(self, config: Dict[str, Any]) -> MultiAgentFeatureFlags:
        components = {
            "planner": config.get("planner", True),
            "targeted_replan": config.get("replan", True),
            "critic": config.get("critic", True),
            "verifier": config.get("verifier", True),
            "parallel_scheduler": config.get("scheduler", True),
            "deep_loop": config.get("deep_loop", True),
        }
        return MultiAgentFeatureFlags(**components)

    def _reviewer(self, config: Dict[str, Any]) -> SixAgentReviewer:
        merged_kwargs = dict(self.coordinator_kwargs or {})
        return SixAgentReviewer(
            "inprocess", coordinator_kwargs=merged_kwargs,
            architecture=self.architecture,
            feature_flags=self._flags(config),
        )

    def run(self, scenario: Dict[str, Any],
            config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        config = config or {}
        reviewer = self._reviewer(config)
        record = collect_real_runtime_metrics(
            reviewer, scenario.get("diff", ""),
            objective=scenario.get("objective", "review"))
        record["expected_count"] = scenario.get("expected_count", 0)
        record["scenario_id"] = scenario.get("scenario_id", "")
        record["risk"] = scenario.get("risk", "low")
        # Phase 12: attach the scenario gold and the derived attribution codes.
        record["expected_replan"] = scenario.get("expected_replan", False)
        record["expected_replan_target"] = scenario.get("expected_replan_target")
        record["expected_agents"] = list(scenario.get("expected_agents", []))
        record["attribution"] = attribute_runtime(record)
        return record


def build_runtime_runner() -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    """Return a ``(diff, config) -> record`` callable for :class:`AblationRunner`."""
    runner = RuntimeScenarioRunner()

    def run_scenario(diff: str, config: Dict[str, Any]) -> Dict[str, Any]:
        scenario = {
            "diff": diff,
            "objective": "review",
            "expected_count": config.get("expected_count", 0),
            "risk": config.get("risk", "low"),
            "expected_replan": config.get("expected_replan", False),
            "expected_replan_target": config.get("expected_replan_target"),
            "expected_agents": list(config.get("expected_agents", [])),
        }
        record = runner.run(scenario, config)
        record["expected_count"] = config.get("expected_count", 0)
        return record

    return run_scenario


__all__ = [
    "RuntimeScenarioRunner", "build_runtime_runner", "collect_real_runtime_metrics",
    "attribute_runtime",
]