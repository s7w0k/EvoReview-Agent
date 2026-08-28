"""Small deterministic case builders shared by the evaluation_v2 tests.

The helpers mirror the frozen ``pr_diff_100.jsonl`` schema (schema_version 1 /
``expected_findings`` with ``rule_id`` in the scorer's ``RULE_TO_CWE`` so a
``Finding`` matches ground truth by CWE) and build real unified diffs so the
adapters can parse added lines.
"""
from evoagent.evaluation_harness import RULE_TO_CWE


def unified_diff(path: str, before: str, after: str) -> str:
    before_lines = before.splitlines() or [""]
    after_lines = after.splitlines() or [""]
    body = ["@@ -1,%d +1,%d @@" % (len(before_lines), len(after_lines))]
    # Emit the full file as removed+added lines for simplicity.
    for line in before_lines:
        body.append("-" + line)
    for line in after_lines:
        body.append("+" + line)
    return "\n".join(["--- a/%s" % path, "+++ b/%s" % path] + ["%s\n" % line for line in body])


def make_risk_case(case_id="pr-0001", rule_id="SEC-EVAL", severity="critical",
                   added_line="    result = eval(value)\n", line=3,
                   path="src/change.py", split="validation",
                   repository="acme/service-01"):
    """A risk case whose expected finding ``rule_id`` lives in RULE_TO_CWE."""
    cwe = RULE_TO_CWE[rule_id]
    diff = unified_diff(path, "def process(value):\n    return value\n",
                        added_line)
    return {
        "id": case_id,
        "repository": repository,
        "pull_request": int(case_id.split("-")[1]),
        "split": split,
        "diff": diff,
        "expected_findings": [{
            "cwe": cwe, "rule_id": rule_id, "severity": severity,
            "path": path, "start_line": line, "end_line": line,
        }],
    }


def make_clean_case(case_id="pr-clean-01", path="src/clean.py",
                    split="validation", repository="acme/service-01"):
    """A clean case with no risk pattern on the added line."""
    return {
        "id": case_id,
        "repository": repository,
        "pull_request": 9001,
        "split": split,
        "diff": unified_diff(
            path,
            "def process(value):\n    return value\n",
            "def process(value):\n    normalized = str(value).strip()\n    return normalized\n"),
        "expected_findings": [],
    }