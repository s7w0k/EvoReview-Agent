'''Extended Six-Agent domain rules fire on positives and stay silent on near-negatives.

These rules live in extrules.reviewer.EXTENDED_RULES and are consumed only by the
deterministic specialist reviewers (Security/Reliability). Single-agent and legacy
baselines read LocalRuleReviewer.RULES and must remain unaffected.
'''
import unittest

from evoagent.diff_parser import parse_unified_diff
from evoagent.reviewer import (
    EXTENDED_RULES,
    ReliabilityRuleReviewer,
    SecurityRuleReviewer,
)

SEC = SecurityRuleReviewer()
REL = ReliabilityRuleReviewer()

# (rule_id, reviewer, positive line, near-negative line)
CASES = [
    # Security
    ("SEC-PATH-TRAVERSAL", SEC, "data = open(root + user_input, 'rb')", "data = open('/var/log/app.log', 'a')"),
    ("SEC-YAML-LOAD", SEC, "cfg = yaml.load(stream)", "cfg = yaml.safe_load(stream)"),
    ("SEC-PICKLE-LOAD", SEC, "obj = pickle.load(f)", "obj = json.load(f)"),
    ("SEC-WEAK-HASH", SEC, "digest = md5(password.encode())", "digest = hashlib.sha256(password.encode()).hexdigest()"),
    ("SEC-WEAK-RANDOM", SEC, "token = random.choice(chars)", "token = secrets.token_hex(16)"),
    ("SEC-INSECURE-TEMPFILE", SEC, "p = tempfile.mktemp()", "p = tempfile.NamedTemporaryFile(delete=True)"),
    ("SEC-ASSERT-AUTH", SEC, "assert is_admin, 'denied'", "if not is_admin: raise PermissionError"),
    ("SEC-INSECURE-COOKIE", SEC, "resp.set_cookie('sid', token, secure=False)", "resp.set_header('X-Request-Id', rid)"),
    ("SEC-OPEN-REDIRECT", SEC, "return redirect(next_url)", "return redirect('https://example.com/done')"),
    ("SEC-LOG-FORGING", SEC, "log.error(f\"failed for {user_input}\")", "log.error(\"failed for %s\", user_id)"),
    # Reliability
    ("REL-UNBOUNDED-RETRY", REL, "while True:", "while attempts < max_retries:"),
    ("REL-FLOAT-MONEY", REL, "tax = amount + 0.1", "tax = amount + Decimal('10')"),
    ("REL-NAIVE-DATETIME", REL, "now = datetime.now()", "now = datetime.now(timezone.utc)"),
    ("REL-BLOCKING-ASYNC", REL, "time.sleep(1)", "await asyncio.sleep(1)"),
    ("REL-NONATOMIC-WRITE", REL, "f = open(path, 'w')", "f = open(path, 'rb')"),
]


def _findings(reviewer, line):
    diff = f"--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n+{line}\n"
    return reviewer.review(diff, parse_unified_diff(diff))


class ExtendedDomainRuleTests(unittest.TestCase):
    def test_rule_metadata_is_complete(self):
        self.assertGreaterEqual(len(EXTENDED_RULES), 15)
        for rule_id, severity, pattern, title, explanation, fix, test in EXTENDED_RULES:
            self.assertTrue(rule_id, "missing rule id")
            self.assertTrue(title, rule_id)
            self.assertTrue(explanation, rule_id)
            self.assertTrue(fix, rule_id)
            self.assertTrue(test, rule_id)
            self.assertTrue(pattern.pattern, rule_id)

    def test_positives_fire_and_negatives_stay_silent(self):
        for rule_id, reviewer, positive, negative in CASES:
            pos = {f.rule_id for f in _findings(reviewer, positive)}
            neg = {f.rule_id for f in _findings(reviewer, negative)}
            self.assertIn(rule_id, pos, f"{rule_id} did not fire on positive")
            self.assertNotIn(rule_id, neg, f"{rule_id} false-positive on safe line")

    def test_all_extended_ids_are_mapped(self):
        from evoagent.evaluation_harness import RULE_TO_CWE
        extended_ids = {rule_id for rule_id, *_ in EXTENDED_RULES}
        self.assertTrue(extended_ids <= set(RULE_TO_CWE))

    def test_precision_placeholders_do_not_fire(self):
        # Clean/dataset "clean" PRs that must NOT be reported.
        for line in (
            'resp.set_cookie("sid", value, secure=True)',
            'response.set_cookie("sid", value, httponly=True, secure=True)',
            'token = "test-placeholder"',
        ):
            for reviewer in (SEC, REL):
                rule_ids = {f.rule_id for f in _findings(reviewer, line)}
                self.assertNotIn("SEC-INSECURE-COOKIE", rule_ids, line)
                self.assertNotIn("SEC-HARDCODED-SECRET", rule_ids, line)

    def test_precision_real_secrets_still_fire(self):
        for line in (
            'api_key = "production-secret-3"',
            'secret = "live-credential-9f2a"',
            'resp.set_cookie("sid", token, secure=False)',
        ):
            rule_ids = {f.rule_id for f in _findings(SEC, line)}
            self.assertTrue(
                rule_ids & {"SEC-HARDCODED-SECRET", "SEC-INSECURE-COOKIE"},
                f"expected a security finding for {line}, got {rule_ids}",
            )

    def test_residual_fn_coverage(self):
        # Phase-7 residual FNs (dataset change_17/34/39/35/25) must now fire
        # so the routing/coverage gap is closed for HIGH risk path traversal
        # and medium precision-sensitive float arithmetic.
        pos = [
            # CWE-22 path traversal via '/' joined tainted path
            ("SEC-PATH-TRAVERSAL", "return open(base / user_path).read()"),
            # CWE-601 open redirect with an arbitrary variable argument
            ("SEC-OPEN-REDIRECT", "return redirect(value)"),
            # CWE-682 float arithmetic on a computed value
            ("REL-FLOAT-MONEY", "total = float(value) * 100"),
            # CWE-532 debug print
            ("REL-DEBUG-PRINT", "print(value)"),
        ]
        for rule_id, line in pos:
            reviewer = SEC if rule_id.startswith("SEC") else REL
            self.assertIn(
                rule_id, {f.rule_id for f in _findings(reviewer, line)}, line
            )
        # structured logging stays silent (no log-forging broadening)
        neg_line = 'log.error("failed for %s", user_id)'
        self.assertNotIn(
            "SEC-LOG-FORGING", {f.rule_id for f in _findings(SEC, neg_line)}
        )


if __name__ == "__main__":
    unittest.main()