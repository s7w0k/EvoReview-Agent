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
    DEFAULT_FULL_CORPUS_FILE,
    CATEGORY_SIZES,
    GOLD_KEYS,
    load_scenarios,
    sample_scenarios,
    build_scenario,
    build_full_corpus,
    write_default_corpus,
    write_full_corpus,
)
from .runtime_runner import (  # noqa: F401
    RuntimeScenarioRunner,
    build_runtime_runner,
    collect_real_runtime_metrics,
)

__all__ = [
    "ABLATION_VARIANTS", "AblationRunner", "build_ablation_matrix",
    "evaluate_run", "load_outcome", "build_report", "render_markdown",
    "DEFAULT_SCENARIO_FILE", "DEFAULT_FULL_CORPUS_FILE", "CATEGORY_SIZES",
    "GOLD_KEYS", "load_scenarios", "sample_scenarios", "build_scenario",
    "build_full_corpus", "write_default_corpus", "write_full_corpus",
    "RuntimeScenarioRunner", "build_runtime_runner", "collect_real_runtime_metrics",
]