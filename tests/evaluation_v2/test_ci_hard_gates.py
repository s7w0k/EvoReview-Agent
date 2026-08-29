'''CI hard gates lock dataset, baselines, runtime wiring and candidate use.'''
import copy
import unittest

from evoagent.evaluation_v2.experiment import DATASET_SHA256
from evoagent.evaluation_v2.gates import build_ci_hard_gates


def _runtime_case(candidate=False):
    invocations = {'eval-v2-evolved-review': 1} if candidate else {}
    return {
        'id': 'pr-1', 'architecture': 'six-agent-v2',
        'graph_shapes': [{'node_id': 's'}], 'called_agents': ['security-agent'],
        'feature_flags': {'planner': True},
        'resolved_policy': {'resolved_policy': 'baseline-high'},
        'prediction_details': [{
            'rule_id': 'SEC-EVAL', 'path': 'a.py', 'line': 3,
            'severity': 'critical',
        }],
        'skill_invocations': invocations,
    }


class CiHardGateTests(unittest.TestCase):
    def _fixture(self):
        current_case = _runtime_case()
        evolved_case = _runtime_case(candidate=True)
        systems = {
            'single_agent': {'metrics': {'detection': {'f1': 0.7143}}},
            'legacy_multi_agent': {'metrics': {'detection': {'f1': 0.825}}},
            'current_harness': {
                'metrics': {'detection': {'tp': 1},
                            'runtime': {'execution_success_rate': 1.0}},
                'case_results': [current_case],
            },
            'evolved_candidate': {
                'metrics': {'detection': {'tp': 1},
                            'runtime': {'execution_success_rate': 1.0}},
                'case_results': [evolved_case],
            },
        }
        evolution = {
            'candidate_manifest': {
                'candidate_id': 'eval-v2-evolved-review',
                'created_from_split': 'validation',
                'validation_dataset_sha256': DATASET_SHA256,
            },
            'validation': {
                'stable': {'case_results': [current_case]},
                'evolved': {'case_results': [evolved_case]},
            },
            'holdout': {},
        }
        return systems, evolution

    def test_complete_contract_passes(self):
        systems, evolution = self._fixture()
        result = build_ci_hard_gates(
            {'sha256': DATASET_SHA256}, systems, evolution)
        self.assertTrue(result['passed'])

    def test_zero_current_tp_fails_wiring_regression_gate(self):
        systems, evolution = self._fixture()
        systems = copy.deepcopy(systems)
        systems['current_harness']['metrics']['detection']['tp'] = 0
        result = build_ci_hard_gates(
            {'sha256': DATASET_SHA256}, systems, evolution)
        self.assertFalse(result['passed'])
        self.assertFalse(result['gates']['Current Harness TP positive']['passed'])


if __name__ == '__main__':
    unittest.main()
