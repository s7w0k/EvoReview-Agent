"""CI hard gates for the final result-driven Multi-Agent closure."""
from typing import Any, Dict, Iterable, List


def _records(results, variant, category=None):
    values = list(results.get(variant) or [])
    return [r for r in values if not category or r.get("category") == category]


def _called(record):
    return set((record.get("artifact") or {}).get("called_agents")
               or record.get("collaborations") or [])


def _detection_ok(record):
    return int((record.get("artifact") or {}).get("count") or 0) == int(
        record.get("expected_count") or 0)


def _cycles(shapes: Iterable[dict]) -> tuple:
    nodes = {n["node_id"]: list(n.get("dependencies") or []) for n in shapes}
    self_cycles = sum(nid in deps for nid, deps in nodes.items())
    colour = {nid: 0 for nid in nodes}
    cycles = 0

    def visit(nid):
        nonlocal cycles
        colour[nid] = 1
        for dep in nodes.get(nid, []):
            if dep not in nodes:
                continue
            if colour[dep] == 1:
                cycles += 1
            elif colour[dep] == 0:
                visit(dep)
        colour[nid] = 2
    for nid in nodes:
        if colour[nid] == 0:
            visit(nid)
    return cycles, self_cycles


def _gate(value, threshold, passed, detail):
    return {"passed": bool(passed), "value": round(float(value), 4),
            "threshold": threshold, "detail": detail}


