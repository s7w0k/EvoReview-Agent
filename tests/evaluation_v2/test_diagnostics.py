'''False-negative attribution separates routing from rule and matcher failures.'''
import unittest

from evoagent.evaluation_v2.diagnostics import (
    analyze_false_negatives, produced_rule_mapping_coverage,
)


class DiagnosticsTests(unittest.TestCase):
    def test_security_gold_without_security_route_is_labeled(self):
        results = [{
            'id': 'pr-1', 'called_agents': ['reliability-agent'],
            'expected_findings': [{
                'rule_id': 'SEC-EVAL', 'cwe': 'CWE-95', 'severity': 'critical',
                'path': 'a.py', 'start_line': 3, 'end_line': 3,
            }],
            'unmatched_expected_indices': [0], 'prediction_details': [],
            'loop_steps_by_agent': {'reliability-agent': 2},
            'skill_invocations': {}, 'collaboration': {},
        }]
        analysis = analyze_false_negatives(results)
        self.assertEqual('NO_AGENT_ROUTED', analysis[0]['reason'])

    def test_direct_cwe_candidate_rule_counts_as_mapped(self):
        coverage = produced_rule_mapping_coverage([{
            'prediction_details': [{'rule_id': 'CWE-502'}]}])
        self.assertEqual(1.0, coverage['coverage'])


if __name__ == '__main__':
    unittest.main()
