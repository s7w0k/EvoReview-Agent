"""Read-only check + idempotent migration for EvoAgent databases.

Work Package 10: the migration itself is exactly what TaskStore/PostgresTaskStore
already run on startup (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS), so
this script only exposes it as an operator tool:

  python scripts/migrate_db.py --db data/evoagent.db --check   # read-only
  python scripts/migrate_db.py --db data/evoagent.db           # idempotent apply

--check never writes.  Both modes report missing tables/columns and exit 1 when
the schema is incomplete so automation can gate on readiness.
"""
import argparse
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REQUIRED_TABLES = [
    "tasks", "failure_cases", "skill_versions", "installations", "trace_events",
    "evaluation_cases", "evolution_runs", "skill_artifact_versions",
    "skill_evolution_runs", "checkpoints", "task_payloads", "agent_messages",
    "webhook_deliveries", "users", "memberships", "repository_grants",
    "audit_log", "deployments", "release_observations", "alerts",
    "agent_memories", "experiences", "skill_usage_stats",
    "chat_sessions", "chat_messages", "chat_context_snapshots", "chat_insights",
    "evolution_jobs", "evolution_hypotheses", "evolution_gate_results",
]

# Append-only columns added by later work packages; absent on pre-upgrade DBs.
REQUIRED_COLUMNS = {
    "evaluation_cases": [
        "source", "active", "category", "suite_id", "dataset_version",
        "repository", "language", "source_uri", "labeler_ids_json",
        "label_schema_version", "created_before_candidate",
    ],
    "failure_cases": ["source_key"],
    "release_observations": [
        "stable_version", "candidate_version", "metrics_json", "latency_ms",
        "cost_estimate", "human_label", "feedback_category", "accepted",
        "evaluated_at",
    ],
    "deployments": [
        "artifact_kind", "job_id", "approval_policy", "quality_budget_json",
        "stage_started_at", "stage_deadline_at", "last_gate_result_json",
        "rollback_version", "rollback_reason", "paused_by",
    ],
    "experiences": ["scope"],
}

# Work Package 0 (closed-loop evolution): tables/columns planned for later work
# packages.  They are *reported* when missing but never treated as a hard
# failure and never created by this script (no destructive migration on the
# first commit).  The closed-loop controller (WP1+) owns their creation.
PLANNED_TABLES = [
    "usage_events",
]

PLANNED_COLUMNS = {
    "evolution_runs": [
        "tenant_id", "job_id", "hypothesis_id", "status", "candidate_kind",
        "approval_status", "approved_by", "updated_at",
    ],
    "skill_evolution_runs": [
        "job_id", "hypothesis_id", "status", "approval_status",
        "approved_by", "updated_at",
    ],
    "skill_versions": [
        "status", "origin", "provenance_json", "patch_json", "updated_at",
        "activated_at", "archived_at",
    ],
}


def inspect_sqlite(path: str):
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {}
        for table in tables:
            columns[table] = {
                row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)
            }
        return tables, columns
    finally:
        conn.close()


def inspect_postgres(url: str):
    import psycopg
    conn = psycopg.connect(url)
    try:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public'"
        ).fetchall()
        tables = {row[0] for row in rows}
        columns = {}
        for table in tables:
            col_rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s", (table,)
            ).fetchall()
            columns[table] = {row[0] for row in col_rows}
        return tables, columns
    finally:
        conn.close()


def report(tables, columns) -> tuple:
    missing_tables = [name for name in REQUIRED_TABLES if name not in tables]
    missing_columns = []
    for table, expected in REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        for column in expected:
            if column not in columns.get(table, set()):
                missing_columns.append("%s.%s" % (table, column))
    # Planned items are informational only (WP0): they never fail --check.
    missing_planned_tables = [name for name in PLANNED_TABLES if name not in tables]
    missing_planned_columns = []
    for table, expected in PLANNED_COLUMNS.items():
        if table not in tables:
            continue
        for column in expected:
            if column not in columns.get(table, set()):
                missing_planned_columns.append("%s.%s" % (table, column))
    return missing_tables, missing_columns, missing_planned_tables, missing_planned_columns


def print_report(missing_tables, missing_columns, missing_planned_tables, missing_planned_columns) -> bool:
    for name in missing_tables:
        print("missing table: %s" % name)
    for name in missing_columns:
        print("missing column: %s" % name)
    for name in missing_planned_tables:
        print("planned table (not created yet): %s" % name)
    for name in missing_planned_columns:
        print("planned column (not added yet): %s" % name)
    if missing_tables or missing_columns:
        return False
    if not missing_planned_tables and not missing_planned_columns:
        print("schema OK: all required tables and columns present")
        return True
    # Required schema is complete; planned items are absent only by design.
    print("schema OK: required schema present (planned closed-loop schema pending)")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", required=True,
        help="SQLite file path or postgres:// connection URL",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Read-only schema inspection; never writes.",
    )
    args = parser.parse_args()

    if args.db.startswith("postgres"):
        from evoagent.postgres_store import PostgresTaskStore
        if not args.check:
            # Idempotent: CREATE TABLE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS.
            PostgresTaskStore(args.db)
        tables, columns = inspect_postgres(args.db)
        missing = report(tables, columns)
    else:
        from evoagent.store import TaskStore
        if not os.path.exists(args.db):
            print("error: database file does not exist: %s" % args.db)
            return 1
        if not args.check:
            TaskStore(args.db)
        tables, columns = inspect_sqlite(args.db)
        missing = report(tables, columns)
    ok = print_report(*missing)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
