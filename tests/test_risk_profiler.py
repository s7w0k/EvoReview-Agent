import unittest

from evoagent.diff_parser import parse_unified_diff
from evoagent.policy.risk import RiskProfile, RiskProfiler


def make_diff(*added_lines):
    body = "\n".join("+%s" % line for line in added_lines)
    return (
        "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n" + body + "\n"
    )


class RiskProfilerTest(unittest.TestCase):
    def setUp(self):
        self.profiler = RiskProfiler()

    def test_clean_code_low_risk(self):
        parsed = parse_unified_diff(make_diff("total = a + b"))
        profile = self.profiler.profile(parsed)
        self.assertEqual(profile.level, "low")

    def test_high_risk_path_elevates(self):
        parsed = parse_unified_diff(
            "--- a/auth/login.py\n+++ a/auth/login.py\n@@ -1 +1 @@\n-old\n+return \"ok\"\n"
        )
        profile = self.profiler.profile(parsed)
        self.assertGreaterEqual(profile.rank, RiskProfile("medium").rank)

    def test_dangerous_token_elevates(self):
        parsed = parse_unified_diff(make_diff('cursor.execute("SELECT * FROM x")'))
        profile = self.profiler.profile(parsed)
        self.assertGreaterEqual(profile.rank, RiskProfile("medium").rank)

    def test_shell_injection_elevates(self):
        parsed = parse_unified_diff(make_diff('subprocess.run(cmd, shell=True)'))
        profile = self.profiler.profile(parsed)
        self.assertGreaterEqual(profile.rank, RiskProfile("medium").rank)

    def test_hardcoded_secret_elevates(self):
        parsed = parse_unified_diff(
            make_diff('password = "b2f6a1c0d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f"')
        )
        profile = self.profiler.profile(parsed)
        self.assertGreaterEqual(profile.rank, RiskProfile("medium").rank)

    def test_sensitive_path_plus_dangerous_token_is_critical(self):
        parsed = parse_unified_diff(
            "--- a/security/gate.py\n+++ b/security/gate.py\n"
            "@@ -1 +1 @@\n-old\n+result = eval(payload)\n"
        )
        profile = self.profiler.profile(parsed)
        self.assertEqual(profile.level, "critical")

    def test_large_volume_elevates(self):
        big = ["x = %d" % i for i in range(600)]
        parsed = parse_unified_diff(make_diff(*big))
        profile = self.profiler.profile(parsed)
        self.assertGreaterEqual(profile.rank, RiskProfile("medium").rank)

    def test_reasons_present(self):
        parsed = parse_unified_diff(
            "--- a/auth/login.py\n+++ b/auth/login.py\n@@ -1 +1 @@\n-old\n+return \"tok\"\n"
        )
        profile = self.profiler.profile(parsed)
        self.assertTrue(profile.reasons)


if __name__ == "__main__":
    unittest.main()