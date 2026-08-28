"""Coordinator-side adapters (Phase 4/6): ``RemoteReviewerAdapter``.

Implements the existing :class:`Reviewer` interface so ``MultiAgentCoordinator``
needs no deep change: a Remote Security/Reliability Reviewer exposes the same
``review`` / ``review_assignment`` surface, but is backed by an A2A transport.

Resilience (Phase 7): transient failures are retried, then the call falls over
to a backup Remote agent and finally a local reviewer.  Every step is mirrored
into the CollaborationBus trace mappings from the plan:
``remote_task_submitted / remote_task_running / remote_artifact_received /
remote_agent_failure / remote_agent_timeout``.
"""
from typing import Any, Dict, List, Optional

from ..reviewer import LocalRuleReviewer, Reviewer

from .errors import (
    A2AError,
    A2AProtocolError,
    A2ASchemaError,
    A2ATimeoutError,
    A2AUnauthorizedError,
)
from .models import A2ATask
from .protocol import findings_from_artifact
from .resilience import CircuitBreaker, FallbackChain, RetryPolicy
from .transport import A2ATransport, ResilientTransport

#: Contract violations that must fail fast (no retry / no silent local fallback).
_IDENTITY_ERRORS = (A2AUnauthorizedError, A2ASchemaError, A2AProtocolError)

_TRACE_KIND = {
    "submitted": "remote_task_submitted",
    "running": "remote_task_running",
    "artifact": "remote_artifact_received",
    "failure": "remote_agent_failure",
    "timeout": "remote_agent_timeout",
}


def build_a2a_task(
    *, diff: str, recipient: str, task_id: str = "", assignment_id: str = "",
    sender: str = "coordinator", task_type: str = "review-assignment",
    guidance: Optional[List[str]] = None, correlation_id: str = "",
) -> A2ATask:
    input_data: Dict[str, Any] = {"diff": diff}
    context: Dict[str, Any] = {}
    if guidance:
        context["guidance"] = list(guidance)
    if assignment_id:
        context["assignment_id"] = assignment_id
    return A2ATask(
        task_id=task_id or ("a2a-" + assignment_id.lower()),
        assignment_id=assignment_id,
        sender=sender,
        recipient=recipient,
        task_type=task_type,
        input=input_data,
        context=context,
        correlation_id=correlation_id or assignment_id,
    )


def artifact_to_findings(artifacts: List[dict]) -> list:
    from .models import A2AArtifact
    findings: List[Any] = []
    for raw in artifacts or []:
        findings.extend(findings_from_artifact(A2AArtifact.from_dict(raw)))
    return findings


class RemoteReviewerAdapter(Reviewer):
    """A :class:`Reviewer` whose execution is delegated to a remote A2A Agent."""

    def __init__(
        self, card: dict, transport: A2ATransport, *,
        local_fallback: Optional[Reviewer] = None,
        backup_card: Optional[dict] = None, backup_transport: Optional[A2ATransport] = None,
        task_type: str = "review-assignment",
        retry: Optional[RetryPolicy] = None, breaker: Optional[CircuitBreaker] = None,
        bus=None, timeout_seconds: Optional[float] = None,
    ):
        self.card = dict(card)
        self.name = str(card.get("name") or card.get("agent_id", "remote"))
        self.domains = tuple(card.get("domains", []))
        self.agent_id = str(card.get("agent_id", self.name))
        self.endpoint = str(card.get("endpoint", ""))
        self.transport = ResilientTransport(
            transport, retry=retry, breaker=breaker,
            backup=backup_transport, timeout_seconds=timeout_seconds,
        )
        self.local_fallback = local_fallback or LocalRuleReviewer()
        self.backup_card = dict(backup_card or {})
        self.task_type = task_type
        self.bus = bus

    # Reviewer interface ----------------------------------------------------
    def review(self, diff: str, parsed) -> list:
        task = build_a2a_task(diff=diff, recipient=self.agent_id, task_type=self.task_type)
        return self._execute(task)

    def review_assignment(self, diff: str, parsed, assignment: dict,
                          feedback: List[str], inbox: List[dict]) -> list:
        task = build_a2a_task(
            diff=diff, recipient=self.agent_id,
            assignment_id=str(assignment.get("assignment_id", "") or ""),
            task_type=self.task_type,
            guidance=list(feedback or []),
            correlation_id=str(assignment.get("assignment_id", "")),
        )
        return self._execute(task)

    # Execution -------------------------------------------------------------
    def _collect(self, transport: A2ATransport, card: dict, task: A2ATask) -> list:
        self._trace("submitted", task, {"attempt": "primary"})
        record = transport.submit_task(_card(card), task)
        self._trace("running", task, {"status": record.get("status")})
        artifacts = transport.get_artifacts(_card(card), task.task_id)
        findings = artifact_to_findings(artifacts)
        self._trace("artifact", task, {"artifacts": len(artifacts), "findings": len(findings)})
        return findings

    def _execute(self, task: A2ATask) -> list:
        primary = lambda: self._collect(self.transport, self.card, task)  # noqa: E731
        providers = [primary]

        def backup_provider():
            if self.backup_card and self.transport.backup is not None:
                self._trace("submitted", task, {"attempt": "backup"})
                backup = self.transport.backup
                return self._collect(backup, self.backup_card, task)
            raise A2AError("no backup transport configured")

        if self.backup_card:
            providers.append(backup_provider)

        def local_provider():
            self._trace("failure", task, {"mode": "local-fallback"})
            return self.local_fallback.review(
                (task.input or {}).get("diff", ""), _parsed_of(task),
            )

        providers.append(local_provider)
        chain = FallbackChain(on_fallback=lambda mode, reason: self._trace(
            "failure", task, {"mode": mode, "reason": reason},
        ))
        try:
            return chain.run(providers, identity_errors=_IDENTITY_ERRORS)
        except A2ATimeoutError as exc:
            self._trace("timeout", task, {"error": str(exc)[:200]})
            raise
        except Exception:  # noqa: BLE001
            raise

    def _trace(self, kind: str, task: A2ATask, detail: Dict[str, Any]) -> None:
        if self.bus is None:
            return
        content = dict(detail)
        content["task_id"] = task.task_id
        content["agent_id"] = self.agent_id
        correlation = task.correlation_id or task.assignment_id
        self.bus.send(
            self.name, "trace", _TRACE_KIND.get(kind, "remote_event"), content, correlation,
        )


def _card(value: dict):
    from .models import AgentCard
    return AgentCard.from_dict(value)


def _parsed_of(task: A2ATask):
    from ..diff_parser import parse_unified_diff
    return parse_unified_diff((task.input or {}).get("diff", ""))


__all__ = ["RemoteReviewerAdapter", "build_a2a_task", "artifact_to_findings"]