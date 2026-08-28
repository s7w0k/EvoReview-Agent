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

#: The six evaluation categories and their required corpus sizes (plan §4.5).
CATEGORY_SIZES = {
    "planning": 15,
    "replan": 10,
    "collaboration": 10,
    "deep_loop": 10,
    "fix": 5,
    "failure": 10,
}

DEFAULT_FULL_CORPUS_FILE = "evaluation_data/multi_agent_scenarios.jsonl"

#: Every scenario must carry gold (plan §4.6).  These are the gold keys the
#: corpus generator always emits.
GOLD_KEYS = (
    "expected_agents", "expected_replan", "expected_replan_target",
    "expected_findings", "expected_parallel_groups",
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


# ---------------------------------------------------------------------------
# Phase 9 : the 60-case scenario corpus (plan §4.5 / §4.6)
# ---------------------------------------------------------------------------

# Realistic unified-diff bodies keyed by a short mnemonic.  Each uses a
# ``{file}`` placeholder so the corpus can mint unique per-case diffs while
# keeping the injected issue set deterministic and auditable.
_DIFF_POOL: Dict[str, str] = {
    "sql-injection": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,4 +1,8 @@\n"
        "def lookup(user, pw):\n+    import sqlite3\n"
        "    conn = sqlite3.connect('app.db')\n"
        "-    return conn.execute('SELECT * FROM accounts')\n"
        "+    cur = conn.execute('SELECT * FROM accounts WHERE name='+user)\n"
        "+    row = cur.fetchone()\n+    return row\n"),
    "broad-except": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,5 +1,9 @@\n"
        "def process():\n-    data = fetch(uri)\n-    persist(data)\n"
        "+    try:\n+        data = fetch(uri)\n+    except Exception:\n+        pass\n"
        "+    try:\n+        persist(data)\n+    except:\n+        pass\n"),
    "path-traversal": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,3 +1,6 @@\n"
        "def read(name):\n+    import os\n"
        "-    return open(name).read()\n"
        "+    p = os.path.join(BASE_DIR, name)\n+    return open(p).read()\n"),
    "race-guard": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,4 +1,7 @@\n"
        "def evict(key):\n+    if key in cache:\n+        del cache[key]\n"
        "    incr(metric)\n"
        "+    stale.append(key)\n    return"),  # unsynchronized check-then-act
    "hardcoded-secret": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,2 +1,4 @@\n"
        "+# temporary local credentials\n+SECRET = 'sk-prod-9f2c'\n"
        "def auth(): ...\n"),
    "command-injection": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,5 +1,6 @@\n"
        "def run(cmd):\n+    import subprocess\n"
        "-    subprocess.run(['ls'])\n"
        "+    subprocess.run(cmd, shell=True)\n"),
    "missing-timeout": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,4 +1,6 @@\n"
        "def call(url):\n+    import requests\n"
        "-    return requests.get(url)\n"
        "+    # no timeout -> worker can hang forever\n"
        "+    return requests.get(url)\n"),
    "unbounded-retry": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,5 +1,8 @@\n"
        "def publish(msg):\n+    while True:\n"
        "        try:\n"
        "+            send(msg)\n+            break\n"
        "+        except Exception:\n+            pass\n"
        "-            send(msg)\n-            break\n-        except Exception:\n-            time.sleep(1)\n"),
    "xss-escape": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,4 +1,5 @@\n"
        "def render(name, html):\n-    return '<div>' + html + '</div>'\n"
        "+    # direct interpolation of user html\n"
        "+    return '<div>' + html + '</div>'\n"),
    "both-inject": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,6 +1,10 @@\n"
        "def handle(q, r):\n+    import sqlite3\n"
        "    db = sqlite3.connect('x.db')\n"
        "-    return dispatch(q)\n"
        "+    rows = db.execute('WHERE id='+r)\n"
        "+    try:\n+        return rows.fetchall()\n"
        "+    except Exception:\n+        return None\n"),
    "clean-baseline": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,3 +1,4 @@\n"
        "def add(a, b):\n+    return a + b\n"),
    "auth-timing": (
        "--- a/{file}\n+++ b/{file}\n@@ -1,3 +1,5 @@\n"
        "def check(cred):\n+    import hmac, time\n"
        "+    time.sleep(len(str(cred['user'])) * 1.5)\n"
        "    ok = hmac.compare_digest(...)\n"
        "+    return ok\n"),
}


def _finding(spec: str, severity: str = "high", line: int = 0) -> Dict[str, Any]:
    """Build a gold finding descriptor."""
    return {"rule_id": spec, "severity": severity, "path": "", "line": line}


