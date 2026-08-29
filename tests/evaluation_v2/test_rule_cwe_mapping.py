'''Every rule emitted by stable Six-Agent scanners must map to a CWE.'''
import unittest

from evoagent.evaluation_harness import RULE_TO_CWE
from evoagent.reviewer import ReliabilityRuleReviewer, SecurityRuleReviewer
from evoagent.ast_analysis import SEVERITY_MAP


class RuleCweMappingTests(unittest.TestCase):
    def test_stable_produced_rule_mapping_coverage_is_complete(self):
        produced = (set(SecurityRuleReviewer.rule_ids)
                    | set(ReliabilityRuleReviewer.rule_ids)
                    | set(SEVERITY_MAP))
        mapped = {rule for rule in produced if rule in RULE_TO_CWE}
        self.assertEqual(produced, mapped)
        self.assertEqual(1.0, len(mapped) / len(produced))

    def test_frozen_candidate_cwe_ids_are_valid_identity_mappings(self):
        for cwe in set(RULE_TO_CWE.values()):
            self.assertTrue(cwe.startswith('CWE-'))


if __name__ == '__main__':
    unittest.main()
