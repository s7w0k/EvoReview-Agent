"""Checkpointed review workflow powered by EvoAgent's own runtime."""
import threading
import uuid
from typing import Any, Dict, Optional, TypedDict

from .decision_trace.trace import TraceEvent as DecisionTraceEvent
from .diff_parser import ParsedDiff, parse_unified_diff
from .models import ChangedLine, Finding, ReviewReport, Severity, TaskState, TraceEvent
from .confidence import apply_enhancement, classify
from .finding_cluster import cluster_findings
from .reviewer import Reviewer
from .runtime import (
    AgentRuntime, RuntimeBudgetExceeded, RuntimeCancelled, RuntimeNode,
)
from .store import TaskStore, utc_now


ALLOWED = {
    TaskState.PENDING: {TaskState.PLANNING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.PLANNING: {TaskState.EXECUTING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.EXECUTING: {TaskState.REVIEWING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.REVIEWING: {TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED},
}


class RuntimeState(TypedDict, total=False):
    task_id: str
    repository: str
    pull_request: Optional[int]
    tenant_id: str
    diff: str
    parsed: Dict[str, Any]
    findings: list
    report: Dict[str, Any]


BudgetExceeded = RuntimeBudgetExceeded
TaskCancelled = RuntimeCancelled


class ReviewHarness:
    node_order = ("planning", "executing", "reviewing")

    def __init__(
        self, store: TaskStore, reviewer: Reviewer, max_steps: int = 8,
        timeout_seconds: int = 120, node_retries: int = 2, observability=None,
        finding_clustering: str = "off", confidence_enhance: bool = False,
        confidence_buckets: tuple = (0.8, 0.5),
        execution_policy=None, execution_context=None,
        recovery_manager=None, trace_logger=None, replay_repository=None,
    ):
        self.store = store
        self.reviewer = reviewer
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds
        self.node_retries = node_retries
        self.observability = observability
        self.finding_clustering = finding_clustering
        self.confidence_enhance = confidence_enhance
        self.confidence_buckets = confidence_buckets
        # Closed-loop wiring (plan section 5.5): a resolved execution policy
        # pins the runtime budget; the context/recovery/trace handles are kept
        # for the replay and recovery phases.
        self.execution_policy = execution_policy
        self.execution_context = execution_context
        self.recovery_manager = recovery_manager
        self.trace_logger = trace_logger
        self.replay_repository = replay_repository
        self.name = "evoagent-runtime"
        self._ctx = threading.local()
        self.runtime = AgentRuntime(
            max_steps, timeout_seconds, node_retries,
            execution_policy=execution_policy,
            recovery_manager=recovery_manager,
        )

    def run(
        self, task_id: str, repository: str, pull_request: Optional[int], diff: str,
        tenant_id: str = "default",
    ) -> ReviewReport:
        task = self.store.get(task_id)
        if task and task.get("state") == TaskState.SUCCESS.value and task.get("report"):
            return self._report_from_dict(task["report"])
        state: RuntimeState = {
            "task_id": task_id, "repository": repository,
            "pull_request": pull_request, "diff": diff, "tenant_id": tenant_id,
        }
        self._ctx.step = max([item["step"] for item in (task or {}).get("trace", [])] or [0])
        self._ctx.task_id = task_id
        checkpoints = self.store.load_checkpoints(task_id)
        self._ctx.state = TaskState.PENDING
        if checkpoints.get("planning", {}).get("status") == "completed":
            self._ctx.state = TaskState.PLANNING
        if checkpoints.get("executing", {}).get("status") == "completed":
            self._ctx.state = TaskState.EXECUTING
        if checkpoints.get("reviewing", {}).get("status") == "completed":
            self._ctx.state = TaskState.REVIEWING
        try:
            self._trace_begin(state)
            result = self.runtime.execute(
                state,
                [
                    RuntimeNode("planning", self._planning),
                    RuntimeNode("executing", self._executing),
                    RuntimeNode("reviewing", self._reviewing),
                ],
                task_id=task_id, checkpoint_store=self.store,
                cancel_check=lambda: self.store.is_cancelled(task_id),
                span_factory=self._span,
            )
            report = self._report_from_dict(result["report"])
            self._ctx.step += 1
            self.store.succeed(
                task_id, report,
                TraceEvent(self._ctx.step, TaskState.SUCCESS, "Review completed", utc_now()),
            )
            self._trace_complete(task_id, "task_completed", report)
            self._capture_snapshot(task_id, report)
            return report
        except TaskCancelled as exc:
            self._ctx.step += 1
            self.store.cancel(
                task_id, TraceEvent(self._ctx.step, TaskState.CANCELLED, str(exc), utc_now())
            )
            raise
        except Exception as exc:
            self._ctx.step += 1
            self.store.fail(
                task_id, str(exc),
                TraceEvent(self._ctx.step, TaskState.FAILED, "Review failed: %s" % exc, utc_now()),
            )
            try:
                self.store.record_failure_case(
                    task_id, "execution_error", {"error": str(exc)[:1000]}
                )
            except Exception:
                pass
            self._trace_complete(task_id, "task_failed")
            raise

    def resume(
        self, task_id: str, repository: str, pull_request: Optional[int], diff: str,
        tenant_id: str = "default",
    ) -> ReviewReport:
        return self.run(task_id, repository, pull_request, diff, tenant_id)

    def _planning(self, state: RuntimeState) -> Dict[str, Any]:
        parsed = parse_unified_diff(state["diff"])
        if not parsed.files and not parsed.added_lines:
            raise ValueError("diff does not contain a valid unified diff with added lines")
        self._transition(TaskState.PLANNING, "Input accepted; preparing review plan")
        return {"parsed": self._serialize_parsed(parsed)}

    def _executing(self, state: RuntimeState) -> Dict[str, Any]:
        parsed = self._deserialize_parsed(state["parsed"])
        self._transition(
            TaskState.EXECUTING, "Reviewing %d changed files" % len(parsed.files)
        )
        contextual = getattr(self.reviewer, "review_with_context", None)
        findings = (
            contextual(
                state["task_id"], state["diff"], parsed,
                repository=state["repository"], tenant_id=state.get("tenant_id", "default"),
            )
            if contextual else self.reviewer.review(state["diff"], parsed)
        )
        return {"findings": [item.to_dict() for item in findings]}

    def _reviewing(self, state: RuntimeState) -> Dict[str, Any]:
        parsed = self._deserialize_parsed(state["parsed"])
        findings = [self._finding_from_dict(item) for item in state["findings"]]
        self._transition(
            TaskState.REVIEWING, "Validating and ranking %d findings" % len(findings)
        )
        risk = self._risk(findings)
        summary_reader = getattr(self.reviewer, "collaboration_summary", None)
        collaboration = summary_reader(state["task_id"]) if summary_reader else {}
        if not collaboration:
            collaboration = self._persisted_collaboration_summary(state["task_id"])
        # Work Package 6: consensus + historical FP rate adjust confidence.
        sources = collaboration.get("finding_sources") or {}
        total_agents = len(collaboration.get("agents", [])) or 1
        cases = self.store.list_failure_cases(
            False, 500, state.get("tenant_id", "default")
        )
        findings = apply_enhancement(
            findings, sources, total_agents, cases, self.confidence_enhance,
        )
        findings, clustering = cluster_findings(
            findings, self.finding_clustering,
        )
        report = ReviewReport(
            repository=state["repository"], pull_request=state.get("pull_request"),
            summary=self._summary(findings, len(parsed.files), risk), risk=risk,
            findings=findings, files_reviewed=parsed.files, reviewer=self.reviewer.name,
            collaboration=collaboration,
            classification=classify(findings, self.confidence_buckets),
            clustering=clustering,
        )
        return {"report": report.to_dict()}

    def _transition(self, target: TaskState, message: str) -> None:
        if target == self._ctx.state:
            return
        if target not in ALLOWED.get(self._ctx.state, set()):
            raise RuntimeError(
                "invalid state transition: %s -> %s" % (self._ctx.state.value, target.value)
            )
        self._ctx.step += 1
        self._ctx.state = target
        self.store.transition(
            self._ctx.task_id,
            TraceEvent(self._ctx.step, target, message, utc_now()),
        )

    def _trace_begin(self, state: RuntimeState) -> None:
        """Record the start of a real review into the decision trace (8.1)."""
        logger = self.trace_logger
        if logger is None:
            return
        task_id = state.get("task_id", "")
        logger.begin(task_id)
        logger.append(task_id, DecisionTraceEvent(
            uuid.uuid4().hex, "policy_resolution", agent_id="runtime",
            policy_id=(self.execution_policy.policy_id
                       if self.execution_policy else ""),
            data={"risk_level": (self.execution_context.risk_level
                                 if self.execution_context else "low")},
        ))
        logger.append(task_id, DecisionTraceEvent(
            uuid.uuid4().hex, "task_started", agent_id="runtime",
            data={"repository": state.get("repository", "")},
        ))

    def _trace_complete(self, task_id: str, action_type: str,
                        report: Optional[ReviewReport] = None) -> None:
        logger = self.trace_logger
        if logger is None:
            return
        data = {}
        if report is not None:
            data = {
                "risk": report.risk,
                "findings": len(report.findings),
                "reviewer": report.reviewer,
            }
        logger.append(task_id, DecisionTraceEvent(
            uuid.uuid4().hex, action_type, agent_id="runtime", data=data,
        ))

    def _capture_snapshot(self, task_id: str,
                          report: Optional[ReviewReport] = None) -> None:
        """Auto-generate and store a replay snapshot for a finished review (8.3)."""
        if self.replay_repository is None:
            return
        from .replay.builder import ReplaySnapshotBuilder
        builder = ReplaySnapshotBuilder(self.replay_repository)
        expected = report.to_dict() if report is not None else None
        snapshot = builder.build(
            execution_context=self.execution_context,
            decision_trace=self.trace_logger.trace(task_id)
            if self.trace_logger is not None else None,
            task_id=task_id, repository=self.execution_context.repository
            if self.execution_context else "",
            expected_output=expected,
        )
        self.replay_repository.save(snapshot)

    def _span(self, name: str, attributes: Dict[str, Any]):
        if self.observability:
            return self.observability.span(
                name, str(attributes.get("task_id", "")), **attributes
            )
        from contextlib import nullcontext
        return nullcontext()

    @staticmethod
    def _serialize_parsed(parsed: ParsedDiff) -> Dict[str, Any]:
        return {
            "files": parsed.files,
            "added_lines": [
                {"path": item.path, "line": item.line, "content": item.content}
                for item in parsed.added_lines
            ],
        }

    @staticmethod
    def _deserialize_parsed(value: Dict[str, Any]) -> ParsedDiff:
        return ParsedDiff(
            list(value["files"]), [ChangedLine(**item) for item in value["added_lines"]]
        )

    @staticmethod
    def _finding_from_dict(value: Dict[str, Any]) -> Finding:
        item = dict(value)
        item["severity"] = Severity(item["severity"])
        return Finding(**item)

    @classmethod
    def _report_from_dict(cls, value: Dict[str, Any]) -> ReviewReport:
        return ReviewReport(
            repository=value["repository"], pull_request=value.get("pull_request"),
            summary=value["summary"], risk=value["risk"],
            findings=[cls._finding_from_dict(item) for item in value.get("findings", [])],
            files_reviewed=list(value.get("files_reviewed", [])),
            reviewer=value.get("reviewer", "unknown"),
            collaboration=dict(value.get("collaboration", {})),
            classification=dict(value.get("classification", {})),
            clustering=dict(value.get("clustering", {})),
        )

    @staticmethod
    def _risk(findings) -> str:
        severities = {item.severity for item in findings}
        if Severity.CRITICAL in severities or Severity.HIGH in severities:
            return "high"
        if Severity.MEDIUM in severities:
            return "medium"
        return "low"

    @staticmethod
    def _summary(findings, file_count: int, risk: str) -> str:
        if not findings:
            return "Reviewed %d file(s); no actionable issue was detected in added lines." % file_count
        return "Reviewed %d file(s); found %d actionable issue(s). Overall risk: %s." % (
            file_count, len(findings), risk,
        )

    def _persisted_collaboration_summary(self, task_id: str) -> Dict[str, Any]:
        task = self.store.get(task_id) or {}
        messages = task.get("collaboration", [])
        if not messages:
            return {}
        kinds = [item.get("kind", "") for item in messages]
        roles = sorted({
            value for item in messages
            for value in (item.get("sender", ""), item.get("recipient", ""))
            if value and value not in {"all", "review-report"}
        })
        rounds = [
            int((item.get("content") or {}).get("round", 0))
            for item in messages
            if isinstance(item.get("content"), dict)
        ]
        final = next((
            item.get("content") or {} for item in reversed(messages)
            if item.get("kind") == "arbitration_decision"
        ), {})
        return {
            "protocol": "plan-challenge-revise-evidence-verify-arbitrate",
            "roles": roles,
            "planned_assignments": kinds.count("assignment"),
            "dialogue_rounds": max(rounds or [1]),
            "messages": len(messages),
            "retries": kinds.count("retry_request"),
            "handoffs": kinds.count("assignment_handoff"),
            "approved_findings": len(final.get("approved_findings", [])),
            "rejected_findings": len(final.get("rejected_findings", [])),
        }
