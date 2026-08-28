"""Evaluation V4 scenario corpus + sampling (plan §9.4).

Scenarios are stored as JSON-lines with a ``diff`` input and the expected
outcome fields (:meth:`evoagent.evaluation_v4.metrics.load_outcome` consumes
the per-run ``record``; the scenario supplies expected values).
"""
import json
import os
import random
from typing import Any, Dict, List, Optional

DEFAULT_SCENARIO_FILE = "multi_agent_scenarios.jsonl"

#: The 8 behavioural fixtures named in the plan to guarantee coverage.
FIXTURE_KINDS = (
    "security-only", "reliability-only", "both", "clean", "critic-evidence-gap",
    "verifier-conflict", "fix-success", "fix-failure",
)


def _diff_for(kind: str) -> str:
    """Deterministic representative diffs per fixture kind."""
    if kind == "security-only":
        return ("--- a/login.py\n+++ b/login.py\n@@ -1,4 +1,6 @@\n"
                "def login(user, pw):\n+    import sqlite3\n"
                "    db = sqlite3.connect('u.db')\n"
                "-    return pw\n+    return db.execute('SELECT * FROM u WHERE pw='+pw)\n")
    if kind == "reliability-only":
        return ("--- a/job.py\n+++ b/job.py\n@@ -1,4 +1,6 @@\n"
                "def run():\n+    try:\n"
                "        data = fetch()\n+    except Exception:\n"
                "+        pass\n    return data\n")
    if kind == "both":
        return ("--- a/api.py\n+++ b/api.py\n@@ -1,5 +1,8 @@\n"
                "def handle(req):\n+    import sqlite3\n"
                "    db = sqlite3.connect('a.db')\n"
                "-    return req\n+    try:\n+        return db.execute('WHERE x='+req)\n"
                "+    except Exception:\n+        return None\n")
    if kind == "clean":
        return ("--- a/lib.py\n+++ b/lib.py\n@@ -1,3 +1,4 @@\n"
                "def add(a, b):\n+    """  # docstring stub
                "    return a + b\n")
    if kind == "critic-evidence-gap":
        return ("--- a/auth.py\n+++ b/auth.py\n@@ -1,3 +1,4 @@\n"
                "def check(tok):\n+    return tok == 'stored'\n")
    if kind == "verifier-conflict":
        return ("--- a/db.py\n+++ b/db.py\n@@ -1,3 +1,5 @@\n"
                "def load(q):\n+    import sqlite3\n"
                "    c = sqlite3.connect('d.db')\n+    return c.execute(q)\n")
    if kind == "fix-success":
        return ("--- a/enc.py\n+++ b/enc.py\n@@ -1,3 +1,4 @@\n"
                "def enc(v):\n+    import base64\n"
                "    return base64.b64encode(v)\n")
    # fix-failure (default)
    return ("--- a/net.py\n+++ b/net.py\n@@ -1,3 +1,5 @@\n"
            "def send(x):\n+    try:\n+        sock.send(x)\n+    except:\\n        pass\n")


def build_scenario(kind: str, index: int = 0) -> Dict[str, Any]:
    """Build one scenario dict for a fixture kind (deterministic)."""
    risk = "high" if kind in ("security-only", "both", "verifier-conflict") else (
        "medium" if kind in ("reliability-only", "critic-evidence-gap",
                             "fix-success", "fix-failure") else "low")
    expected = 2 if kind == "both" else (1 if "only" in kind or kind in (
        "verifier-conflict", "fix-success", "fix-failure") else 0)
    return {
        "scenario_id": "%s-%d" % (kind, index),
        "kind": kind,
        "objective": "review scenario %s" % kind,
        "diff": _diff_for(kind),
        "risk": risk,
        "expected_count": expected,
    }


def load_scenarios(path: str, *, kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Load scenarios from a JSON-lines file (or generate the defaults)."""
    if not os.path.exists(path):
        return [build_scenario(k) for k in
                (kinds or FIXTURE_KINDS) if k in FIXTURE_KINDS]
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if not kinds or item.get("kind") in kinds:
                out.append(item)
    return out or [build_scenario(k) for k in (kinds or FIXTURE_KINDS)]


def sample_scenarios(scenarios: List[Dict[str, Any]], n: int,
                     seed: int = 0) -> List[Dict[str, Any]]:
    """Draw a seeded, reproducible sample of ``n`` scenarios."""
    if n <= 0 or n >= len(scenarios):
        return list(scenarios)
    rng = random.Random(seed)
    return rng.sample(scenarios, n)


def write_default_corpus(path: str) -> int:
    """Persist the default 8-fixture corpus (one scenario per kind)."""
    items = [build_scenario(k) for k in FIXTURE_KINDS]
    with open(path, "w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(items)


__all__ = [
    "DEFAULT_SCENARIO_FILE", "FIXTURE_KINDS", "build_scenario", "load_scenarios",
    "sample_scenarios", "write_default_corpus",
]