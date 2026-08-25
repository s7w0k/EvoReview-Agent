"""Work Package 2: report-chat context building, model-output validation and
the OpenAI-compatible transport.  These are pure-unit tests with no HTTP, store
or feedback side effects.
"""
import socket
import unittest
import urllib.error
from unittest import mock

from evoagent import chat
from evoagent.chat import (
    ChatContextBuilder,
    ChatModelClient,
    ChatModelError,
    ChatModelNotConfigured,
    decode_model_output,
    report_fingerprint,
    validate_citations,
    validate_insights,
)


def _findings(n=2):
    return [
        {"rule_id": "SEC-EVAL", "severity": "critical", "path": "app.py", "line": 3,
         "title": "eval", "confidence": 0.9, "evidence": "eval(user_input)", "fix": ""}
        for _ in range(n)
    ]


def _added_lines(n=2):
    return [{"path": "app.py", "line": i + 1, "content": "x = %d" % i} for i in range(n)]


def _context(findings=None, added_lines=None, max_findings=chat.CHAT_MAX_FINDINGS,
             max_diff_lines=chat.CHAT_MAX_DIFF_LINES):
    builder = ChatContextBuilder(
        max_findings=max_findings, max_diff_lines=max_diff_lines,
        max_trace_items=30, memory_limit=4, context_tokens=10000,
    )
    return builder.build(
        repository="org/repo", risk="high",
        report={"summary": "contains eval", "risk": "high"},
        findings=findings or _findings(),
        added_lines=added_lines or _added_lines(),
        trace=[{"summary": "planner assigned"}],
        memories=[{"content": "previous eval feedback"}],
        question="SEC-EVAL 为什么是高风险？", history=[],
    )


class ReportFingerprintTests(unittest.TestCase):
    def test_stable_for_same_report(self):
        report = {"findings": [{"rule_id": "SEC-EVAL"}], "risk": "high"}
        self.assertEqual(report_fingerprint(report), report_fingerprint(report))

    def test_changes_on_report_change(self):
        a = report_fingerprint({"risk": "high"})
        b = report_fingerprint({"risk": "low"})
        self.assertNotEqual(a, b)


class ContextBuilderTests(unittest.TestCase):
    def test_references_and_fingerprint_are_reproducible(self):
        ctx = _context()
        self.assertEqual("report", ctx["references"][0]["type"])
        self.assertTrue(any(r["type"] == "finding" for r in ctx["references"]))
        self.assertTrue(any(r["type"] == "diff" for r in ctx["references"]))
        self.assertTrue(any(r["type"] == "trace" for r in ctx["references"]))
        self.assertIn("SEC-EVAL", ctx["text"])
        self.assertIn("为什么是高风险", ctx["text"])
        # reproducible fingerprint
        ctx2 = _context()
        self.assertEqual(ctx["context_fingerprint"], ctx2["context_fingerprint"])

    def test_findings_truncated_under_budget_keeps_question(self):
        ctx = _context(findings=_findings(5), max_findings=2)
        self.assertEqual(3, ctx["truncation"]["findings"])
        # only 2 finding references + report kept
        finding_refs = [r for r in ctx["references"] if r["type"] == "finding"]
        self.assertEqual(2, len(finding_refs))
        # question always preserved
        self.assertIn("为什么是高风险", ctx["text"])

    def test_diff_truncated_under_budget(self):
        ctx = _context(added_lines=_added_lines(5), max_diff_lines=2)
        self.assertEqual(3, ctx["truncation"]["diff_lines"])


class CitationValidationTests(unittest.TestCase):
    def test_forged_finding_and_diff_citations_dropped(self):
        ctx = _context()
        kept = validate_citations(
            [
                {"type": "finding", "ref": "finding:0"},
                {"type": "finding", "ref": "finding:99"},  # forged
                {"type": "diff", "path": "app.py", "line": 1},
                {"type": "diff", "path": "evil.py", "line": 999},  # forged
                {"type": "report", "ref": "report"},
                {"type": "memory", "ref": "made-up"},  # forged
            ],
            ctx["references"],
        )
        types = {(c["type"], c.get("ref"), c.get("path"), c.get("line")) for c in kept}
        self.assertIn(("finding", "finding:0", None, None), types)
        self.assertIn(("diff", None, "app.py", 1), types)
        self.assertIn(("report", "report", None, None), types)
        self.assertNotIn(("finding", "finding:99", None, None), types)
        self.assertNotIn(("memory", "made-up", None, None), types)

    def test_diff_must_land_on_a_real_added_line(self):
        ctx = _context(added_lines=[{"path": "app.py", "line": 7, "content": "x"}])
        kept = validate_citations(
            [{"type": "diff", "path": "app.py", "line": 7},
             {"type": "diff", "path": "app.py", "line": 8}],
            ctx["references"],
        )
        self.assertEqual(1, len(kept))
        self.assertEqual(7, kept[0]["line"])