def _make_scenario(category: str, kind: str, index: int, diff_id: str, *,
                   risk: str, expected_count: int, expected_agents: List[str],
                   expected_replan: bool = False,
                   expected_replan_target: Optional[str] = None,
                   findings: Optional[List[str]] = None,
                   expected_parallel_groups: Optional[List[List[str]]] = None,
                   ) -> Dict[str, Any]:
    """Assemble one gold-bearing scenario from a pool diff."""
    fname = "%s_%s_%d.py" % (category, kind, index)
    body = _DIFF_POOL[diff_id].format(file=fname)
    return {
        "scenario_id": "%s-%03d" % (category, index),
        "category": category,
        "kind": kind,
        "objective": "%s scenario %s #%d" % (category, kind, index),
        "diff": body,
        "risk": risk,
        # the count of genuine issues to expect from a correct reviewer
        "expected_count": expected_count,
        # --- gold (plan §4.6) ---
        "expected_agents": list(expected_agents),
        "expected_replan": expected_replan,
        "expected_replan_target": expected_replan_target,
        "expected_findings": [_finding(f, severity="high" if i == 0 else "medium")
                              for i, f in enumerate(findings or [])],
        "expected_parallel_groups": [list(g) for g in (expected_parallel_groups or [])],
    }


def build_full_corpus() -> List[Dict[str, Any]]:
    """Return the 60-case categorical corpus (plan §4.5).

    Categories and sizes:
        planning 15, replan 10, collaboration 10, deep_loop 10, fix 5, failure 10.

    Every scenario carries a gold block (:data:`GOLD_KEYS`) so a harness can
    assert routing / replan / parallel behaviour against ground truth.
    """
    corpus: List[Dict[str, Any]] = []

    # ---- planning (15): routing decision per diff -------------------------
    specs = [
        ("sql-injection", "security-only", "high", 1, ["security-agent",
             "critic-agent", "verifier-agent"], []),
        ("path-traversal", "security-only", "high", 1, ["security-agent",
             "critic-agent", "verifier-agent"], []),
        ("command-injection", "security-only", "high", 1, ["security-agent",
             "critic-agent", "verifier-agent"], []),
        ("hardcoded-secret", "security-only", "high", 1, ["security-agent",
             "critic-agent", "verifier-agent"], []),
        ("xss-escape", "security-only", "high", 1, ["security-agent",
             "critic-agent", "verifier-agent"], []),
        ("auth-timing", "security-only", "high", 1, ["security-agent",
             "critic-agent", "verifier-agent"], []),
        ("broad-except", "reliability-only", "medium", 1, ["reliability-agent",
             "critic-agent", "verifier-agent"], []),
        ("race-guard", "reliability-only", "high", 1, ["reliability-agent",
             "critic-agent", "verifier-agent"], []),
        ("missing-timeout", "reliability-only", "medium", 1, [
             "reliability-agent", "critic-agent", "verifier-agent"], []),
        ("unbounded-retry", "reliability-only", "high", 1, ["reliability-agent",
             "critic-agent", "verifier-agent"], []),
        ("both-inject", "both", "high", 2, ["security-agent",
             "reliability-agent", "critic-agent", "verifier-agent"],
             ["SEC-SQLI", "REL-BROAD-EXCEPT"]),
        ("clean-baseline", "clean", "low", 0, ["reliability-agent"], []),
    ]
    for i, (diff_id, kind, risk, cnt, agents, findings) in enumerate(specs, 1):
        corpus.append(_make_scenario(
            "planning", kind, i, diff_id, risk=risk, expected_count=cnt,
            expected_agents=agents, findings=findings,
            expected_parallel_groups=[["security-agent", "reliability-agent"]]
            if kind == "both" else []))
    # remaining 3 planning cases recycle two reliability and one security body.
    for i, (diff_id, kind) in enumerate([("missing-timeout", "reliability"),
                                          ("race-guard", "reliability"),
                                          ("sql-injection", "security")], 13):
        idx = i
        _spec_id = diff_id
        _k = kind
        corpus.append(_make_scenario(
            "planning", "%s-planner" % _k, idx, _spec_id,
            risk="high" if "security" in _k else "medium",
            expected_count=1,
            expected_agents=["%s-agent" % _k, "critic-agent", "verifier-agent"],
            findings=[]))

    # ---- replan (10): evidence gap -> targeted recheck --------------------
    replan_specs = [
        ("auth-timing", "missing-security-evidence", "security-agent", "high"),
        ("sql-injection", "missing-security-evidence", "security-agent", "high"),
        ("path-traversal", "missing-security-evidence", "security-agent", "high"),
        ("xss-escape", "missing-security-evidence", "security-agent", "high"),
        ("race-guard", "missing-reliability-evidence", "reliability-agent", "high"),
        ("broad-except", "missing-reliability-evidence", "reliability-agent", "medium"),
        ("missing-timeout", "missing-reliability-evidence", "reliability-agent", "medium"),
        ("unbounded-retry", "missing-reliability-evidence", "reliability-agent", "high"),
        ("command-injection", "missing-security-evidence", "security-agent", "high"),
        ("hardcoded-secret", "missing-security-evidence", "security-agent", "high"),
    ]
    for idx, (diff_id, kind, target, risk) in enumerate(replan_specs, 1):
        corpus.append(_make_scenario(
            "replan", kind, idx, diff_id, risk=risk, expected_count=1,
            expected_agents=[target, "critic-agent", "verifier-agent"],
            expected_replan=True, expected_replan_target=target,
            findings=[]))

    # ---- collaboration (10): critic gap / verifier conflict / both ---------
    collab_specs = [
        ("sql-injection", "critic-evidence-gap", "security-agent"),
        ("path-traversal", "critic-evidence-gap", "security-agent"),
        ("race-guard", "critic-evidence-gap", "reliability-agent"),
        ("both-inject", "critic-evidence-gap", "security-agent"),
        ("command-injection", "verifier-conflict", "security-agent"),
        ("xss-escape", "verifier-conflict", "security-agent"),
        ("missing-timeout", "verifier-conflict", "reliability-agent"),
        ("auth-timing", "verifier-conflict", "security-agent"),
        ("unbounded-retry", "collaboration-both", "reliability-agent"),
        ("hardcoded-secret", "collaboration-both", "security-agent"),
    ]
    for idx, (diff_id, kind, target) in enumerate(collab_specs, 1):
        risks = {"security-agent": "high", "reliability-agent": "medium"}
        corpus.append(_make_scenario(
            "collaboration", kind, idx, diff_id, risk=risks[target],
            expected_count=1,
            expected_agents=[target, "critic-agent", "verifier-agent"],
            expected_parallel_groups=[["critic-agent", "verifier-agent"]]
            if "conflict" in kind else []))

    # ---- deep_loop (10): deepen-until-converged high risk ------------------
    deep_specs = [
        "sql-injection", "command-injection", "path-traversal", "xss-escape",
        "hardcoded-secret", "auth-timing", "both-inject", "race-guard",
        "unbounded-retry", "missing-timeout",
    ]
    for idx, diff_id in enumerate(deep_specs, 1):
        corpus.append(_make_scenario(
            "deep_loop", "deep-verification", idx, diff_id, risk="high",
            expected_count=1,
            expected_agents=["security-agent" if diff_id in (
                "sql-injection", "command-injection", "path-traversal",
                "xss-escape", "hardcoded-secret", "auth-timing", "both-inject")
                else "reliability-agent", "critic-agent", "verifier-agent"],
            expected_parallel_groups=[]))

    # ---- fix (5): verified finding -> fix-agent addresses it ---------------
    fix_specs = [
        ("sql-injection", "fix-success"),
        ("command-injection", "fix-success"),
        ("path-traversal", "fix-success"),
        ("hardcoded-secret", "fix-success"),
        ("race-guard", "fix-failure"),
    ]
    for idx, (diff_id, kind) in enumerate(fix_specs, 1):
        corpus.append(_make_scenario(
            "fix", kind, idx, diff_id, risk="high", expected_count=1,
            expected_agents=["security-agent" if diff_id in (
                "sql-injection", "command-injection", "path-traversal",
                "hardcoded-secret") else "reliability-agent",
                             "critic-agent", "verifier-agent", "fix-agent"],
            expected_parallel_groups=[]))

    # ---- failure (10): broken / undermining code paths ---------------------
    failure_specs = [
        ("broad-except", "swallowed-exception", "reliability-agent"),
        ("race-guard", "unsafe-concurrency", "reliability-agent"),
        ("missing-timeout", "hang-risk", "reliability-agent"),
        ("unbounded-retry", "unbounded-retry", "reliability-agent"),
        ("sql-injection", "sql-injection", "security-agent"),
        ("command-injection", "code-execution", "security-agent"),
        ("xss-escape", "dom-xss", "security-agent"),
        ("auth-timing", "timing-oracle", "security-agent"),
        ("both-inject", "compound-failure", "security-agent"),
        ("hardcoded-secret", "credential-leak", "security-agent"),
    ]
    for idx, (diff_id, kind, target) in enumerate(failure_specs, 1):
        risk = "high" if target == "security-agent" else "medium"
        corpus.append(_make_scenario(
            "failure", kind, idx, diff_id, risk=risk, expected_count=1,
            expected_agents=[target, "critic-agent", "verifier-agent"],
            expected_parallel_groups=[]))

    assert len(corpus) == sum(CATEGORY_SIZES.values()), \
        "corpus size mismatch: %d != %d" % (len(corpus), sum(CATEGORY_SIZES.values()))
    return corpus


def write_full_corpus(path: str) -> int:
    """Persist the 60-case categorical corpus as JSON-lines."""
    items = build_full_corpus()
    with open(path, "w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(items)


__all__ = [
    "DEFAULT_SCENARIO_FILE", "FIXTURE_KINDS", "CATEGORY_SIZES",
    "DEFAULT_FULL_CORPUS_FILE", "GOLD_KEYS", "build_scenario", "load_scenarios",
    "sample_scenarios", "write_default_corpus", "build_full_corpus",
    "write_full_corpus",
]