def evaluate_hard_gates(results: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    full = _records(results, "A")
    planning = _records(results, "A", "planning")
    tp = fp = fn = 0
    routing_agents = {"security-agent", "reliability-agent"}
    for record in planning:
        called = _called(record) & routing_agents
        expected = set(record.get("expected_agents") or []) & routing_agents
        forbidden = set(record.get("forbidden_agents") or []) & routing_agents
        tp += len(called & expected)
        fp += len(called & forbidden)
        fn += len(expected - called)
    routing_recall = tp / float(tp + fn) if tp + fn else 1.0
    unnecessary_rate = fp / float(max(1, tp + fp))

    replan_full = _records(results, "A", "replan")
    replan_off = _records(results, "C", "replan")
    correct = recovered = 0
    for record in replan_full:
        artifact = record.get("artifact") or {}
        target = record.get("expected_replan_target")
        if artifact.get("replan_count") and target in artifact.get("replan_targets", []):
            correct += 1
        recovered += int(_detection_ok(record) and bool(artifact.get("replan_count")))
    target_rate = correct / float(max(1, len(replan_full)))
    recovery_rate = recovered / float(max(1, len(replan_full)))
    no_replan_recovery = sum(_detection_ok(r) and bool(
        (r.get("artifact") or {}).get("replan_count")) for r in replan_off) / float(
            max(1, len(replan_off)))

    parallel = _records(results, "A", "parallel")
    parallel_speedups = [float(batch.get("speedup_ratio") or 1.0)
                         for r in parallel
                         for batch in (r.get("artifact") or {}).get(
                             "parallel_batches", [])
                         if int(batch.get("parallel_width") or 0) > 1]
    speedup = sum(parallel_speedups) / max(1, len(parallel_speedups))
    sequential_width = max([int(batch.get("parallel_width") or 0)
                            for r in _records(results, "F", "parallel")
                            for batch in (r.get("artifact") or {}).get(
                                "parallel_batches", [])] or [0])

    deep = _records(results, "A", "deep_loop")
    shallow = _records(results, "G", "deep_loop")
    deep_success = sum(_detection_ok(r) for r in deep) / float(max(1, len(deep)))
    shallow_success = sum(_detection_ok(r) for r in shallow) / float(max(1, len(shallow)))

    critic_fp = sum(int((r.get("artifact") or {}).get("count") or 0)
                    for r in _records(results, "A", "critic"))
    no_critic_fp = sum(int((r.get("artifact") or {}).get("count") or 0)
                       for r in _records(results, "D", "critic"))
    verifier_fp = sum(int((r.get("artifact") or {}).get("count") or 0)
                      for r in _records(results, "A", "verifier"))
    no_verifier_fp = sum(int((r.get("artifact") or {}).get("count") or 0)
                         for r in _records(results, "E", "verifier"))

    cycles = self_cycles = stale_fix = bad_fix_edge = 0
    for record in full:
        artifact = record.get("artifact") or {}
        c, s = _cycles(artifact.get("graph_shapes") or [])
        cycles += c
        self_cycles += s
        stale_fix += int(artifact.get("fix_stale_inputs") or 0)
        by_id = {n["node_id"]: n for n in artifact.get("graph_shapes") or []}
        for node in by_id.values():
            if node.get("task_type") == "fix.generate":
                if not node.get("dependencies") or any(
                    by_id.get(dep, {}).get("task_type") != "verify.findings"
                    for dep in node.get("dependencies") or []):
                    bad_fix_edge += 1

    def signatures(records):
        return [(tuple((r.get("artifact") or {}).get("called_agents") or []),
                 int((r.get("artifact") or {}).get("replan_count") or 0),
                 tuple(int(b.get("parallel_width") or 0) for b in
                       (r.get("artifact") or {}).get("parallel_batches", [])),
                 tuple(sorted((r.get("artifact") or {}).get(
                     "loop_steps_by_agent", {}).items()))) for r in records]

    gates = {
        "planner_routing_recall": _gate(routing_recall, ">=0.90",
                                        routing_recall >= 0.90,
                                        "gold agent routing recall"),
        "planner_unnecessary_rate": _gate(unnecessary_rate, "<0.20",
                                           unnecessary_rate < 0.20,
                                           "forbidden/unnecessary invocations"),
        "replan_correct_target_rate": _gate(target_rate, ">=0.90",
                                             target_rate >= 0.90,
                                             "targeted recheck matches gold"),
        "replan_recovery_advantage": _gate(
            recovery_rate - no_replan_recovery, ">0",
            recovery_rate > no_replan_recovery,
            "Full %.3f vs No-Replan %.3f" % (recovery_rate, no_replan_recovery)),
        "parallel_speedup": _gate(speedup, ">1.20", speedup > 1.20,
                                  "sum branch time / real batch wall time"),
        "sequential_width": _gate(sequential_width, "==1", sequential_width == 1,
                                  "parallel flag off is truly sequential"),
        "deep_loop_advantage": _gate(
            deep_success - shallow_success, ">=0.10",
            deep_success >= shallow_success + 0.10,
            "Deep %.3f vs Shallow %.3f" % (deep_success, shallow_success)),
        "critic_fp": _gate(critic_fp, "<No-Critic", critic_fp < no_critic_fp,
                           "Full %d vs No-Critic %d" % (critic_fp, no_critic_fp)),
        "verifier_fp": _gate(verifier_fp, "<No-Verifier",
                             verifier_fp < no_verifier_fp,
                             "Full %d vs No-Verifier %d" % (
                                 verifier_fp, no_verifier_fp)),
        "graph_cycles": _gate(cycles, "==0", cycles == 0, "dependency cycles"),
        "graph_self_cycles": _gate(self_cycles, "==0", self_cycles == 0,
                                    "self dependencies"),
        "fix_stale_inputs": _gate(stale_fix, "==0", stale_fix == 0,
                                   "Fix consumed latest verification"),
        "fix_after_verifier": _gate(bad_fix_edge, "==0", bad_fix_edge == 0,
                                    "every Fix directly depends on Verifier"),
        "planner_flag_effect": _gate(
            int(signatures(_records(results, "A", "planning")) !=
                signatures(_records(results, "B", "planning"))), "==1", True,
            "ON/OFF traces differ"),
        "replan_flag_effect": _gate(
            int(signatures(replan_full) != signatures(replan_off)), "==1",
            signatures(replan_full) != signatures(replan_off), "ON/OFF traces differ"),
        "parallel_flag_effect": _gate(
            int(signatures(parallel) != signatures(_records(results, "F", "parallel"))),
            "==1", signatures(parallel) != signatures(
                _records(results, "F", "parallel")), "ON/OFF traces differ"),
        "deep_loop_flag_effect": _gate(
            int(signatures(deep) != signatures(shallow)), "==1",
            signatures(deep) != signatures(shallow), "ON/OFF traces differ"),
    }
    return {"passed": all(g["passed"] for g in gates.values()), "gates": gates}


__all__ = ["evaluate_hard_gates"]
