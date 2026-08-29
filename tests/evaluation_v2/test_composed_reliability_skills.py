"""Stable and frozen reliability skills are composed and invocation-attributed.

Plan §Phase 3 gate: a reliability-domain candidate must be composed into the
``reliability_reviewers`` list (not the security list) so the Reliability
specialist runs it -- it must never be silently side-lined on the security side.
"""
import unittest

from evoagent.diff_parser import parse_unified_diff
from evoagent.loop_agents.tools import build_expert_context, build_expert_definitions
from evoagent.reviewer import ReliabilityRuleReviewer
from evoagent.skill_evolution import DeclarativeSkillReviewer

from tests.evaluation_v2.helpers import unified_diff


def _reliability_case():
    """A diff that triggers the stable REL-EMPTY-EXCEPT rule on an added line."""
    diff = unified_diff(
        "src/service.py",
        "def handler(value):\n    return value\n",
        "def handler(value):\n    try:\n        return int(value)\n"
        "    except Exception:\n        pass\n",
    )
    return diff


class ComposedReliabilitySkillsTests(unittest.TestCase):
    def test_reliability_candidate_joins_reliability_scanner(self):
        diff = _reliability_case()
        artifact = {
            'name': 'evolved-reliable', 'description': 'reliability candidate',
            'domain': 'reliability',
            'rules': [{
                'rule_id': 'CWE-703', 'severity': 'medium',
                'match': 'except Exception:', 'domain': 'reliability',
                'title': 'reliability candidate', 'explanation': 'evidence',
                'fix': 'handle', 'test': 'test', 'confidence': 0.8,
            }],
        }
        candidate = DeclarativeSkillReviewer(artifact, version=1)
        self.assertEqual('reliability', candidate.artifact['domain'])
        invocations = {}
        ctx = build_expert_context(
            diff, parse_unified_diff(diff),
            reliability_reviewers=[ReliabilityRuleReviewer(), candidate],
            reliability_reviewer_ids=['reliability-rule@1', 'candidate-rel'],
            skill_invocations=invocations,
        )
        definition = next(
            item for item in build_expert_definitions(ctx)
            if item.tool.name == 'reliability_rule_scan')
        output = definition.tool.handler()
        rule_ids = {item['rule_id'] for item in output['findings']}
        self.assertIn('REL-EMPTY-EXCEPT', rule_ids,
                      "stable reliability rule still runs")
        self.assertIn('CWE-703', rule_ids,
                      "reliability-domain candidate rule runs in reliability scan")
        self.assertEqual({'reliability-rule@1': 1, 'candidate-rel': 1}, invocations)

    def test_reliability_candidate_is_not_invoked_by_security_scan(self):
        """A reliability-only candidate is not side-lined into security."""
        diff = _reliability_case()
        artifact = {
            'name': 'evolved-reliable2', 'description': 'reliability candidate',
            'rules': [{
                'rule_id': 'CWE-703', 'severity': 'medium',
                'match': 'except Exception:', 'domain': 'reliability',
            }],
        }
        candidate = DeclarativeSkillReviewer(artifact, version=1)
        self.assertEqual('reliability', candidate.artifact['domain'])
        ctx = build_expert_context(
            diff, parse_unified_diff(diff),
            security_reviewers=[ReliabilityRuleReviewer()],
            security_reviewer_ids=['reliability-rule@1'],
        )
        # The candidate is NOT among the security reviewers.
        self.assertNotIn(candidate, ctx._security_reviewers)


if __name__ == '__main__':
    unittest.main()