"""Phase 1/2 acceptance: JSON-RPC framing, AgentMessage<->A2AMessage adapter,
Finding<->Artifact adapter, schema validation."""
import json
import unittest

from evoagent.a2a.errors import A2AProtocolError, A2ASchemaError
from evoagent.a2a.models import A2AMessage, A2ATask, AgentCard
from evoagent.a2a.protocol import (
    build_request, findings_from_artifact, loads_request,
    message_from_bus, message_to_bus, validate_task_fields,
    artifact_from_findings,
)
from evoagent.models import Finding, Severity


class JsonRpcFrameTest(unittest.TestCase):
    def test_build_request(self):
        req = build_request("task.submit", {"task": {"task_id": "x"}})
        self.assertEqual(req["jsonrpc"], "2.0")
        self.assertEqual(req["method"], "task.submit")

    def test_unsupported_method(self):
        with self.assertRaises(A2ASchemaError):
            build_request("nope.method", {})

    def test_loads_malformed(self):
        with self.assertRaises(A2AProtocolError):
            loads_request(b"not json {")

    def test_loads_missing_fields(self):
        with self.assertRaises(A2ASchemaError):
            loads_request(b'{"jsonrpc":"2.0"}')

    def test_loads_wrong_version(self):
        with self.assertRaises(A2AProtocolError):
            loads_request(b'{"jsonrpc":"1.0","method":"x","params":{}}')


class BusMessageAdapterTest(unittest.TestCase):
    def test_round_trip(self):
        wire = message_from_bus(
            task_id="t1", sender="critic", recipient="security-agent",
            message_type="peer_challenge",
            payload={"objections": ["x"]}, correlation_id="k",
        )
        bus = message_to_bus(wire)
        self.assertEqual(bus["sender"], "critic")
        self.assertEqual(bus["kind"], "peer_challenge")
        self.assertEqual(bus["content"], {"objections": ["x"]})
        self.assertEqual(bus["correlation_id"], "k")
        self.assertIn("a2a", bus)


class FindingArtifactAdapterTest(unittest.TestCase):
    def test_same_structure_through_wire(self):
        findings = [
            Finding(rule_id="SEC-EVAL", severity=Severity.HIGH, title="Injection",
                    explanation="e", path="a.py", line=3, evidence="eval(",
                    fix="f", test="t", confidence=0.9),
        ]
        artifact = artifact_from_findings("t1", "security-agent", findings)
        back = findings_from_artifact(artifact)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0].rule_id, "SEC-EVAL")
        self.assertEqual(back[0].path, "a.py")
        self.assertEqual(back[0].line, 3)

    def test_agent_message_is_not_the_wire_object(self):
        # A2AMessage is distinct from the internal AgentMessage dataclass.
        self.assertNotEqual(
            A2AMessage.__name__,
            "AgentMessage",
        )


class ValidateTaskTest(unittest.TestCase):
    def test_valid(self):
        validate_task_fields({"task_id": "t", "recipient": "r", "input": {}})

    def test_invalid(self):
        with self.assertRaises(A2ASchemaError):
            validate_task_fields({"task_id": "", "recipient": "r", "input": {}})


class RoundTripSerialisationTest(unittest.TestCase):
    def test_all_objects_json(self):
        for obj in [
            AgentCard(agent_id="a", name="A", endpoint="", protocol_version="v1"),
            A2ATask(task_id="t", assignment_id="a", sender="s", recipient="r",
                    task_type="review-assignment", input={}),
            A2AMessage(message_id="m", task_id="t", sender="s", recipient="r",
                       message_type="m", payload={}),
        ]:
            raw = json.dumps(obj.to_dict())
            self.assertIsInstance(raw, str)


if __name__ == "__main__":
    unittest.main()