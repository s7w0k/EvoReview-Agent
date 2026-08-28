"""Multi-Agent Value Evaluation V4 -- package (plan §9)."""
from .ablation import (  # noqa: F401
    ABLATION_VARIANTS,
    AblationRunner,
    build_ablation_matrix,
)
from .metrics import (  # noqa: F401
    evaluate_run,
    load_outcome,
)
from .report import (  # noqa: F401
    build_report,
    render_markdown,
)
from .scenarios import (  # noqa: F401
    DEFAULT_SCENARIO_FILE,
    load_scenarios,
    sample_scenarios,
)

__all__ = [
    "ABLATION_VARIANTS", "AblationRunner", "build_ablation_matrix",
    "evaluate_run", "load_outcome", "build_report", "render_markdown",
    "DEFAULT_SCENARIO_FILE", "load_scenarios", "sample_scenarios",
]