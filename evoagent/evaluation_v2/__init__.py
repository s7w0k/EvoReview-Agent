"""Evaluation Harness V2.

A reproducible, closed-loop evaluation layer that plugs the full Agent Harness
and the Self-Evolution protocol into the frozen 100-PR controlled benchmark
while keeping the original scorer untouched during a given comparison.

Modules
-------
- ``adapters``: four evaluated systems (Single-Agent / Legacy Multi-Agent /
  Current Full Harness / Evolved Harness) exposed through one result struct.
- ``metrics``: detection + runtime + governance metrics, reusing the V1 scorer.
- ``experiment``: end-to-end runs over a dataset producing per-case + JSON.
- ``evolution_protocol``: Validation -> Experience -> Hypothesis -> Candidate ->
  Replay -> Safety Gate -> FrozenCandidateManifest, then blind Holdout.
- ``report``: Markdown + machine-readable JSON evaluation-report generation.
"""

from .adapters import EvaluationExecutionResult  # noqa: F401
from . import report  # noqa: F401

__all__ = ["EvaluationExecutionResult", "report"]