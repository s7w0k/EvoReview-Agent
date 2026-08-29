'''Stable and frozen security skills are composed and invocation-attributed.'''
import unittest

from evoagent.diff_parser import parse_unified_diff
from evoagent.loop_agents.tools import build_expert_context, build_expert_definitions
from evoagent.reviewer import SecurityRuleReviewer
from evoagent.skill_evolution import DeclarativeSkillReviewer

from tests.evaluation_v2.helpers import make_risk_case


class ComposedSecuritySkillsTests(unittest.TestCase):
    def test_candidate_extends_stable_scanner_without_replacing_it(self):
        case = make_risk_case()
        artifact = {
            'name': 'evolved-extra', 'description': 'extra',
            'rules': [{
                'rule_id': 'CWE-95', 'severity': 'critical', 'match': 'eval(',
                'title': 'candidate', 'explanation': 'candidate evidence',
                'fix': 'remove', 'test': 'test', 'confidence': 0.8,
            }],
        }
        candidate = DeclarativeSkillReviewer(artifact, version=1)
        invocations = {}
        ctx = build_expert_context(
            case['diff'], parse_unified_diff(case['diff']),
            security_reviewers=[SecurityRuleReviewer(), candidate],
            security_reviewer_ids=['security-rule@1', 'candidate-x'],
            skill_invocations=invocations,
        )
        definition = next(
            item for item in build_expert_definitions(ctx)
            if item.tool.name == 'security_rule_scan')
        output = definition.tool.handler()
        rule_ids = {item['rule_id'] for item in output['findings']}
        self.assertIn('SEC-EVAL', rule_ids)
        self.assertIn('CWE-95', rule_ids)
        self.assertEqual({'security-rule@1': 1, 'candidate-x': 1}, invocations)


if __name__ == '__main__':
    unittest.main()
