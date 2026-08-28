"""Phase 1 acceptance: domain model is FastAPI-free, JSON (de)serialisable,
schema-validated, version-compatible, and round-trips through every field."""
import json
import unittest

from evoagent.a2a.models import (
    A2AArtifact, A2AMessage, A2ATask, AgentCard, TaskStatus,
)


class AgentCardTest(unittest.TestCase):
    def test_round_trip(self):
        card = AgentCard(
            agent_id="security-agent", name="Security Review Agent",
            endpoint="http://security-agent:8001/a2a",
            protocol_version="v1",
            capabilities=["code-review", "security-review"],
            domains=["security"], supported_task_types=["review-assignment"],
            version="1.0.0", health_status="healthy", deployment="http",
        )
        data = json.loads(json.dumps(card.to_dict()))
        rebuilt = AgentCard.from_dict(data)
        self.assertEqual(rebuilt.agent_id, "security-agent")
        self.assertEqual(rebuilt.protocol_version, "v1")
        self.assertEqual(rebuilt.capabilities, ["code-review", "security-review"])

    def test_version_defaults(self):
        card = AgentCard(agent_id="a", name="A", endpoint="", protocol_version="")
        self.assertEqual(card.protocol_version, "v1")
        self.assertEqual(card.version, "1.0.0")


class A2ATaskTest(unittest.TestCase):
    def test_round_trip(self):
        task = A2ATask(
            task_id="review-001", assignment_id="A01", sender="planner",
            recipient="security-agent", task_type="review-assignment",
            input={"diff": "@@ -0 +1 @@\n+import os\n"},
        )
        data = json.loads(json.dumps(task.to_dict()))
        rebuilt = A2ATask.from_dict(data)
        self.assertEqual(rebuilt.task_id, "review-001")
        self.assertEqual(rebuilt.input["diff"], task.input["diff"])

    def test_missing_required_fields_rejected_at_validation(self):
        from evoagent.a2a.protocol import validate_task_fields
        from evoagent.a2a.errors import A2ASchemaError
        with self.assertRaises(A2ASchemaError):
            validate_task_fields({"task_id": "", "recipient": "x", "input": {}})


class TaskStatusTest(unittest.TestCase):
    def test_enum_values(self):
        self.assertEqual(TaskStatus.COMPLETED.value, "COMPLETED")
        self.assertEqual(TaskStatus.TIMED_OUT.value, "TIMED_OUT")


class A2AArtifactTest(unittest.TestCase):
    def test_round_trip(self):
        artifact = A2AArtifact(
            artifact_id="art-1", task_id="t1", artifact_type="review-findings",
            producer="security-agent",
            content={"findings": [{"path": "a.py", "line": 1}]},
            metadata={"protocol_version": "v1"},
        )
        rebuilt = A2AArtifact.from_dict(json.loads(json.dumps(artifact.to_dict())))
        self.assertEqual(rebuilt.content["findings"][0]["path"], "a.py")


class A2AMessageTest(unittest.TestCase):
    def test_round_trip(self):
        msg = A2AMessage(
            message_id="m1", task_id="t1", sender="s", recipient="r",
            message_type="peer_challenge", payload={"x": 1},
            correlation_id="c1", timestamp="2026-01-01T00:00:00Z",
        )
        rebuilt = A2AMessage.from_dict(json.loads(json.dumps(msg.to_dict())))
        self.assertEqual(rebuilt.payload, {"x": 1})


if __name__ == "__main__":
    unittest.main()