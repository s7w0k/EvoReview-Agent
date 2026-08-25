"""Fixed code-review benchmark dataset loader (hardening plan Phase 8).

Cases are stored as JSONL under ``benchmarks/<category>/cases.jsonl``.  Each
record is a code-review case with a concrete unified diff and the expected
findings used to score the harness.  Includes explicitly no-issue (negative)
cases so false positives can be measured.

Use ``load_cases()`` to get every case or filter by category / risk level.
"""
import json
import os

_CATEGORIES = ("security", "reliability", "correctness", "regression")


def _benchmark_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def load_cases(category: str = "all") -> list:
    """Return benchmark cases, optionally filtered to one category."""
    chosen = _CATEGORIES if category == "all" else (category,)
    cases = []
    for cat in chosen:
        path = os.path.join(_benchmark_root(), cat, "cases.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                case = json.loads(line)
                case["category"] = cat
                cases.append(case)
    return cases


def by_risk_level(risk_level: str = "all") -> list:
    return [c for c in load_cases() if risk_level == "all" or c.get("risk_level") == risk_level]


def case_count() -> dict:
    counts = {}
    for cat in _CATEGORIES:
        counts[cat] = len(load_cases(cat))
    counts["total"] = sum(counts.values())
    return counts


__all__ = ["load_cases", "by_risk_level", "case_count", "_CATEGORIES"]