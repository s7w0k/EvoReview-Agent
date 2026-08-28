"""Unified runtime feature-flags for the multi-agent loop (plan §4.3, §4.4).

Ablation (and CI hard-gates) pass one explicit :class:`MultiAgentFeatureFlags`
into the runtime instead of scattering env vars.  Each flag is guaranteed to
change *real behavior* downstream (plan §4.4):

* ``planner``            -- True: SemanticPlanner; False: FallbackPlanner.
* ``targeted_replan``    -- True: targeted graph mutation on evidence gaps;
                            False: no replan node is ever inserted.
* ``critic``             -- True: critic stage participates; False: no critic node.
* ``verifier``           -- True: verifier stage participates; False: no verifier node.
* ``parallel_scheduler`` -- True: batch delegation up to the concurrency budget;
                            False: ``max_parallel_agents = 1`` (serial).
* ``deep_loop``          -- True: observation-driven deep loops; False: shallow
                            single-gate stepper for specialist agents.
"""
from dataclasses import asdict, dataclass, field
from typing import Dict


@dataclass
class MultiAgentFeatureFlags:
    planner: bool = True
    targeted_replan: bool = True
    critic: bool = True
    verifier: bool = True
    parallel_scheduler: bool = True
    deep_loop: bool = True

    @property
    def effective_max_parallel(self) -> int:
        """The scheduler budget honours ``parallel_scheduler`` (plan §4.4)."""
        return 3 if self.parallel_scheduler else 1

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    def clone(self, **overrides: bool) -> "MultiAgentFeatureFlags":
        values = self.to_dict()
        values.update(overrides)
        return MultiAgentFeatureFlags(**values)


#: Convenience parse helper for the ablation runner.
def flags_from_dict(data: Dict[str, object]) -> MultiAgentFeatureFlags:
    return MultiAgentFeatureFlags(**{
        key: bool(data[key]) for key in
        ("planner", "targeted_replan", "critic", "verifier",
         "parallel_scheduler", "deep_loop") if key in data
    })


_ABLATION_VARIANTS = {
    "Full": {},
    "NoPlanner": {"planner": False},
    "NoReplan": {"targeted_replan": False},
    "NoCritic": {"critic": False},
    "NoVerifier": {"verifier": False},
    "Sequential": {"parallel_scheduler": False},
    "Shallow": {"deep_loop": False},
}


def ablation_variant(name: str) -> MultiAgentFeatureFlags:
    """Return the flag set for a named ablation variant (plan §5 / Phase 10)."""
    overrides = _ABLATION_VARIANTS.get(name, {})
    return MultiAgentFeatureFlags(**overrides)


__all__ = [
    "MultiAgentFeatureFlags", "flags_from_dict", "ablation_variant",
    "_ABLATION_VARIANTS",
]