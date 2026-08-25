"""PostgreSQL persistence backend.

The implementation mirrors TaskStore's public API and is selected when
EVOAGENT_DATABASE_URL starts with postgres. psycopg is an optional production
dependency so local development can remain zero-config.
"""
import hashlib
import json
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Optional

from .models import ReviewReport, TaskState, TraceEvent
from .store import utc_now


class PostgresTaskStore:
    def __init__(self, url: str):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL mode requires: pip install psycopg[binary]") from exc
        self.psycopg = psycopg
        self.dict_row = dict_row
        self.url = url
        self._init()

    def _connect(self):
        return self.psycopg.connect(self.url, row_factory=self.dict_row)

    @contextmanager
    def _connection(self):
        # psycopg.Connection's context manager commits/rolls back but does not
        # close; this wrapper adds deterministic close in finally.  Existing
        # _connect() is preserved for callers that rely on its return type.
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ping(self) -> bool:
        """Work Package 10 readiness probe: cheapest possible DB round-trip."""
        try:
            with self._connection() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def _init(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, repository TEXT NOT NULL,
                pull_request INTEGER, input_json JSONB NOT NULL, report_json JSONB,
                error TEXT, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS trace_events (
                id BIGSERIAL PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), step INTEGER NOT NULL,
                state TEXT NOT NULL, message TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS failure_cases (
                id BIGSERIAL PRIMARY KEY, task_id TEXT NOT NULL, category TEXT NOT NULL,
                payload_json JSONB NOT NULL, resolved BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS skill_versions (
                id BIGSERIAL PRIMARY KEY, skill_name TEXT NOT NULL, version INTEGER NOT NULL,
                prompt TEXT NOT NULL, score DOUBLE PRECISION NOT NULL, active BOOLEAN NOT NULL DEFAULT FALSE,
                parent_version INTEGER, created_at TIMESTAMPTZ NOT NULL, UNIQUE(skill_name, version))""",
            """CREATE TABLE IF NOT EXISTS installations (
                installation_id BIGINT PRIMARY KEY, account_login TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS evaluation_cases (
                id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, split TEXT NOT NULL,
                diff TEXT NOT NULL, expected_json JSONB NOT NULL, source TEXT NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL)""",
            "ALTER TABLE evaluation_cases "
            "ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE evaluation_cases ADD COLUMN IF NOT EXISTS suite_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE evaluation_cases ADD COLUMN IF NOT EXISTS dataset_version TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE evaluation_cases ADD COLUMN IF NOT EXISTS repository TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE evaluation_cases ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE evaluation_cases ADD COLUMN IF NOT EXISTS source_uri TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE evaluation_cases ADD COLUMN IF NOT EXISTS labeler_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE evaluation_cases ADD COLUMN IF NOT EXISTS label_schema_version TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE evaluation_cases ADD COLUMN IF NOT EXISTS created_before_candidate BOOLEAN NOT NULL DEFAULT FALSE",
            """CREATE TABLE IF NOT EXISTS evolution_runs (
                id TEXT PRIMARY KEY, skill_name TEXT NOT NULL, candidate_version INTEGER NOT NULL,
                baseline_version INTEGER, decision TEXT NOT NULL, candidate_score DOUBLE PRECISION NOT NULL,
                baseline_score DOUBLE PRECISION NOT NULL, metrics_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS skill_artifact_versions (
                id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'default',
                skill_name TEXT NOT NULL, version INTEGER NOT NULL,
                artifact_json JSONB NOT NULL, artifact_sha256 TEXT NOT NULL,
                score DOUBLE PRECISION NOT NULL, active BOOLEAN NOT NULL DEFAULT FALSE,
                parent_version INTEGER, created_at TIMESTAMPTZ NOT NULL,
                status TEXT, origin TEXT, repository_scope TEXT,
                provenance_json JSONB, patch_json JSONB,
                updated_at TIMESTAMPTZ, activated_at TIMESTAMPTZ, archived_at TIMESTAMPTZ,
                UNIQUE(tenant_id, skill_name, version))""",
            """CREATE TABLE IF NOT EXISTS skill_evolution_runs (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'default',
                skill_name TEXT NOT NULL, candidate_version INTEGER NOT NULL,
                baseline_version INTEGER, decision TEXT NOT NULL,
                candidate_score DOUBLE PRECISION NOT NULL, baseline_score DOUBLE PRECISION NOT NULL,
                metrics_json JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL)""",
            "ALTER TABLE skill_artifact_versions ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'",
            "ALTER TABLE skill_evolution_runs ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'",
            "ALTER TABLE skill_artifact_versions ADD COLUMN IF NOT EXISTS status TEXT",
            "ALTER TABLE skill_artifact_versions ADD COLUMN IF NOT EXISTS origin TEXT",
            "ALTER TABLE skill_artifact_versions ADD COLUMN IF NOT EXISTS repository_scope TEXT",
            "ALTER TABLE skill_artifact_versions ADD COLUMN IF NOT EXISTS provenance_json JSONB",
            "ALTER TABLE skill_artifact_versions ADD COLUMN IF NOT EXISTS patch_json JSONB",
            "ALTER TABLE skill_artifact_versions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
            "ALTER TABLE skill_artifact_versions ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ",
            "ALTER TABLE skill_artifact_versions ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'",
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE installations ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'",
            """CREATE TABLE IF NOT EXISTS checkpoints (
                task_id TEXT NOT NULL REFERENCES tasks(id), node TEXT NOT NULL, status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1, state_json JSONB NOT NULL, error TEXT,
                updated_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(task_id,node))""",
            """CREATE TABLE IF NOT EXISTS task_payloads (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id), diff TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS agent_messages (
                id BIGSERIAL PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                sender TEXT NOT NULL, recipient TEXT NOT NULL, kind TEXT NOT NULL,
                correlation_id TEXT NOT NULL, content_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS webhook_deliveries (
                delivery_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, event_type TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL, task_id TEXT, received_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS memberships (
                user_id TEXT NOT NULL REFERENCES users(id), tenant_id TEXT NOT NULL, role TEXT NOT NULL,
                PRIMARY KEY(user_id,tenant_id))""",
            """CREATE TABLE IF NOT EXISTS repository_grants (
                tenant_id TEXT NOT NULL, repository TEXT NOT NULL, auto_fix BOOLEAN NOT NULL DEFAULT FALSE,
                PRIMARY KEY(tenant_id,repository))""",
            """CREATE TABLE IF NOT EXISTS audit_log (
                id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, actor TEXT NOT NULL,
                action TEXT NOT NULL, resource TEXT NOT NULL, detail_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS deployments (
                tenant_id TEXT NOT NULL, skill_name TEXT NOT NULL, stable_version INTEGER,
                candidate_version INTEGER, canary_percent INTEGER NOT NULL DEFAULT 0,
                shadow_percent INTEGER NOT NULL DEFAULT 0, max_error_rate DOUBLE PRECISION NOT NULL DEFAULT .1,
                min_samples INTEGER NOT NULL DEFAULT 20, status TEXT NOT NULL DEFAULT 'stable',
                samples INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(tenant_id,skill_name))""",
            # Work Package 4 parity: append-only columns on deployments and the
            # shadow observation table mirror the SQLite backend (idempotent).
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS "
            "max_disagreement_rate DOUBLE PRECISION NOT NULL DEFAULT 0.2",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS "
            "auto_promote BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS "
            "shadow_samples INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS "
            "disagreements INTEGER NOT NULL DEFAULT 0",
            # Closed-loop WP6: staged canary and atomic rollback fields.
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS artifact_kind TEXT",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS job_id TEXT",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS approval_policy TEXT",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS quality_budget_json JSONB",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS stage_started_at TIMESTAMPTZ",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS stage_deadline_at TIMESTAMPTZ",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS last_gate_result_json JSONB",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS rollback_version INTEGER",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS rollback_reason TEXT",
            "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS paused_by TEXT",
            """CREATE TABLE IF NOT EXISTS release_observations (
                id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, skill_name TEXT NOT NULL,
                task_id TEXT NOT NULL, lane TEXT NOT NULL, primary_json JSONB NOT NULL,
                candidate_json JSONB, disagreement DOUBLE PRECISION NOT NULL,
                candidate_failed BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS alerts (
                id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, alert_key TEXT NOT NULL,
                severity TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE(tenant_id,alert_key,status))""",
            """CREATE TABLE IF NOT EXISTS agent_memories (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, repository TEXT NOT NULL,
                task_id TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
                keywords_json JSONB NOT NULL, metadata_json JSONB NOT NULL,
                importance DOUBLE PRECISION NOT NULL DEFAULT .5,
                created_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ)""",
            """CREATE INDEX IF NOT EXISTS idx_agent_memories_lookup
                ON agent_memories(tenant_id,repository,scope,created_at)""",
            """CREATE TABLE IF NOT EXISTS experiences (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, repository TEXT,
                task_id TEXT NOT NULL, source_type TEXT NOT NULL, category TEXT NOT NULL,
                experience_type TEXT NOT NULL, fingerprint TEXT NOT NULL,
                payload_json JSONB NOT NULL, evidence TEXT, confidence DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL, rejection_reason TEXT, candidate_run_id TEXT,
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE(tenant_id, fingerprint, task_id))""",
            "CREATE INDEX IF NOT EXISTS idx_experiences_lookup "
            "ON experiences(tenant_id, repository, experience_type, status)",
            "CREATE INDEX IF NOT EXISTS idx_experiences_fingerprint "
            "ON experiences(tenant_id, experience_type, fingerprint)",
            "ALTER TABLE experiences ADD COLUMN IF NOT EXISTS "
            "scope TEXT NOT NULL DEFAULT 'repository-local'",
            # Work Package 4: usage metrics that only accumulate for evolved
            # skills whose findings carry an explicit source_skill attribution.
            """CREATE TABLE IF NOT EXISTS skill_usage_stats (
                tenant_id TEXT NOT NULL, skill_name TEXT NOT NULL, version INTEGER NOT NULL,
                executions INTEGER NOT NULL DEFAULT 0,
                findings_proposed INTEGER NOT NULL DEFAULT 0,
                findings_approved INTEGER NOT NULL DEFAULT 0,
                false_positive_feedback INTEGER NOT NULL DEFAULT 0,
                last_used_at TIMESTAMPTZ,
                PRIMARY KEY(tenant_id, skill_name, version))""",
            # Work Package 1: report-chat data model (mirrors the SQLite backend).
            """CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, task_id TEXT NOT NULL,
                repository TEXT NOT NULL, title TEXT NOT NULL, created_by TEXT NOT NULL,
                status TEXT NOT NULL, report_fingerprint TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_task "
            "ON chat_sessions(tenant_id, task_id, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_creator "
            "ON chat_sessions(tenant_id, created_by, updated_at)",
            """CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, session_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL, citations_json JSONB NOT NULL,
                provider TEXT, model TEXT, prompt_version TEXT,
                input_tokens INTEGER, output_tokens INTEGER,
                status TEXT NOT NULL, error TEXT, client_request_id TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(session_id, client_request_id))""",
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_session "
            "ON chat_messages(session_id, created_at)",
            """CREATE TABLE IF NOT EXISTS chat_context_snapshots (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, session_id TEXT NOT NULL,
                message_id TEXT NOT NULL, report_fingerprint TEXT NOT NULL,
                context_fingerprint TEXT NOT NULL, references_json JSONB NOT NULL,
                truncation_json JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_chat_snapshots_message "
            "ON chat_context_snapshots(message_id)",
            """CREATE TABLE IF NOT EXISTS chat_insights (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, session_id TEXT NOT NULL,
                source_message_id TEXT NOT NULL, task_id TEXT NOT NULL,
                category TEXT NOT NULL, finding_json JSONB NOT NULL, note TEXT NOT NULL,
                confidence DOUBLE PRECISION NOT NULL, validation_json JSONB NOT NULL,
                status TEXT NOT NULL, confirmed_by TEXT, feedback_case_id BIGINT,
                supersedes_id TEXT, created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_chat_insights_session "
            "ON chat_insights(session_id, status)",
            "ALTER TABLE failure_cases ADD COLUMN IF NOT EXISTS source_key TEXT",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_failure_cases_source_key "
            "ON failure_cases(task_id, source_key) WHERE source_key IS NOT NULL",
            # Closed-loop WP1: durable evolution jobs with lease + checkpoint.
            """CREATE TABLE IF NOT EXISTS evolution_jobs (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, repository_scope TEXT,
                capability_kind TEXT NOT NULL, capability_name TEXT NOT NULL,
                trigger_type TEXT NOT NULL, trigger_ref TEXT,
                idempotency_key TEXT NOT NULL, status TEXT NOT NULL,
                current_step TEXT NOT NULL, candidate_version BIGINT,
                evolution_run_id TEXT, lease_owner TEXT, lease_until TIMESTAMPTZ,
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                budget_json JSONB NOT NULL, checkpoint_json JSONB NOT NULL,
                error TEXT, created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
                UNIQUE(tenant_id, idempotency_key))""",
            "CREATE INDEX IF NOT EXISTS idx_evolution_jobs_status "
            "ON evolution_jobs(tenant_id, status, updated_at)",
            # Closed-loop WP2: structured Reflection Hypotheses.
            """CREATE TABLE IF NOT EXISTS evolution_hypotheses (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
                repository_scope TEXT, problem_type TEXT NOT NULL,
                failure_signature TEXT NOT NULL, root_cause TEXT NOT NULL,
                change_type TEXT NOT NULL, expected_effect_json JSONB NOT NULL,
                affected_domains_json JSONB NOT NULL, risk_level TEXT NOT NULL,
                permissions_json JSONB NOT NULL, evaluation_requirements_json JSONB NOT NULL,
                rationale TEXT NOT NULL, evidence_ids_json JSONB NOT NULL,
                provenance_json JSONB NOT NULL, status TEXT NOT NULL,
                reviewed_by TEXT, created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_evolution_hypotheses_lookup "
            "ON evolution_hypotheses(tenant_id, status, updated_at)",
            # Closed-loop WP5: independent per-gate results for shadow/canary.
            """CREATE TABLE IF NOT EXISTS evolution_gate_results (
                id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, job_id TEXT NOT NULL,
                candidate_kind TEXT NOT NULL, candidate_name TEXT NOT NULL,
                candidate_version INTEGER NOT NULL, stage TEXT NOT NULL,
                gate_name TEXT NOT NULL, baseline_value DOUBLE PRECISION,
                candidate_value DOUBLE PRECISION, threshold_json JSONB NOT NULL,
                passed BOOLEAN NOT NULL, evidence_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_evolution_gate_results_lookup "
            "ON evolution_gate_results(tenant_id, candidate_name, stage, created_at)",
            # Closed-loop WP5: shadow observation quality/cost/latency fields.
            "ALTER TABLE release_observations ADD COLUMN IF NOT EXISTS stable_version INTEGER",
            "ALTER TABLE release_observations ADD COLUMN IF NOT EXISTS candidate_version INTEGER",
            "ALTER TABLE release_observations ADD COLUMN IF NOT EXISTS metrics_json JSONB",
            "ALTER TABLE release_observations ADD COLUMN IF NOT EXISTS latency_ms DOUBLE PRECISION",
            "ALTER TABLE release_observations ADD COLUMN IF NOT EXISTS cost_estimate DOUBLE PRECISION",
            "ALTER TABLE release_observations ADD COLUMN IF NOT EXISTS human_label TEXT",
            "ALTER TABLE release_observations ADD COLUMN IF NOT EXISTS feedback_category TEXT",
            "ALTER TABLE release_observations ADD COLUMN IF NOT EXISTS accepted BOOLEAN",
            "ALTER TABLE release_observations ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMPTZ",
            # Work Package 2: idempotent backfill of append-only lifecycle columns.
            "UPDATE skill_artifact_versions "
            "SET status = CASE WHEN active = TRUE THEN 'active' ELSE 'validated' END "
            "WHERE status IS NULL",
            "UPDATE skill_artifact_versions SET origin = 'agent-created' WHERE origin IS NULL",
            "UPDATE skill_artifact_versions SET provenance_json = '{}'::jsonb "
            "WHERE provenance_json IS NULL",
            "UPDATE skill_artifact_versions SET updated_at = created_at WHERE updated_at IS NULL",
        ]
        with self._connection() as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)

    def create(
        self, task_id: str, repository: str, pull_request: Optional[int],
        payload: Dict[str, Any], tenant_id: str = "default",
    ) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tasks(id,state,repository,pull_request,input_json,report_json,error,"
                "created_at,updated_at,tenant_id,cancel_requested) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,NULL,NULL,%s,%s,%s,FALSE)",
                (task_id, TaskState.PENDING.value, repository, pull_request,
                 json.dumps(payload), now, now, tenant_id),
            )

    def transition(self, task_id: str, event: TraceEvent) -> None:
        with self._connection() as conn:
            conn.execute("UPDATE tasks SET state=%s,updated_at=%s WHERE id=%s", (event.state.value, event.created_at, task_id))
            conn.execute(
                "INSERT INTO trace_events(task_id,step,state,message,created_at) VALUES (%s,%s,%s,%s,%s)",
                (task_id, event.step, event.state.value, event.message, event.created_at),
            )

    def succeed(self, task_id: str, report: ReviewReport, event: TraceEvent) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE tasks SET state=%s,report_json=%s::jsonb,updated_at=%s WHERE id=%s",
                (TaskState.SUCCESS.value, json.dumps(report.to_dict(), ensure_ascii=False), event.created_at, task_id),
            )
            conn.execute(
                "INSERT INTO trace_events(task_id,step,state,message,created_at) VALUES (%s,%s,%s,%s,%s)",
                (task_id, event.step, event.state.value, event.message, event.created_at),
            )

    def fail(self, task_id: str, error: str, event: TraceEvent) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE tasks SET state=%s,error=%s,updated_at=%s WHERE id=%s",
                (TaskState.FAILED.value, error[:2000], event.created_at, task_id),
            )
            conn.execute(
                "INSERT INTO trace_events(task_id,step,state,message,created_at) VALUES (%s,%s,%s,%s,%s)",
                (task_id, event.step, event.state.value, event.message, event.created_at),
            )

    def get(self, task_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            query = "SELECT * FROM tasks WHERE id=%s"
            params = [task_id]
            if tenant_id is not None:
                query += " AND tenant_id=%s"
                params.append(tenant_id)
            row = conn.execute(query, params).fetchone()
            if not row:
                return None
            events = conn.execute(
                "SELECT step,state,message,created_at FROM trace_events WHERE task_id=%s ORDER BY id", (task_id,)
            ).fetchall()
            messages = conn.execute(
                "SELECT sender,recipient,kind,correlation_id,content_json,created_at "
                "FROM agent_messages WHERE task_id=%s ORDER BY id", (task_id,)
            ).fetchall()
        value = dict(row)
        value["input"] = value.pop("input_json")
        value["report"] = value.pop("report_json")
        value["trace"] = [dict(item) for item in events]
        value["collaboration"] = []
        for message in messages:
            item = dict(message)
            item["content"] = item.pop("content_json")
            item["created_at"] = item["created_at"].isoformat()
            value["collaboration"].append(item)
        for key in ("created_at", "updated_at"):
            value[key] = value[key].isoformat()
        for item in value["trace"]:
            item["created_at"] = item["created_at"].isoformat()
        return value

    def record_agent_message(self, task_id: str, message: Dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO agent_messages(task_id,sender,recipient,kind,correlation_id,"
                "content_json,created_at) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)",
                (task_id, message["sender"], message["recipient"], message["kind"],
                 message.get("correlation_id", ""),
                 json.dumps(message.get("content", {}), ensure_ascii=False), utc_now()),
            )

    def save_agent_memory(self, memory: Dict[str, Any]) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "INSERT INTO agent_memories(id,tenant_id,repository,task_id,agent,scope,kind,"
                "content,keywords_json,metadata_json,importance,created_at,expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET "
                "importance=GREATEST(agent_memories.importance,EXCLUDED.importance),"
                "expires_at=EXCLUDED.expires_at RETURNING *",
                (
                    memory["id"], memory["tenant_id"], memory["repository"],
                    memory.get("task_id", ""), memory.get("agent", ""), memory["scope"],
                    memory["kind"], memory["content"],
                    json.dumps(memory.get("keywords", []), ensure_ascii=False),
                    json.dumps(memory.get("metadata", {}), ensure_ascii=False),
                    float(memory.get("importance", 0.5)), memory["created_at"],
                    memory.get("expires_at"),
                ),
            ).fetchone()
        return self._memory_from_row(row)

    def list_agent_memories(
        self, tenant_id: str, repository: str, scopes: tuple,
        limit: int = 100,
    ) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_memories WHERE tenant_id=%s AND repository=%s "
                "AND scope=ANY(%s) AND (expires_at IS NULL OR expires_at>%s) "
                "ORDER BY importance DESC,created_at DESC LIMIT %s",
                (
                    tenant_id, repository, list(scopes), utc_now(),
                    max(1, min(limit, 500)),
                ),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def delete_agent_memories(self, task_id: str = "", scope: str = "") -> int:
        clauses = []
        params = []
        if task_id:
            clauses.append("task_id=%s")
            params.append(task_id)
        if scope:
            clauses.append("scope=%s")
            params.append(scope)
        if not clauses:
            raise ValueError("memory deletion requires task_id or scope")
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM agent_memories WHERE " + " AND ".join(clauses), params
            )
            return cursor.rowcount

    def purge_expired_agent_memories(self) -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM agent_memories WHERE expires_at IS NOT NULL AND expires_at<=%s",
                (utc_now(),),
            )
            return cursor.rowcount

    @staticmethod
    def _memory_from_row(row) -> Dict[str, Any]:
        value = dict(row)
        value["keywords"] = value.pop("keywords_json")
        value["metadata"] = value.pop("metadata_json")
        for key in ("created_at", "expires_at"):
            if value.get(key) is not None:
                value[key] = value[key].isoformat()
        return value

    def list_tasks(self, limit: int = 50, tenant_id: Optional[str] = None) -> list:
        with self._connection() as conn:
            where = " WHERE tenant_id=%s" if tenant_id is not None else ""
            params = ([tenant_id] if tenant_id is not None else []) + [max(1, min(limit, 200))]
            rows = conn.execute(
                "SELECT id,state,repository,pull_request,error,created_at,updated_at,tenant_id "
                "FROM tasks" + where + " ORDER BY created_at DESC LIMIT %s", params
            ).fetchall()
        values = [dict(row) for row in rows]
        for value in values:
            value["created_at"] = value["created_at"].isoformat()
            value["updated_at"] = value["updated_at"].isoformat()
        return values

    def record_failure_case(
        self, task_id: str, category: str, payload: Dict[str, Any],
        source_key: Optional[str] = None,
    ) -> int:
        """Persist a failure case, returning its ID (idempotent on source_key)."""
        with self._connection() as conn:
            if source_key:
                row = conn.execute(
                    "SELECT id FROM failure_cases WHERE task_id=%s AND source_key=%s",
                    (task_id, source_key),
                ).fetchone()
                if row is not None:
                    return int(row["id"])
            row = conn.execute(
                "INSERT INTO failure_cases(task_id,category,payload_json,created_at,source_key) "
                "VALUES (%s,%s,%s::jsonb,%s,%s) RETURNING id",
                (task_id, category, json.dumps(payload, ensure_ascii=False), utc_now(), source_key),
            ).fetchone()
            return int(row["id"])

    # ---- Work Package 1: report-chat store methods (SQLite parity) ----
    def _chat_value(self, row) -> dict:
        value = dict(row)
        for key in ("created_at", "updated_at"):
            if value.get(key) is not None and hasattr(value[key], "isoformat"):
                value[key] = value[key].isoformat()
        return value

    def create_chat_session(
        self, tenant_id: str, task_id: str, repository: str, title: str,
        created_by: str, report_fingerprint: str,
    ) -> dict:
        session_id = str(uuid.uuid4())
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO chat_sessions(id, tenant_id, task_id, repository, title, created_by, "
                "status, report_fingerprint, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s,%s)",
                (session_id, tenant_id, task_id, repository, title, created_by,
                 report_fingerprint, now, now),
            )
        return self.get_chat_session(session_id, tenant_id)

    def get_chat_session(self, session_id: str, tenant_id: str) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE id=%s AND tenant_id=%s",
                (session_id, tenant_id),
            ).fetchone()
        return self._chat_value(row) if row else {}

    def list_task_chat_sessions(
        self, task_id: str, tenant_id: str, limit: int = 50,
    ) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_sessions WHERE task_id=%s AND tenant_id=%s "
                "ORDER BY updated_at DESC LIMIT %s",
                (task_id, tenant_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._chat_value(row) for row in rows]

    def update_chat_session_status(
        self, session_id: str, tenant_id: str, status: str,
    ) -> dict:
        with self._connection() as conn:
            conn.execute(
                "UPDATE chat_sessions SET status=%s, updated_at=%s WHERE id=%s AND tenant_id=%s",
                (status, utc_now(), session_id, tenant_id),
            )
        return self.get_chat_session(session_id, tenant_id)

    def append_chat_message(
        self, tenant_id: str, session_id: str, role: str, content: str,
        citations: list, client_request_id: Optional[str] = None,
        provider: Optional[str] = None, model: Optional[str] = None,
        prompt_version: Optional[str] = None, input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None, status: str = "pending",
        error: Optional[str] = None,
    ) -> dict:
        message_id = str(uuid.uuid4())
        now = utc_now()
        with self._connection() as conn:
            if client_request_id:
                row = conn.execute(
                    "SELECT * FROM chat_messages WHERE session_id=%s AND client_request_id=%s",
                    (session_id, client_request_id),
                ).fetchone()
                if row is not None:
                    return self._chat_value(row)
            conn.execute(
                "INSERT INTO chat_messages(id, tenant_id, session_id, role, content, citations_json, "
                "provider, model, prompt_version, input_tokens, output_tokens, status, error, "
                "client_request_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (message_id, tenant_id, session_id, role, content,
                 json.dumps(citations, ensure_ascii=False), provider, model, prompt_version,
                 input_tokens, output_tokens, status, error, client_request_id, now),
            )
        return self.get_chat_message(message_id, tenant_id)

    def get_chat_message(self, message_id: str, tenant_id: str) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM chat_messages WHERE id=%s AND tenant_id=%s",
                (message_id, tenant_id),
            ).fetchone()
        if row is None:
            return {}
        value = self._chat_value(row)
        value["citations"] = value.pop("citations_json")
        return value

    def list_chat_messages(
        self, session_id: str, tenant_id: str, limit: int = 100,
    ) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id=%s AND tenant_id=%s "
                "ORDER BY created_at ASC, id ASC LIMIT %s",
                (session_id, tenant_id, max(1, min(limit, 500))),
            ).fetchall()
        values = []
        for row in rows:
            value = self._chat_value(row)
            value["citations"] = value.pop("citations_json")
            values.append(value)
        return values

    def complete_chat_message(
        self, message_id: str, tenant_id: str, content: str, citations: list,
        provider: Optional[str] = None, model: Optional[str] = None,
        prompt_version: Optional[str] = None, input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> dict:
        with self._connection() as conn:
            conn.execute(
                "UPDATE chat_messages SET status='completed', error=NULL, content=%s, citations_json=%s::jsonb, "
                "provider=%s, model=%s, prompt_version=%s, input_tokens=%s, output_tokens=%s "
                "WHERE id=%s AND tenant_id=%s",
                (content, json.dumps(citations, ensure_ascii=False), provider, model, prompt_version,
                 input_tokens, output_tokens, message_id, tenant_id),
            )
        return self.get_chat_message(message_id, tenant_id)

    def fail_chat_message(self, message_id: str, tenant_id: str, error: str) -> dict:
        with self._connection() as conn:
            conn.execute(
                "UPDATE chat_messages SET status='failed', error=%s WHERE id=%s AND tenant_id=%s",
                (error, message_id, tenant_id),
            )
        return self.get_chat_message(message_id, tenant_id)

    def save_chat_context_snapshot(
        self, tenant_id: str, session_id: str, message_id: str,
        report_fingerprint: str, context_fingerprint: str,
        references: list, truncation: dict,
    ) -> dict:
        snapshot_id = str(uuid.uuid4())
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO chat_context_snapshots(id, tenant_id, session_id, message_id, "
                "report_fingerprint, context_fingerprint, references_json, truncation_json, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)",
                (snapshot_id, tenant_id, session_id, message_id, report_fingerprint,
                 context_fingerprint, json.dumps(references, ensure_ascii=False),
                 json.dumps(truncation, ensure_ascii=False), now),
            )
        return self.get_chat_context_snapshot(message_id, tenant_id)

    def get_chat_context_snapshot(self, message_id: str, tenant_id: str) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM chat_context_snapshots WHERE message_id=%s AND tenant_id=%s",
                (message_id, tenant_id),
            ).fetchone()
        if row is None:
            return {}
        value = self._chat_value(row)
        value["references"] = value.pop("references_json")
        value["truncation"] = value.pop("truncation_json")
        return value

    def create_chat_insight(
        self, tenant_id: str, session_id: str, source_message_id: str, task_id: str,
        category: str, finding: dict, note: str, confidence: float,
        validation: dict, status: str = "draft",
    ) -> dict:
        insight_id = str(uuid.uuid4())
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO chat_insights(id, tenant_id, session_id, source_message_id, task_id, "
                "category, finding_json, note, confidence, validation_json, status, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s)",
                (insight_id, tenant_id, session_id, source_message_id, task_id, category,
                 json.dumps(finding, ensure_ascii=False), note, float(confidence),
                 json.dumps(validation, ensure_ascii=False), status, now, now),
            )
        return self.get_chat_insight(insight_id, tenant_id)

    def get_chat_insight(self, insight_id: str, tenant_id: str) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM chat_insights WHERE id=%s AND tenant_id=%s",
                (insight_id, tenant_id),
            ).fetchone()
        if row is None:
            return {}
        value = self._chat_value(row)
        value["finding"] = value.pop("finding_json")
        value["validation"] = value.pop("validation_json")
        return value

    def list_chat_insights(self, session_id: str, tenant_id: str) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_insights WHERE session_id=%s AND tenant_id=%s "
                "ORDER BY created_at ASC",
                (session_id, tenant_id),
            ).fetchall()
        values = []
        for row in rows:
            value = self._chat_value(row)
            value["finding"] = value.pop("finding_json")
            value["validation"] = value.pop("validation_json")
            values.append(value)
        return values

    def update_chat_insight_status(
        self, insight_id: str, tenant_id: str, status: str,
        confirmed_by: Optional[str] = None, feedback_case_id: Optional[int] = None,
    ) -> dict:
        with self._connection() as conn:
            conn.execute(
                "UPDATE chat_insights SET status=%s, confirmed_by=%s, feedback_case_id=%s, updated_at=%s "
                "WHERE id=%s AND tenant_id=%s",
                (status, confirmed_by, feedback_case_id, utc_now(), insight_id, tenant_id),
            )
        return self.get_chat_insight(insight_id, tenant_id)

    # ---- Work Package 5: candidate confirmation and controlled sedimentation ----
    def update_chat_insight(
        self, insight_id: str, tenant_id: str, category: str,
        finding: dict, note: str, validation: dict,
    ) -> dict:
        """Edit a draft insight's category / finding / note and re-record validation."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE chat_insights SET category=%s, finding_json=%s::jsonb, note=%s, "
                "validation_json=%s::jsonb, updated_at=%s WHERE id=%s AND tenant_id=%s",
                (category, json.dumps(finding, ensure_ascii=False), note[:2000],
                 json.dumps(validation, ensure_ascii=False), utc_now(), insight_id, tenant_id),
            )
        return self.get_chat_insight(insight_id, tenant_id)

    def claim_chat_insight(
        self, insight_id: str, tenant_id: str, confirmed_by: str,
    ) -> bool:
        """Atomically claim a draft insight for confirmation (draft -> confirming)."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE chat_insights SET status='confirming', confirmed_by=%s, updated_at=%s "
                "WHERE id=%s AND tenant_id=%s AND status='draft'",
                (confirmed_by, utc_now(), insight_id, tenant_id),
            )
            return cursor.rowcount > 0

    def list_chat_insights_by_status(self, status: str, limit: int = 100) -> list:
        """Scan insights by status across tenants (used for crash reconciliation)."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_insights WHERE status=%s ORDER BY updated_at ASC LIMIT %s",
                (status, max(1, min(limit, 500))),
            ).fetchall()
        values = []
        for row in rows:
            value = self._chat_value(row)
            value["finding"] = value.pop("finding_json")
            value["validation"] = value.pop("validation_json")
            values.append(value)
        return values

    def get_failure_case_by_source_key(
        self, task_id: str, source_key: str,
    ) -> Optional[int]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM failure_cases WHERE task_id=%s AND source_key=%s",
                (task_id, source_key),
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def list_chat_messages_by_status(self, status: str, limit: int = 100) -> list:
        """Scan messages by status across tenants (used for crash recovery)."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE status=%s ORDER BY created_at ASC LIMIT %s",
                (status, max(1, min(limit, 500))),
            ).fetchall()
        values = []
        for row in rows:
            value = self._chat_value(row)
            value["citations"] = value.pop("citations_json")
            values.append(value)
        return values

    def purge_chat_history(
        self, cutoff_iso: str, tenant_id: Optional[str] = None,
    ) -> int:
        """Delete old chat message bodies that never formed confirmed feedback."""
        params: list = [cutoff_iso]
        tenant_clause = ""
        if tenant_id is not None:
            tenant_clause = " AND tenant_id=%s"
            params.append(tenant_id)
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM chat_messages WHERE created_at < %s"
                + tenant_clause
                + " AND session_id NOT IN "
                "(SELECT session_id FROM chat_insights WHERE status='confirmed')",
                params,
            )
            return cursor.rowcount

    def list_failure_cases(
        self, unresolved_only: bool = False, limit: int = 100,
        tenant_id: Optional[str] = None,
    ) -> list:
        joins = " f"
        clauses = []
        params = []
        if tenant_id is not None:
            joins += " JOIN tasks t ON t.id=f.task_id"
            clauses.append("t.tenant_id=%s")
            params.append(tenant_id)
        if unresolved_only:
            clauses.append("f.resolved=FALSE")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT f.* FROM failure_cases" + joins + where
                + " ORDER BY f.id DESC LIMIT %s", params
            ).fetchall()
        values = [dict(row) for row in rows]
        for value in values:
            value["payload"] = value.pop("payload_json")
            value["created_at"] = value["created_at"].isoformat()
        return values

    def list_task_failure_cases(
        self, task_id: str, tenant_id: Optional[str] = None,
    ) -> list:
        joins = " f"
        clauses = ["f.task_id=%s"]
        params = [task_id]
        if tenant_id is not None:
            joins += " JOIN tasks t ON t.id=f.task_id"
            clauses.append("t.tenant_id=%s")
            params.append(tenant_id)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT f.* FROM failure_cases" + joins
                + " WHERE " + " AND ".join(clauses)
                + " ORDER BY f.id DESC",
                params,
            ).fetchall()
        values = [dict(row) for row in rows]
        for value in values:
            value["payload"] = value.pop("payload_json")
            value["created_at"] = value["created_at"].isoformat()
        return values

    def resolve_failure_cases(self, case_ids: list) -> None:
        ids = [int(value) for value in case_ids]
        if not ids:
            return
        with self._connection() as conn:
            conn.execute("UPDATE failure_cases SET resolved=TRUE WHERE id=ANY(%s)", (ids,))

    # ---- Closed-loop WP1: durable evolution jobs --------------------------
    def _decode_evolution_job(self, row) -> dict:
        value = self._chat_value(row)
        value["budget"] = value.pop("budget_json")
        value["checkpoint"] = value.pop("checkpoint_json")
        return value

    def create_evolution_job(
        self, job_id: str, tenant_id: str, repository_scope, capability_kind: str,
        capability_name: str, trigger_type: str, trigger_ref, idempotency_key: str,
        budget: Dict[str, Any], max_retries: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """Insert a job; returns None when the idempotency key already exists."""
        now = utc_now()
        with self._connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO evolution_jobs("
                    "id,tenant_id,repository_scope,capability_kind,capability_name,"
                    "trigger_type,trigger_ref,idempotency_key,status,current_step,"
                    "candidate_version,evolution_run_id,lease_owner,lease_until,"
                    "retry_count,max_retries,budget_json,checkpoint_json,error,"
                    "created_at,updated_at,finished_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending','collecting',NULL,NULL,"
                    "NULL,NULL,0,%s,%s::jsonb,%s::jsonb,NULL,%s,%s,NULL)",
                    (job_id, tenant_id, repository_scope, capability_kind, capability_name,
                     trigger_type, trigger_ref, idempotency_key, max_retries,
                     json.dumps(budget, ensure_ascii=False),
                     json.dumps({"step": "collecting"}, ensure_ascii=False),
                     now, now),
                )
            except Exception:
                # UNIQUE(tenant_id, idempotency_key) violation.
                conn.rollback()
                return None
        return self.get_evolution_job(job_id, tenant_id)

    def get_evolution_job(self, job_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_jobs WHERE id=%s AND tenant_id=%s",
                (job_id, tenant_id),
            ).fetchone()
        return self._decode_evolution_job(row) if row is not None else None

    def find_active_evolution_job(
        self, tenant_id: str, idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_jobs WHERE tenant_id=%s AND idempotency_key=%s "
                "AND status IN ('pending','running','paused') ORDER BY created_at DESC LIMIT 1",
                (tenant_id, idempotency_key),
            ).fetchone()
        return self._decode_evolution_job(row) if row is not None else None

    def list_evolution_jobs(self, tenant_id: str, limit: int = 50) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM evolution_jobs WHERE tenant_id=%s "
                "ORDER BY created_at DESC LIMIT %s",
                (tenant_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._decode_evolution_job(row) for row in rows]

    def update_evolution_job(
        self, job_id: str, tenant_id: str, **fields,
    ) -> Optional[Dict[str, Any]]:
        allowed = {
            "status", "current_step", "candidate_version", "evolution_run_id",
            "lease_owner", "lease_until", "retry_count", "error", "finished_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_evolution_job(job_id, tenant_id)
        updates["updated_at"] = utc_now()
        assignments = ", ".join("%s=%%s" % key for key in updates)
        params = list(updates.values()) + [job_id, tenant_id]
        with self._connection() as conn:
            conn.execute(
                "UPDATE evolution_jobs SET %s WHERE id=%%s AND tenant_id=%%s"
                % assignments,
                params,
            )
        return self.get_evolution_job(job_id, tenant_id)

    def update_evolution_job_checkpoint(
        self, job_id: str, tenant_id: str, step: str, checkpoint: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            conn.execute(
                "UPDATE evolution_jobs SET current_step=%s, checkpoint_json=%s::jsonb, updated_at=%s "
                "WHERE id=%s AND tenant_id=%s",
                (step, json.dumps(checkpoint, ensure_ascii=False), utc_now(), job_id, tenant_id),
            )
        return self.get_evolution_job(job_id, tenant_id)

    def acquire_evolution_job_lease(
        self, job_id: str, tenant_id: str, owner: str, lease_until: str,
    ) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE evolution_jobs SET status='running', current_step='collecting', "
                "lease_owner=%s, lease_until=%s, error=NULL, updated_at=%s "
                "WHERE id=%s AND tenant_id=%s AND status='pending'",
                (owner, lease_until, utc_now(), job_id, tenant_id),
            )
            return cursor.rowcount > 0

    def release_evolution_job_lease(
        self, job_id: str, tenant_id: str, owner: str,
    ) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE evolution_jobs SET lease_owner=NULL, lease_until=NULL, updated_at=%s "
                "WHERE id=%s AND tenant_id=%s AND lease_owner=%s",
                (utc_now(), job_id, tenant_id, owner),
            )
            return cursor.rowcount > 0

    def list_expired_evolution_jobs(self, now: str, limit: int = 100) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM evolution_jobs WHERE status='running' "
                "AND lease_until IS NOT NULL AND lease_until < %s "
                "ORDER BY updated_at ASC LIMIT %s",
                (now, max(1, min(limit, 500))),
            ).fetchall()
        return [self._decode_evolution_job(row) for row in rows]

    # ---- Closed-loop WP2: structured Reflection Hypotheses ----------------
    def _decode_hypothesis(self, row) -> dict:
        value = dict(row)
        value["expected_effect"] = value.pop("expected_effect_json")
        value["affected_domains"] = value.pop("affected_domains_json")
        value["permissions"] = value.pop("permissions_json")
        value["evaluation_requirements"] = value.pop("evaluation_requirements_json")
        value["evidence_ids"] = value.pop("evidence_ids_json")
        value["provenance"] = value.pop("provenance_json")
        for key in ("created_at", "updated_at"):
            if value.get(key) is not None and hasattr(value[key], "isoformat"):
                value[key] = value[key].isoformat()
        return value

    def create_hypothesis(self, hypothesis: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO evolution_hypotheses("
                "id,job_id,tenant_id,repository_scope,problem_type,failure_signature,"
                "root_cause,change_type,expected_effect_json,affected_domains_json,"
                "risk_level,permissions_json,evaluation_requirements_json,rationale,"
                "evidence_ids_json,provenance_json,status,reviewed_by,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s::jsonb,"
                "%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)",
                (hypothesis["id"], hypothesis.get("job_id", ""), hypothesis["tenant_id"],
                 hypothesis.get("repository_scope"), hypothesis["problem_type"],
                 hypothesis["failure_signature"], hypothesis["root_cause"],
                 hypothesis["change_type"],
                 json.dumps(hypothesis.get("expected_effect", {}), ensure_ascii=False),
                 json.dumps(hypothesis.get("affected_domains", []), ensure_ascii=False),
                 hypothesis["risk_level"],
                 json.dumps(hypothesis.get("permissions", []), ensure_ascii=False),
                 json.dumps(hypothesis.get("evaluation_requirements", {}), ensure_ascii=False),
                 hypothesis["rationale"],
                 json.dumps(hypothesis.get("evidence_ids", []), ensure_ascii=False),
                 json.dumps(hypothesis.get("provenance", {}), ensure_ascii=False),
                 hypothesis["status"], hypothesis.get("reviewed_by"), now, now),
            )
        return self.get_hypothesis(hypothesis["id"], hypothesis["tenant_id"])

    def get_hypothesis(self, hypothesis_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_hypotheses WHERE id=%s AND tenant_id=%s",
                (hypothesis_id, tenant_id),
            ).fetchone()
        return self._decode_hypothesis(row) if row is not None else None

    def list_hypotheses(
        self, tenant_id: str, status: Optional[str] = None, limit: int = 100,
    ) -> list:
        query = "SELECT * FROM evolution_hypotheses WHERE tenant_id=%s"
        params = [tenant_id]
        if status is not None:
            query += " AND status=%s"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(max(1, min(limit, 500)))
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_hypothesis(row) for row in rows]

    def update_hypothesis(
        self, hypothesis_id: str, tenant_id: str, **fields,
    ) -> Optional[Dict[str, Any]]:
        allowed = {"status", "reviewed_by"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_hypothesis(hypothesis_id, tenant_id)
        updates["updated_at"] = utc_now()
        assignments = ", ".join("%s=%%s" % key for key in updates)
        params = list(updates.values()) + [hypothesis_id, tenant_id]
        with self._connection() as conn:
            conn.execute(
                "UPDATE evolution_hypotheses SET %s WHERE id=%%s AND tenant_id=%%s"
                % assignments,
                params,
            )
        return self.get_hypothesis(hypothesis_id, tenant_id)

    # ---- Closed-loop WP5: shadow gate results -----------------------------
    def _decode_gate_result(self, row) -> dict:
        value = dict(row)
        value["threshold"] = value.pop("threshold_json")
        value["evidence"] = value.pop("evidence_json")
        value["passed"] = bool(value["passed"])
        for key in ("created_at",):
            if value.get(key) is not None and hasattr(value[key], "isoformat"):
                value[key] = value[key].isoformat()
        return value

    def save_gate_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now()
        with self._connection() as conn:
            row = conn.execute(
                "INSERT INTO evolution_gate_results("
                "tenant_id,job_id,candidate_kind,candidate_name,candidate_version,"
                "stage,gate_name,baseline_value,candidate_value,threshold_json,"
                "passed,evidence_json,created_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s) RETURNING *",
                (result["tenant_id"], result.get("job_id", ""), result["candidate_kind"],
                 result["candidate_name"], int(result["candidate_version"]),
                 result.get("stage", ""), result["gate_name"],
                 result.get("baseline_value"), result.get("candidate_value"),
                 json.dumps(result.get("threshold", {}), ensure_ascii=False),
                 bool(result.get("passed", False)),
                 json.dumps(result.get("evidence", {}), ensure_ascii=False), now),
            ).fetchone()
        return self._decode_gate_result(row)

    def list_gate_results(
        self, tenant_id: str, stage: Optional[str] = None,
        candidate_name: Optional[str] = None, limit: int = 100,
    ) -> list:
        query = "SELECT * FROM evolution_gate_results WHERE tenant_id=%s"
        params = [tenant_id]
        if stage is not None:
            query += " AND stage=%s"
            params.append(stage)
        if candidate_name is not None:
            query += " AND candidate_name=%s"
            params.append(candidate_name)
        query += " ORDER BY id DESC LIMIT %s"
        params.append(max(1, min(limit, 500)))
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_gate_result(row) for row in rows]

    def backfill_release_observation(
        self, tenant_id: str, skill_name: str, task_id: str,
        human_label: str, feedback_category: str, accepted: bool,
    ) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE release_observations SET human_label=%s, feedback_category=%s, "
                "accepted=%s, evaluated_at=%s WHERE tenant_id=%s AND skill_name=%s "
                "AND task_id=%s AND id=(SELECT MAX(id) FROM release_observations "
                "WHERE tenant_id=%s AND skill_name=%s AND task_id=%s)",
                (human_label[:200], feedback_category, accepted, utc_now(),
                 tenant_id, skill_name, task_id, tenant_id, skill_name, task_id),
            )
            return cursor.rowcount > 0

    def list_distinct_experience_tenants(self) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT tenant_id FROM experiences WHERE tenant_id IS NOT NULL"
            ).fetchall()
        return [row["tenant_id"] for row in rows]

    def record_experience(
        self, tenant_id: str, repository: Optional[str], task_id: str,
        source_type: str, category: str, experience_type: str, fingerprint: str,
        payload: Dict[str, Any], evidence: Optional[str], confidence: float,
        status: str, rejection_reason: Optional[str] = None,
        scope: str = "repository-local",
    ) -> Dict[str, Any]:
        now = utc_now()
        experience_id = uuid.uuid4().hex
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO experiences(id,tenant_id,repository,task_id,source_type,category,"
                "experience_type,fingerprint,payload_json,evidence,confidence,status,rejection_reason,"
                "scope,created_at,updated_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(tenant_id, fingerprint, task_id) DO NOTHING",
                (experience_id, tenant_id, repository, task_id, source_type, category,
                 experience_type, fingerprint, json.dumps(payload, ensure_ascii=False),
                 evidence, float(confidence), status, rejection_reason, scope, now, now),
            )
        return {
            "id": experience_id, "tenant_id": tenant_id, "repository": repository,
            "task_id": task_id, "experience_type": experience_type,
            "fingerprint": fingerprint, "status": status, "scope": scope,
            "inserted": cursor.rowcount > 0,
        }

    def promote_experience_scope(
        self, tenant_id: str, fingerprint: str, scope: str,
    ) -> int:
        if scope not in {"repository-local", "tenant-shared", "global-builtin"}:
            raise ValueError("invalid experience scope: %s" % scope)
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE experiences SET scope=%s, updated_at=%s "
                "WHERE tenant_id=%s AND fingerprint=%s",
                (scope, utc_now(), tenant_id, fingerprint),
            )
            return cursor.rowcount

    @staticmethod
    def _decode_experience(row) -> Dict[str, Any]:
        value = dict(row)
        value["payload"] = value.pop("payload_json")
        for key in ("created_at", "updated_at"):
            if hasattr(value.get(key), "isoformat"):
                value[key] = value[key].isoformat()
        return value

    def list_experiences(
        self, tenant_id: str, experience_type: Optional[str] = None,
        status: Optional[str] = None, limit: int = 100,
    ) -> list:
        query = "SELECT * FROM experiences WHERE tenant_id=%s"
        params = [tenant_id]
        if experience_type is not None:
            query += " AND experience_type=%s"
            params.append(experience_type)
        if status is not None:
            query += " AND status=%s"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(max(1, min(limit, 500)))
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_experience(row) for row in rows]

    def list_observed_experiences_by_fingerprint(
        self, tenant_id: str, fingerprint: str,
    ) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM experiences WHERE tenant_id=%s AND fingerprint=%s AND status='observed' "
                "ORDER BY created_at",
                (tenant_id, fingerprint),
            ).fetchall()
        return [self._decode_experience(row) for row in rows]

    def corroborate_experiences(
        self, tenant_id: str, fingerprint: str,
    ) -> Dict[str, Any]:
        now = utc_now()
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id FROM experiences WHERE tenant_id=%s AND fingerprint=%s AND status='observed'",
                (tenant_id, fingerprint),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                conn.execute(
                    "UPDATE experiences SET status='corroborated', updated_at=%s WHERE id=ANY(%s)",
                    (now, ids),
                )
        return {"fingerprint": fingerprint, "count": len(ids)}

    def list_corroborated_rule_candidates(
        self, tenant_id: str, limit: int = 100,
    ) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM experiences WHERE tenant_id=%s AND experience_type='rule_candidate' "
                "AND status='corroborated' ORDER BY created_at LIMIT %s",
                (tenant_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._decode_experience(row) for row in rows]

    def list_experiences_by_ids(self, experience_ids: list) -> list:
        if not experience_ids:
            return []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM experiences WHERE id=ANY(%s)", (list(experience_ids),),
            ).fetchall()
        return [self._decode_experience(row) for row in rows]

    def find_failure_case_ids_for_experiences(self, experiences: list) -> list:
        ids = []
        for exp in experiences:
            finding = (exp.get("payload") or {}).get("finding") or {}
            signature = (
                str(finding.get("rule_id", "")).strip().upper(),
                str(finding.get("path", "")),
                str(finding.get("line", "")),
            )
            for case in self.list_task_failure_cases(exp["task_id"], exp["tenant_id"]):
                if case.get("category") != "missed_issue":
                    continue
                case_finding = (case.get("payload") or {}).get("finding") or {}
                case_signature = (
                    str(case_finding.get("rule_id", "")).strip().upper(),
                    str(case_finding.get("path", "")),
                    str(case_finding.get("line", "")),
                )
                if case_signature == signature and case["id"] not in ids:
                    ids.append(case["id"])
        return ids

    def mark_experience_consumed(
        self, experience_ids: list, candidate_run_id: Optional[str] = None,
    ) -> None:
        ids = list(experience_ids)
        if not ids:
            return
        with self._connection() as conn:
            conn.execute(
                "UPDATE experiences SET status='consumed', candidate_run_id=%s, updated_at=%s "
                "WHERE id=ANY(%s)",
                (candidate_run_id, utc_now(), ids),
            )

    def mark_experience_run(
        self, experience_ids: list, candidate_run_id: Optional[str] = None,
    ) -> None:
        ids = list(experience_ids)
        if not ids:
            return
        with self._connection() as conn:
            conn.execute(
                "UPDATE experiences SET candidate_run_id=%s, updated_at=%s WHERE id=ANY(%s)",
                (candidate_run_id, utc_now(), ids),
            )

    @staticmethod
    def _decode_usage_stats(row) -> Dict[str, Any]:
        value = dict(row)
        value["executions"] = int(value["executions"])
        value["findings_proposed"] = int(value["findings_proposed"])
        value["findings_approved"] = int(value["findings_approved"])
        value["false_positive_feedback"] = int(value["false_positive_feedback"])
        return value

    def record_skill_usage(
        self, tenant_id: str, skill_name: str, version: int,
        executions: int = 0, findings_proposed: int = 0,
        findings_approved: int = 0, false_positive_feedback: int = 0,
        last_used_at: Optional[str] = None,
    ) -> None:
        """Accumulate usage counters for an evolved skill@version.

        Counters only move in one direction; the caller is responsible for
        deciding whether attribution is reliable (explicit source_skill).
        """
        if version is None:
            return
        now = last_used_at or utc_now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO skill_usage_stats(tenant_id,skill_name,version,executions,"
                "findings_proposed,findings_approved,false_positive_feedback,last_used_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(tenant_id, skill_name, version) DO UPDATE SET "
                "executions = skill_usage_stats.executions + EXCLUDED.executions, "
                "findings_proposed = skill_usage_stats.findings_proposed + EXCLUDED.findings_proposed, "
                "findings_approved = skill_usage_stats.findings_approved + EXCLUDED.findings_approved, "
                "false_positive_feedback = skill_usage_stats.false_positive_feedback + EXCLUDED.false_positive_feedback, "
                "last_used_at = EXCLUDED.last_used_at",
                (tenant_id, skill_name, int(version), int(executions), int(findings_proposed),
                 int(findings_approved), int(false_positive_feedback), now),
            )

    def get_skill_usage_stats(
        self, tenant_id: str, skill_name: str, version: int,
    ) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM skill_usage_stats WHERE tenant_id=%s AND skill_name=%s AND version=%s",
                (tenant_id, skill_name, int(version)),
            ).fetchone()
        return self._decode_usage_stats(row) if row else None

    def list_skill_usage_stats(
        self, tenant_id: str, limit: int = 200,
    ) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM skill_usage_stats WHERE tenant_id=%s ORDER BY skill_name, version "
                "LIMIT %s", (tenant_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._decode_usage_stats(row) for row in rows]

    def save_evaluation_case(
        self, name: str, split: str, diff: str, expected: list,
        source: str = "manual", active: bool = True, category: str = "",
        suite_id: str = "", dataset_version: str = "", repository: str = "",
        language: str = "", source_uri: str = "", labeler_ids: Optional[list] = None,
        label_schema_version: str = "", created_before_candidate: bool = False,
    ) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "INSERT INTO evaluation_cases(name,split,diff,expected_json,source,active,"
                "category,suite_id,dataset_version,repository,language,source_uri,"
                "labeler_ids_json,label_schema_version,created_before_candidate,created_at) "
                "VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) "
                "ON CONFLICT(name) DO NOTHING RETURNING *",
                (name, split, diff, json.dumps(expected, ensure_ascii=False), source, active,
                 category, suite_id, dataset_version, repository, language, source_uri,
                 json.dumps(list(labeler_ids or []), ensure_ascii=False),
                 label_schema_version, created_before_candidate, utc_now()),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM evaluation_cases WHERE name=%s", (name,)
                ).fetchone()
                if (
                    row["split"] != split
                    or row["diff"] != diff
                    or row["expected_json"] != expected
                ):
                    raise ValueError(
                        "evaluation case names are immutable; use a new name for revised content"
                    )
        value = dict(row)
        value["expected"] = value.pop("expected_json")
        value["created_at"] = value["created_at"].isoformat()
        value["category"] = value.get("category", "") or ""
        value["suite_id"] = value.get("suite_id", "") or ""
        value["dataset_version"] = value.get("dataset_version", "") or ""
        value["repository"] = value.get("repository", "") or ""
        value["language"] = value.get("language", "") or ""
        value["source_uri"] = value.get("source_uri", "") or ""
        value["labeler_ids"] = value.pop("labeler_ids_json", []) or []
        value["label_schema_version"] = value.get("label_schema_version", "") or ""
        value["created_before_candidate"] = bool(value.get("created_before_candidate", False))
        return value

    def list_evaluation_cases(
        self, split: Optional[str] = None, active_only: bool = True, limit: int = 100,
        source: Optional[str] = None,
    ) -> list:
        clauses = []
        params = []
        if split:
            clauses.append("split=%s")
            params.append(split)
        if active_only:
            clauses.append("active=TRUE")
        if source and source != "all":
            if source == "builtin":
                clauses.append("(source IS NULL OR source != 'github-real')")
            else:
                clauses.append("source=%s")
                params.append(source)
        query = "SELECT * FROM evaluation_cases"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id LIMIT %s"
        params.append(max(1, min(limit, 500)))
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        values = [dict(row) for row in rows]
        for value in values:
            value["expected"] = value.pop("expected_json")
            value["created_at"] = value["created_at"].isoformat()
            value["category"] = value.get("category", "") or ""
            value["suite_id"] = value.get("suite_id", "") or ""
            value["dataset_version"] = value.get("dataset_version", "") or ""
            value["repository"] = value.get("repository", "") or ""
            value["language"] = value.get("language", "") or ""
            value["source_uri"] = value.get("source_uri", "") or ""
            value["labeler_ids"] = value.pop("labeler_ids_json", []) or []
            value["label_schema_version"] = value.get("label_schema_version", "") or ""
            value["created_before_candidate"] = bool(value.get("created_before_candidate", False))
        return values

    def archive_oldest_holdout_cases(self, limit: int = 1) -> list:
        """Mirror of TaskStore.archive_oldest_holdout_cases (see its docstring)."""
        if limit < 1:
            return []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id FROM evaluation_cases WHERE split='holdout' AND active=TRUE "
                "ORDER BY id LIMIT %s", (limit,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("%s" for _ in ids)
                conn.execute(
                    "UPDATE evaluation_cases SET active=FALSE WHERE id IN (%s)"
                    % placeholders, tuple(ids),
                )
        return ids

    def save_evolution_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO evolution_runs(id,skill_name,candidate_version,baseline_version,decision,"
                "candidate_score,baseline_score,metrics_json,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                (
                    run["id"], run["skill_name"], run["candidate_version"], run.get("baseline_version"),
                    run["decision"], run["candidate_score"], run["baseline_score"],
                    json.dumps(run["metrics"], ensure_ascii=False), run["created_at"],
                ),
            )
        return run

    def list_evolution_runs(self, limit: int = 50) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM evolution_runs ORDER BY created_at DESC LIMIT %s",
                (max(1, min(limit, 200)),),
            ).fetchall()
        values = [dict(row) for row in rows]
        for value in values:
            value["metrics"] = value.pop("metrics_json")
            value["created_at"] = value["created_at"].isoformat()
        return values

    def get_active_skill_version(self, skill_name: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM skill_versions WHERE skill_name=%s AND active=TRUE ORDER BY version DESC LIMIT 1",
                (skill_name,),
            ).fetchone()
        return dict(row) if row else None

    def save_skill_version(self, skill_name: str, prompt: str, score: float, activate: bool = False) -> Dict[str, Any]:
        active = self.get_active_skill_version(skill_name)
        with self._connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (skill_name,))
            row = conn.execute(
                "SELECT COALESCE(MAX(version),0) AS version FROM skill_versions WHERE skill_name=%s",
                (skill_name,),
            ).fetchone()
            version = int(row["version"]) + 1
            if activate:
                conn.execute("UPDATE skill_versions SET active=FALSE WHERE skill_name=%s", (skill_name,))
            conn.execute(
                "INSERT INTO skill_versions(skill_name,version,prompt,score,active,parent_version,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (skill_name, version, prompt, score, activate, active["version"] if active else None, utc_now()),
            )
        return {"skill_name": skill_name, "version": version, "score": score, "active": activate}

    def list_skill_versions(self, skill_name: str) -> list:
        with self._connection() as conn:
            return list(conn.execute("SELECT * FROM skill_versions WHERE skill_name=%s ORDER BY version DESC", (skill_name,)).fetchall())

    def activate_skill_version(self, skill_name: str, version: int) -> bool:
        with self._connection() as conn:
            exists = conn.execute("SELECT 1 FROM skill_versions WHERE skill_name=%s AND version=%s", (skill_name, version)).fetchone()
            if not exists:
                return False
            conn.execute("UPDATE skill_versions SET active=FALSE WHERE skill_name=%s", (skill_name,))
            conn.execute("UPDATE skill_versions SET active=TRUE WHERE skill_name=%s AND version=%s", (skill_name, version))
        return True

    def save_skill_artifact(
        self, skill_name: str, artifact: Dict[str, Any], score: float,
        activate: bool = False, tenant_id: str = "default",
        status: Optional[str] = None, origin: Optional[str] = None,
        repository_scope: Optional[str] = None, provenance: Optional[Dict[str, Any]] = None,
        patch: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from . import skill_lifecycle
        artifact_json = json.dumps(
            artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        artifact_sha256 = hashlib.sha256(artifact_json.encode("utf-8")).hexdigest()
        if status is None:
            status = skill_lifecycle.default_status(activate)
        if not skill_lifecycle.is_valid(status):
            raise ValueError("invalid skill artifact status: %s" % status)
        origin = origin or "agent-created"
        provenance_json = provenance or {}
        patch_json = patch
        now = utc_now()
        active = self.get_active_skill_artifact(skill_name, tenant_id)
        with self._connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("artifact:" + skill_name,))
            row = conn.execute(
                "SELECT COALESCE(MAX(version),0) AS version FROM skill_artifact_versions "
                "WHERE tenant_id=%s AND skill_name=%s", (tenant_id, skill_name),
            ).fetchone()
            version = int(row["version"]) + 1
            if activate:
                conn.execute(
                    "UPDATE skill_artifact_versions SET active=FALSE, status=%s, updated_at=%s "
                    "WHERE tenant_id=%s AND skill_name=%s AND active=TRUE",
                    (skill_lifecycle.VALIDATED, now, tenant_id, skill_name),
                )
            conn.execute(
                "INSERT INTO skill_artifact_versions(tenant_id,skill_name,version,artifact_json,"
                "artifact_sha256,score,active,parent_version,created_at,status,origin,"
                "repository_scope,provenance_json,patch_json,updated_at,activated_at,archived_at) "
                "VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,NULL)",
                (tenant_id, skill_name, version, artifact_json, artifact_sha256, float(score), activate,
                 active["version"] if active else None, now, status, origin, repository_scope,
                 json.dumps(provenance_json, ensure_ascii=False, sort_keys=True),
                 json.dumps(patch_json, ensure_ascii=False, sort_keys=True) if patch_json is not None else None,
                 now, now if activate else None),
            )
        return {
            "tenant_id": tenant_id, "skill_name": skill_name, "version": version, "score": float(score),
            "active": activate, "parent_version": active["version"] if active else None,
            "artifact_sha256": artifact_sha256, "created_at": now,
            "status": status, "origin": origin, "repository_scope": repository_scope,
            "provenance": provenance or {}, "patch": patch,
            "updated_at": now, "activated_at": now if activate else None, "archived_at": None,
        }

    @staticmethod
    def _decode_skill_artifact(row) -> Dict[str, Any]:
        value = dict(row)
        value["artifact"] = value.pop("artifact_json")
        value["active"] = bool(value["active"])
        value["status"] = value.pop("status", None)
        value["origin"] = value.pop("origin", None)
        value["repository_scope"] = value.pop("repository_scope", None)
        value["provenance"] = value.pop("provenance_json", None) or {}
        value["patch"] = value.pop("patch_json", None)
        for key in ("updated_at", "activated_at", "archived_at"):
            if hasattr(value.get(key), "isoformat"):
                value[key] = value[key].isoformat()
        if hasattr(value.get("created_at"), "isoformat"):
            value["created_at"] = value["created_at"].isoformat()
        return value

    def get_active_skill_artifact(
        self, skill_name: str, tenant_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM skill_artifact_versions WHERE tenant_id=%s AND skill_name=%s "
                "AND active=TRUE ORDER BY version DESC LIMIT 1", (tenant_id, skill_name),
            ).fetchone()
        return self._decode_skill_artifact(row) if row else None

    def list_active_skill_artifacts(self, tenant_id: str = "default") -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM skill_artifact_versions WHERE tenant_id=%s AND active=TRUE "
                "ORDER BY skill_name", (tenant_id,)
            ).fetchall()
        return [self._decode_skill_artifact(row) for row in rows]

    def list_skill_artifact_versions(
        self, skill_name: str, tenant_id: str = "default",
    ) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM skill_artifact_versions WHERE tenant_id=%s AND skill_name=%s "
                "ORDER BY version DESC", (tenant_id, skill_name),
            ).fetchall()
        return [self._decode_skill_artifact(row) for row in rows]

    def list_skill_artifact_versions_for_tenant(
        self, tenant_id: str = "default", limit: int = 500,
    ) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM skill_artifact_versions WHERE tenant_id=%s "
                "ORDER BY skill_name, version DESC LIMIT %s",
                (tenant_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [self._decode_skill_artifact(row) for row in rows]

    def activate_skill_artifact(
        self, skill_name: str, version: int, tenant_id: str = "default",
        actor: str = "api", reason: str = "activate version",
    ) -> bool:
        return self.transition_skill_artifact(
            tenant_id, skill_name, version, "active", actor, reason,
        )

    def transition_skill_artifact(
        self, tenant_id: str, skill_name: str, version: int, target_status: str,
        actor: str = "api", reason: str = "",
    ) -> bool:
        from . import skill_lifecycle
        if not skill_lifecycle.is_valid(target_status):
            raise ValueError("invalid target status: %s" % target_status)
        now = utc_now()
        with self._connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("artifact:" + skill_name,))
            row = conn.execute(
                "SELECT * FROM skill_artifact_versions "
                "WHERE tenant_id=%s AND skill_name=%s AND version=%s",
                (tenant_id, skill_name, version),
            ).fetchone()
            if not row:
                return False
            current = row["status"]
            if skill_lifecycle.enabled():
                if target_status == "active":
                    if row["active"]:
                        return True
                    has_activated_run = conn.execute(
                        "SELECT 1 FROM skill_evolution_runs r "
                        "WHERE r.tenant_id=%s AND r.skill_name=%s AND r.candidate_version=%s "
                        "AND r.decision='activated'",
                        (tenant_id, skill_name, version),
                    ).fetchone()
                    if not (
                        skill_lifecycle.is_activatable(current)
                        or current == skill_lifecycle.CANARY
                        or has_activated_run
                    ):
                        return False
                    conn.execute(
                        "UPDATE skill_artifact_versions SET active=FALSE, status=%s, updated_at=%s "
                        "WHERE tenant_id=%s AND skill_name=%s AND active=TRUE",
                        (skill_lifecycle.VALIDATED, now, tenant_id, skill_name),
                    )
                    conn.execute(
                        "UPDATE skill_artifact_versions SET active=TRUE, status=%s, updated_at=%s, "
                        "activated_at=%s WHERE tenant_id=%s AND skill_name=%s AND version=%s",
                        (skill_lifecycle.ACTIVE, now, now, tenant_id, skill_name, version),
                    )
                else:
                    if not skill_lifecycle.can_transition(current, target_status):
                        return False
                    if target_status == skill_lifecycle.ARCHIVED and row["active"]:
                        return False
                    archived_at = now if target_status == skill_lifecycle.ARCHIVED else None
                    conn.execute(
                        "UPDATE skill_artifact_versions SET status=%s, updated_at=%s, archived_at=%s "
                        "WHERE tenant_id=%s AND skill_name=%s AND version=%s",
                        (target_status, now, archived_at, tenant_id, skill_name, version),
                    )
            else:
                # Dark switch off: legacy activation/rollback path without status gating.
                if target_status != "active":
                    return False
                if row["active"]:
                    return True
                has_activated_run = conn.execute(
                    "SELECT 1 FROM skill_evolution_runs r "
                    "WHERE r.tenant_id=%s AND r.skill_name=%s AND r.candidate_version=%s "
                    "AND r.decision='activated'",
                    (tenant_id, skill_name, version),
                ).fetchone()
                if not has_activated_run:
                    return False
                conn.execute(
                    "UPDATE skill_artifact_versions SET active=FALSE WHERE tenant_id=%s AND skill_name=%s",
                    (tenant_id, skill_name),
                )
                conn.execute(
                    "UPDATE skill_artifact_versions SET active=TRUE, status=%s, updated_at=%s "
                    "WHERE tenant_id=%s AND skill_name=%s AND version=%s",
                    (skill_lifecycle.ACTIVE, now, tenant_id, skill_name, version),
                )
            conn.execute(
                "INSERT INTO audit_log(tenant_id,actor,action,resource,detail_json,created_at) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                (tenant_id, actor, "skill.artifact.transition",
                 "%s@%s-%s" % (skill_name, version, target_status),
                 json.dumps({"from": current, "to": target_status, "reason": reason},
                            ensure_ascii=False), now),
            )
        return True

    def check_skill_artifact_consistency(self) -> list:
        issues = []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT tenant_id, skill_name, version, active, status "
                "FROM skill_artifact_versions"
            ).fetchall()
            for row in rows:
                active = bool(row["active"])
                status = row["status"]
                if active and status != "active":
                    issues.append({
                        "type": "active_mismatch", "tenant_id": row["tenant_id"],
                        "skill_name": row["skill_name"], "version": row["version"],
                        "detail": "active=TRUE but status=%r" % status,
                    })
                if not active and status == "active":
                    issues.append({
                        "type": "inactive_as_active", "tenant_id": row["tenant_id"],
                        "skill_name": row["skill_name"], "version": row["version"],
                        "detail": "active=FALSE but status='active'",
                    })
            dup = conn.execute(
                "SELECT tenant_id, skill_name, COUNT(*) AS n FROM skill_artifact_versions "
                "WHERE active=TRUE GROUP BY tenant_id, skill_name HAVING COUNT(*) > 1"
            ).fetchall()
        for row in dup:
            issues.append({
                "type": "multiple_active", "tenant_id": row["tenant_id"],
                "skill_name": row["skill_name"], "version": row["n"],
                "detail": "%d active=TRUE versions" % row["n"],
            })
        return issues

    def save_skill_evolution_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO skill_evolution_runs(id,tenant_id,skill_name,candidate_version,baseline_version,"
                "decision,candidate_score,baseline_score,metrics_json,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                (run["id"], run.get("tenant_id", "default"), run["skill_name"], run["candidate_version"],
                 run.get("baseline_version"), run["decision"], run["candidate_score"],
                 run["baseline_score"], json.dumps(run["metrics"], ensure_ascii=False),
                 run["created_at"]),
            )
        return run

    def list_skill_evolution_runs(
        self, limit: int = 50, tenant_id: Optional[str] = None,
    ) -> list:
        with self._connection() as conn:
            if tenant_id is None:
                rows = conn.execute(
                    "SELECT * FROM skill_evolution_runs ORDER BY created_at DESC LIMIT %s",
                    (max(1, min(limit, 200)),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM skill_evolution_runs WHERE tenant_id=%s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (tenant_id, max(1, min(limit, 200))),
                ).fetchall()
        values = [dict(row) for row in rows]
        for value in values:
            value["metrics"] = value.pop("metrics_json")
            value["created_at"] = value["created_at"].isoformat()
        return values

    def save_task_payload(self, task_id: str, diff: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO task_payloads(task_id,diff,created_at) VALUES (%s,%s,%s) "
                "ON CONFLICT(task_id) DO UPDATE SET diff=EXCLUDED.diff,created_at=EXCLUDED.created_at",
                (task_id, diff, utc_now()),
            )

    def update_task_input(self, task_id: str, updates: Dict[str, Any]) -> None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT input_json FROM tasks WHERE id=%s", (task_id,)
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            value = dict(row["input_json"])
            value.update(updates)
            conn.execute(
                "UPDATE tasks SET input_json=%s::jsonb,updated_at=%s WHERE id=%s",
                (json.dumps(value, ensure_ascii=False), utc_now(), task_id),
            )

    def get_task_payload(self, task_id: str) -> Optional[str]:
        with self._connection() as conn:
            row = conn.execute("SELECT diff FROM task_payloads WHERE task_id=%s", (task_id,)).fetchone()
        return row["diff"] if row else None

    def save_checkpoint(
        self, task_id: str, node: str, state: Dict[str, Any], status: str = "completed",
        attempt: int = 1, error: str = "",
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO checkpoints(task_id,node,status,attempt,state_json,error,updated_at) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT(task_id,node) DO UPDATE SET "
                "status=EXCLUDED.status,attempt=EXCLUDED.attempt,state_json=EXCLUDED.state_json,"
                "error=EXCLUDED.error,updated_at=EXCLUDED.updated_at",
                (task_id, node, status, attempt, json.dumps(state, ensure_ascii=False),
                 error[:2000] or None, utc_now()),
            )

    def load_checkpoints(self, task_id: str) -> Dict[str, Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT node,status,attempt,state_json,error,updated_at FROM checkpoints "
                "WHERE task_id=%s ORDER BY updated_at", (task_id,)
            ).fetchall()
        result = {}
        for row in rows:
            item = dict(row)
            item["state"] = item.pop("state_json")
            item["updated_at"] = item["updated_at"].isoformat()
            result[item.pop("node")] = item
        return result

    def request_cancel(self, task_id: str, tenant_id: Optional[str] = None) -> bool:
        query = "UPDATE tasks SET cancel_requested=TRUE,updated_at=%s WHERE id=%s"
        params = [utc_now(), task_id]
        if tenant_id is not None:
            query += " AND tenant_id=%s"
            params.append(tenant_id)
        with self._connection() as conn:
            cursor = conn.execute(query, params)
            return cursor.rowcount > 0

    def is_cancelled(self, task_id: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM tasks WHERE id=%s", (task_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def cancel(self, task_id: str, event: TraceEvent) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE tasks SET state=%s,updated_at=%s WHERE id=%s",
                (TaskState.CANCELLED.value, event.created_at, task_id),
            )
            conn.execute(
                "INSERT INTO trace_events(task_id,step,state,message,created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (task_id, event.step, event.state.value, event.message, event.created_at),
            )

    def claim_webhook(
        self, delivery_id: str, tenant_id: str, event_type: str, payload_sha256: str,
    ) -> bool:
        if not delivery_id:
            raise ValueError("X-GitHub-Delivery is required")
        with self._connection() as conn:
            row = conn.execute(
                "INSERT INTO webhook_deliveries"
                "(delivery_id,tenant_id,event_type,payload_sha256,received_at) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(delivery_id) DO NOTHING RETURNING delivery_id",
                (delivery_id, tenant_id, event_type, payload_sha256, utc_now()),
            ).fetchone()
            if row:
                return True
            existing = conn.execute(
                "SELECT payload_sha256 FROM webhook_deliveries WHERE delivery_id=%s",
                (delivery_id,),
            ).fetchone()
            if existing and existing["payload_sha256"] != payload_sha256:
                raise ValueError("delivery id was already used with a different payload")
            return False

    def complete_webhook(self, delivery_id: str, task_id: Optional[str]) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE webhook_deliveries SET task_id=%s WHERE delivery_id=%s",
                (task_id, delivery_id),
            )

    def get_webhook(self, delivery_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM webhook_deliveries WHERE delivery_id=%s", (delivery_id,)
            ).fetchone()
        return dict(row) if row else None

    def create_user(
        self, user_id: str, username: str, password_hash: str,
        tenant_id: str, role: str,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO users(id,username,password_hash,created_at) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT(username) DO NOTHING",
                (user_id, username, password_hash, utc_now()),
            )
            row = conn.execute("SELECT id FROM users WHERE username=%s", (username,)).fetchone()
            conn.execute(
                "INSERT INTO memberships(user_id,tenant_id,role) VALUES (%s,%s,%s) "
                "ON CONFLICT(user_id,tenant_id) DO UPDATE SET role=EXCLUDED.role",
                (row["id"], tenant_id, role),
            )

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id,username,password_hash,active FROM users WHERE username=%s",
                (username,),
            ).fetchone()
            if not row:
                return None
            memberships = conn.execute(
                "SELECT tenant_id,role FROM memberships WHERE user_id=%s", (row["id"],)
            ).fetchall()
        value = dict(row)
        value["memberships"] = [dict(item) for item in memberships]
        return value

    def grant_repository(self, tenant_id: str, repository: str, auto_fix: bool = False) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO repository_grants(tenant_id,repository,auto_fix) VALUES (%s,%s,%s) "
                "ON CONFLICT(tenant_id,repository) DO UPDATE SET auto_fix=EXCLUDED.auto_fix",
                (tenant_id, repository, auto_fix),
            )

    def repository_allowed(
        self, tenant_id: str, repository: str, require_auto_fix: bool = False,
    ) -> bool:
        with self._connection() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM repository_grants WHERE tenant_id=%s", (tenant_id,)
            ).fetchone()["n"]
            row = conn.execute(
                "SELECT auto_fix FROM repository_grants WHERE tenant_id=%s AND repository=%s",
                (tenant_id, repository),
            ).fetchone()
        return True if total == 0 else bool(row and (not require_auto_fix or row["auto_fix"]))

    def audit(
        self, tenant_id: str, actor: str, action: str, resource: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO audit_log(tenant_id,actor,action,resource,detail_json,created_at) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                (tenant_id, actor, action, resource,
                 json.dumps(detail or {}, ensure_ascii=False), utc_now()),
            )

    def list_audit(self, tenant_id: str, limit: int = 100) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT actor,action,resource,detail_json,created_at FROM audit_log "
                "WHERE tenant_id=%s ORDER BY id DESC LIMIT %s",
                (tenant_id, max(1, min(limit, 500))),
            ).fetchall()
        return [{**dict(row), "detail": row["detail_json"],
                 "created_at": row["created_at"].isoformat()} for row in rows]

    def save_deployment(self, tenant_id: str, skill_name: str, config: Dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO deployments(tenant_id,skill_name,stable_version,candidate_version,"
                "canary_percent,shadow_percent,max_error_rate,min_samples,status,samples,errors,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0,%s) "
                "ON CONFLICT(tenant_id,skill_name) DO UPDATE SET stable_version=EXCLUDED.stable_version,"
                "candidate_version=EXCLUDED.candidate_version,canary_percent=EXCLUDED.canary_percent,"
                "shadow_percent=EXCLUDED.shadow_percent,max_error_rate=EXCLUDED.max_error_rate,"
                "min_samples=EXCLUDED.min_samples,status=EXCLUDED.status,samples=0,errors=0,"
                "updated_at=EXCLUDED.updated_at",
                (tenant_id, skill_name, config.get("stable_version"), config.get("candidate_version"),
                 int(config.get("canary_percent", 0)), int(config.get("shadow_percent", 0)),
                 float(config.get("max_error_rate", .1)), int(config.get("min_samples", 20)),
                 config.get("status", "running"), utc_now()),
            )
            conn.execute(
                "UPDATE deployments SET max_disagreement_rate=%s,auto_promote=%s,"
                "shadow_samples=0,disagreements=0,updated_at=%s "
                "WHERE tenant_id=%s AND skill_name=%s",
                (float(config.get("max_disagreement_rate", .2)),
                 bool(config.get("auto_promote", False)), utc_now(), tenant_id, skill_name),
            )

    def get_deployment(self, tenant_id: str, skill_name: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM deployments WHERE tenant_id=%s AND skill_name=%s",
                (tenant_id, skill_name),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["quality_budget"] = value.pop("quality_budget_json", None) or {}
        value["last_gate_result"] = value.pop("last_gate_result_json", None)
        for key in ("updated_at", "stage_started_at", "stage_deadline_at"):
            if value.get(key) is not None and hasattr(value[key], "isoformat"):
                value[key] = value[key].isoformat()
        return value

    def update_deployment(
        self, tenant_id: str, skill_name: str, **fields,
    ) -> Optional[Dict[str, Any]]:
        allowed = {
            "canary_percent", "shadow_percent", "status", "paused_by",
            "stage_started_at", "stage_deadline_at", "last_gate_result_json",
            "rollback_version", "rollback_reason",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_deployment(tenant_id, skill_name)
        updates["updated_at"] = utc_now()
        assignments = ", ".join("%s=%%s" % key for key in updates)
        params = list(updates.values()) + [tenant_id, skill_name]
        with self._connection() as conn:
            conn.execute(
                "UPDATE deployments SET %s WHERE tenant_id=%%s AND skill_name=%%s"
                % assignments, params,
            )
        return self.get_deployment(tenant_id, skill_name)

    def rollback_deployment(
        self, tenant_id: str, skill_name: str, reason: str,
        metrics_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT candidate_version FROM deployments WHERE tenant_id=%s AND skill_name=%s",
                (tenant_id, skill_name),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE deployments SET status='rolled_back', canary_percent=0, "
                "shadow_percent=0, rollback_reason=%s, rollback_version=candidate_version, "
                "last_gate_result_json=%s::jsonb, updated_at=%s "
                "WHERE tenant_id=%s AND skill_name=%s",
                (reason[:2000], json.dumps(metrics_snapshot or {}, ensure_ascii=False),
                 utc_now(), tenant_id, skill_name),
            )
        return self.get_deployment(tenant_id, skill_name)

    def record_deployment_result(
        self, tenant_id: str, skill_name: str, failed: bool,
    ) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "UPDATE deployments SET samples=samples+1,errors=errors+%s,updated_at=%s "
                "WHERE tenant_id=%s AND skill_name=%s RETURNING *",
                (int(failed), utc_now(), tenant_id, skill_name),
            ).fetchone()
            if not row:
                return None
            value = dict(row)
            if (value["status"] == "running" and value["samples"] >= value["min_samples"]
                    and value["errors"] / value["samples"] > value["max_error_rate"]):
                conn.execute(
                    "UPDATE deployments SET status='rolled_back',canary_percent=0,shadow_percent=0,"
                    "updated_at=%s WHERE tenant_id=%s AND skill_name=%s",
                    (utc_now(), tenant_id, skill_name),
                )
                value["status"] = "rolled_back"
        return value

    def record_shadow_observation(
        self, tenant_id: str, skill_name: str, task_id: str, lane: str,
        primary: Dict[str, Any], candidate: Optional[Dict[str, Any]],
        disagreement: float, candidate_failed: bool = False,
        stable_version: Optional[int] = None, candidate_version: Optional[int] = None,
        latency_ms: Optional[float] = None, cost_estimate: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Mirror of TaskStore.record_shadow_observation (see its docstring)."""
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO release_observations(tenant_id,skill_name,task_id,lane,"
                "primary_json,candidate_json,disagreement,candidate_failed,"
                "stable_version,candidate_version,latency_ms,cost_estimate,"
                "metrics_json,created_at) VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                (tenant_id, skill_name, task_id, lane,
                 json.dumps(primary, ensure_ascii=False),
                 json.dumps(candidate, ensure_ascii=False) if candidate is not None else None,
                 float(disagreement), bool(candidate_failed), stable_version,
                 candidate_version, latency_ms, cost_estimate,
                 json.dumps(metrics or {}, ensure_ascii=False), utc_now()),
            )
            row = conn.execute(
                "UPDATE deployments SET shadow_samples=shadow_samples+1,"
                "disagreements=disagreements+%s,updated_at=%s "
                "WHERE tenant_id=%s AND skill_name=%s RETURNING *",
                (int(disagreement > 0), utc_now(), tenant_id, skill_name),
            ).fetchone()
            if not row:
                return None
            value = dict(row)
            disagreement_rate = (
                value["disagreements"] / value["shadow_samples"]
                if value["shadow_samples"] else 0.0
            )
            error_rate = value["errors"] / value["samples"] if value["samples"] else 0.0
            if (
                value["status"] == "running" and value["auto_promote"]
                and value["shadow_samples"] >= value["min_samples"]
                and disagreement_rate <= value["max_disagreement_rate"]
                and error_rate <= value["max_error_rate"]
                and not candidate_failed
            ):
                conn.execute(
                    "UPDATE deployments SET status='promoted',stable_version=candidate_version,"
                    "canary_percent=0,shadow_percent=0,updated_at=%s "
                    "WHERE tenant_id=%s AND skill_name=%s",
                    (utc_now(), tenant_id, skill_name),
                )
                value["status"] = "promoted"
        return value

    def list_release_observations(
        self, tenant_id: str, skill_name: str, limit: int = 100,
    ) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM release_observations WHERE tenant_id=%s AND skill_name=%s "
                "ORDER BY id DESC LIMIT %s",
                (tenant_id, skill_name, max(1, min(limit, 500))),
            ).fetchall()
        values = []
        for row in rows:
            item = dict(row)
            item["primary"] = item.pop("primary_json")
            raw = item.pop("candidate_json")
            item["candidate"] = raw if raw is not None else None
            item["metrics"] = item.pop("metrics_json", {}) or {}
            item["accepted"] = bool(item.get("accepted", False))
            item["created_at"] = item["created_at"].isoformat()
            if item.get("evaluated_at") is not None and hasattr(item["evaluated_at"], "isoformat"):
                item["evaluated_at"] = item["evaluated_at"].isoformat()
            values.append(item)
        return values

    def create_alert(
        self, tenant_id: str, alert_key: str, severity: str, message: str,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO alerts(tenant_id,alert_key,severity,message,status,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,'open',%s,%s) ON CONFLICT(tenant_id,alert_key,status) DO NOTHING",
                (tenant_id, alert_key, severity, message[:1000], utc_now(), utc_now()),
            )

    def list_alerts(self, tenant_id: str, limit: int = 100) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE tenant_id=%s ORDER BY id DESC LIMIT %s",
                (tenant_id, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_installation(
        self, installation_id: int, account_login: str, tenant_id: str = "default"
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO installations(installation_id,account_login,created_at,tenant_id) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT(installation_id) DO UPDATE "
                "SET account_login=EXCLUDED.account_login,created_at=EXCLUDED.created_at,"
                "tenant_id=EXCLUDED.tenant_id",
                (installation_id, account_login, utc_now(), tenant_id),
            )

    def installation_tenant(self, installation_id: int) -> Optional[str]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT tenant_id FROM installations WHERE installation_id=%s",
                (installation_id,),
            ).fetchone()
        return row["tenant_id"] if row else None

    def dashboard_stats(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        with self._connection() as conn:
            where = " WHERE tenant_id=%s" if tenant_id is not None else ""
            params = (tenant_id,) if tenant_id is not None else ()
            row = conn.execute(
                "SELECT COUNT(*) AS total,COUNT(*) FILTER(WHERE state='SUCCESS') AS success,"
                "COUNT(*) FILTER(WHERE state='FAILED') AS failed FROM tasks" + where,
                params,
            ).fetchone()
            if tenant_id is None:
                failures = conn.execute(
                    "SELECT COUNT(*) AS n FROM failure_cases WHERE resolved=FALSE"
                ).fetchone()["n"]
            else:
                failures = conn.execute(
                    "SELECT COUNT(*) AS n FROM failure_cases f JOIN tasks t ON t.id=f.task_id "
                    "WHERE f.resolved=FALSE AND t.tenant_id=%s", (tenant_id,)
                ).fetchone()["n"]
            skills = conn.execute(
                "SELECT COUNT(*) AS n FROM skill_versions WHERE active=TRUE"
            ).fetchone()["n"]
            if tenant_id is None:
                skills += conn.execute(
                    "SELECT COUNT(*) AS n FROM skill_artifact_versions WHERE active=TRUE"
                ).fetchone()["n"]
            else:
                skills += conn.execute(
                    "SELECT COUNT(*) AS n FROM skill_artifact_versions "
                    "WHERE tenant_id=%s AND active=TRUE", (tenant_id,)
                ).fetchone()["n"]
        return {"tasks_total": row["total"], "tasks_success": row["success"], "tasks_failed": row["failed"],
                "success_rate": round(row["success"] / row["total"], 4) if row["total"] else 0.0,
                "unresolved_failure_cases": failures, "active_skill_versions": skills}


def create_store(database_url: str, sqlite_path: str):
    if database_url.startswith(("postgres://", "postgresql://")):
        return PostgresTaskStore(database_url)
    from .store import TaskStore
    return TaskStore(sqlite_path)
