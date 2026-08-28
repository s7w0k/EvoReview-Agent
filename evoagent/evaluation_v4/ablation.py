"""Ablation runner for the V4 variants (plan §9.6).

Each ablation flips one architectural knob ON/OFF while the rest stay at the
baseline, so value is attributed to that single component.
"""
from typing import Any, Callable, Dict, List

#: (key, name, enabled-by-default) -- the required ablations from the plan.
ABLATION_VARIANTS: Dict[str, Dict[str, Any]] = {
    "A": {"name": "Full (baseline)", "default_on": True},
    "B": {"name": "No Dynamic Planner", "components": {"planner": False}},
    "C": {"name": "No Targeted Replan", "components": {"replan": False}},
    "D": {"name": "No Critic", "components": {"critic": False}},
    "E": {"name": "No Verifier", "components": {"verifier": False}},
    "F": {"name": "No Parallel Scheduler", "components": {"scheduler": False}},
    "G": {"name": "Shallow Loops", "components": {"deep_loop": False}},
}


def build_ablation_matrix() -> List[Dict[str, Any]]:
    """Describe the 7 variants so the harness can configure each run."""
    return [
        {
            "variant": key,
            "name": spec["name"],
            "enabled": {
                comp: spec.get("components", {}).get(comp, True)
                for comp in ("planner", "replan", "critic", "verifier",
                             "scheduler", "deep_loop")
            },
        }
        for key, spec in ABLATION_VARIANTS.items()
    ]


class AblationRunner:
    """Runs a scenario runner (``fn(diff, config) -> record``) per variant."""

    def __init__(self, run_scenario: Callable):
        self.run_scenario = run_scenario

    def run(self, scenarios: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        results: Dict[str, List[Dict[str, Any]]] = {}
        for row in build_ablation_matrix():
            variant = row["variant"]
            records: List[Dict[str, Any]] = []
            for scenario in scenarios:
                config = {
                    **row["enabled"],
                    "kind": scenario.get("kind"),
                    "expected_count": scenario.get("expected_count", 0),
                    "risk": scenario.get("risk", "low"),
                    "expected_replan": scenario.get("expected_replan", False),
                    "expected_replan_target": scenario.get(
                        "expected_replan_target"),
                    "expected_agents": list(scenario.get("expected_agents", [])),
                    "allowed_agents": list(scenario.get("allowed_agents", [])),
                    "forbidden_agents": list(scenario.get("forbidden_agents", [])),
                    "required_graph_edges": list(scenario.get(
                        "required_graph_edges", [])),
                    "optional_graph_edges": list(scenario.get(
                        "optional_graph_edges", [])),
                    "category": scenario.get("category", ""),
                    "objective": scenario.get("objective", "review"),
                }
                record = self.run_scenario(scenario.get("diff", ""), config)
                record["expected_count"] = scenario.get("expected_count", 0)
                record["scenario_id"] = scenario.get("scenario_id", "")
                records.append(record)
            results[variant] = records
        return results


__all__ = ["ABLATION_VARIANTS", "AblationRunner", "build_ablation_matrix"]
