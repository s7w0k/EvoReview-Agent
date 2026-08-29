'''Runtime hard gates fail before invalid Six-Agent results reach scoring.'''
import tempfile
import unittest
from os.path import join

from evoagent.evaluation_v2.adapters import (
    CurrentHarnessEvaluationAdapter, EvaluationExecutionResult,
)

from tests.evaluation_v2.helpers import make_risk_case


class RuntimeHardGateTests(unittest.TestCase):
    def test_missing_runtime_proof_fails_closed(self):
        adapter = CurrentHarnessEvaluationAdapter(join(tempfile.gettempdir(), 'unused.db'))
        result = EvaluationExecutionResult(architecture='legacy')
        with self.assertRaisesRegex(RuntimeError, 'runtime wiring gate failed'):
            adapter.validate_execution(make_risk_case(), result)

    def test_complete_runtime_proof_passes(self):
        adapter = CurrentHarnessEvaluationAdapter(join(tempfile.gettempdir(), 'unused.db'))
        result = EvaluationExecutionResult(
            architecture='six-agent-v2',
            graph_shapes=[{'node_id': 'security'}],
            called_agents=['security-agent'],
        )
        adapter.validate_execution(make_risk_case(), result)


if __name__ == '__main__':
    unittest.main()