class InsightValidationTests(unittest.TestCase):
    def test_valid_category_and_clamped_confidence(self):
        findings = _findings()
        insights = validate_insights(
            [{"category": "false_positive", "finding_ref": "finding:0",
              "note": "  不适用  ", "confidence": 1.7}],
            findings,
        )
        self.assertEqual(1, len(insights))
        self.assertEqual(1.0, insights[0]["confidence"])
        self.assertEqual("false_positive", insights[0]["category"])
        self.assertEqual(findings[0], insights[0]["finding"])

    def test_unknown_category_dropped(self):
        insights = validate_insights(
            [{"category": "nonsense", "finding_ref": "finding:0", "note": "x", "confidence": 0.5}],
            _findings(),
        )
        self.assertEqual([], insights)

    def test_invalid_finding_ref_never_becomes_high_confidence_rule_candidate(self):
        findings = _findings()
        insights = validate_insights(
            [{"category": "false_positive", "finding_ref": "finding:99", "note": "x", "confidence": 0.9}],
            findings,
        )
        self.assertEqual(1, len(insights))
        self.assertIsNone(insights[0]["finding"])
        self.assertIsNone(insights[0]["finding_ref"])


class DecodeModelOutputTests(unittest.TestCase):
    def test_non_json_raises_stable_error(self):
        with self.assertRaises(ChatModelError) as cm:
            decode_model_output("not json", [], [], True)
        self.assertEqual("invalid_output", cm.exception.reason)

    def test_non_object_json_raises(self):
        with self.assertRaises(ChatModelError):
            decode_model_output("[1,2]", [], [], True)

    def test_insights_dropped_when_disabled(self):
        references = _context()["references"]
        out = decode_model_output(
            '{"answer":"ok","insights":[{"category":"false_positive","finding_ref":"finding:0","note":"x","confidence":0.9}]}',
            references, _findings(), False,
        )
        self.assertEqual([], out["insights"])
        self.assertEqual("ok", out["answer"])

    def test_forged_citations_removed_from_decoded_output(self):
        references = _context()["references"]
        out = decode_model_output(
            '{"answer":"ok","citations":[{"type":"finding","ref":"finding:0"},{"type":"finding","ref":"finding:99"}],"insights":[]}',
            references, _findings(), True,
        )
        self.assertEqual(1, len(out["citations"]))


class ChatModelClientTests(unittest.TestCase):
    def test_not_configured(self):
        client = ChatModelClient("", "", "")
        with self.assertRaises(ChatModelNotConfigured):
            client.complete("sys", [{"role": "user", "content": "hi"}])

    def test_timeout_maps_to_domain_error(self):
        client = ChatModelClient("http://x", "k", "m")
        with mock.patch("urllib.request.urlopen", side_effect=socket.timeout("t")):
            with self.assertRaises(ChatModelError) as cm:
                client.complete("sys", [{"role": "user", "content": "hi"}])
        self.assertEqual("timeout", cm.exception.reason)

    def test_http_error_maps_to_domain_error(self):
        client = ChatModelClient("http://x", "k", "m")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
                "url", 500, "err", {}, None)):
            with self.assertRaises(ChatModelError) as cm:
                client.complete("sys", [{"role": "user", "content": "hi"}])
        self.assertEqual("http", cm.exception.reason)

    def test_rate_limit_maps_to_domain_error(self):
        client = ChatModelClient("http://x", "k", "m")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
                "url", 429, "rl", {}, None)):
            with self.assertRaises(ChatModelError) as cm:
                client.complete("sys", [{"role": "user", "content": "hi"}])
        self.assertEqual("rate_limit", cm.exception.reason)

    def test_non_string_assistant_content_raises(self):
        client = ChatModelClient("http://x", "k", "m")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b'{"choices":[{"message":{"content":123}}]}'
        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(ChatModelError) as cm:
                client.complete("sys", [{"role": "user", "content": "hi"}])
        self.assertEqual("invalid_output", cm.exception.reason)


if __name__ == "__main__":
    unittest.main()