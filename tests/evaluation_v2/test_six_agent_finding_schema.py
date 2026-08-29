'''Six-agent findings must satisfy the frozen matcher schema contract.'''
import gc
import shutil
import tempfile
import time
import unittest
from os.path import join

from evoagent.evaluation_harness import RULE_TO_CWE
from evoagent.evaluation_v2.adapters import CurrentHarnessEvaluationAdapter
from evoagent.models import Severity

from tests.evaluation_v2.helpers import make_risk_case


class SixAgentFindingSchemaTests(unittest.TestCase):
    def test_produced_findings_are_matcher_compatible(self):
        tmp = tempfile.mkdtemp()
        adapter = CurrentHarnessEvaluationAdapter(join(tmp, 'schema.db'))
        try:
            result = adapter.review_case(make_risk_case())
        finally:
            adapter.close()
            gc.collect()
            for _ in range(5):
                try:
                    shutil.rmtree(tmp)
                    break
                except PermissionError:
                    time.sleep(0.1)
                    gc.collect()
        self.assertTrue(result.findings)
        for finding in result.findings:
            self.assertTrue(finding.path)
            self.assertGreater(finding.line, 0)
            self.assertTrue(finding.rule_id)
            self.assertIsInstance(finding.severity, Severity)
            self.assertTrue(
                finding.rule_id in RULE_TO_CWE or finding.rule_id.startswith('CWE-'),
                finding.rule_id,
            )


if __name__ == '__main__':
    unittest.main()
