"""Evidence-driven governance for self-evolution.

Every evolution change must carry a traceable lineage, an attribution report, a
fail-safe against slice-level regressions, and be constrained by an evolution
budget so candidates cannot explode.  These three concerns live here.
"""
from .attribution import (
    AttributionReport,
    AttributionReportBuilder,
    EvidenceCounts,
    ProductionOutcome,
    ReplayOutcome,
    render_attribution,
)
from .budget import (
    BudgetDecision,
    BudgetDenied,
    EvolutionBudget,
    EvolutionBudgetGuard,
)
from .lineage import (
    EvolutionLineage,
    LineageNode,
    LineageStage,
    LineageTracker,
)
from .regression import (
    REGRESSION_DIMENSIONS,
    RegressionAttribution,
    RegressionLocator,
    RegressionSegment,
)

__all__ = [
    "AttributionReport",
    "AttributionReportBuilder",
    "BudgetDecision",
    "BudgetDenied",
    "EvidenceCounts",
    "EvolutionBudget",
    "EvolutionBudgetGuard",
    "EvolutionLineage",
    "LineageNode",
    "LineageStage",
    "LineageTracker",
    "ProductionOutcome",
    "REGRESSION_DIMENSIONS",
    "RegressionAttribution",
    "RegressionLocator",
    "RegressionSegment",
    "ReplayOutcome",
    "render_attribution",
]