"""Persistent Evolution Controller (closed-loop WP1).

Turns the one-shot ``auto_propose`` call into a durable, idempotent, recoverable
job.  It does *not* copy any evaluation logic: it delegates to the existing
``EvolutionEngine`` (prompt) and ``SkillEvolutionEngine`` (declarative rule
skill), and only owns scheduling, leasing, checkpointing and recovery.

While ``settings.evolution_controller_enabled`` is False (the default), the
controller is inert and the system behaves exactly like the pre-WP1 manual
path.
"""
import hashlib
import logging
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from . import evolution_reflection as reflection
from . import evolution_state as state
from .metrics import metrics

logger = logging.getLogger(__name__)


class EvolutionJobError(RuntimeError):
    """A stable, user-visible job control error."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _fingerprint_ref(items: list) -> str:
    """Stable trigger_ref for an event batch of corroborated experiences."""
    digest = hashlib.sha256()
    for item in sorted(items):
        digest.update(str(item).encode("utf-8"))
    return digest.hexdigest()[:24]


class EvolutionController:
    def __init__(
        self, store, settings,
        prompt_engine, skill_engine,
    ):
        self.store = store
        self.settings = settings
        self.prompt_engine = prompt_engine
        self.skill_engine = skill_engine
        self._worker_id = "%s-%s" % (socket.gethostname(), uuid.uuid4().hex[:8])
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._scanner_thread = None

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.settings.evolution_controller_enabled

    @property
    def lease_seconds(self) -> int:
        return max(1, self.settings.evolution_lease_seconds)

    def _budget(self) -> Dict[str, Any]:
        return {
            "max_retries": self.settings.evolution_job_max_retries,
            "timeout_seconds": self.settings.evolution_job_timeout_seconds,
            "max_concurrent_jobs": self.settings.evolution_max_concurrent_jobs,
        }

    # ------------------------------------------------------------------
    def enqueue(
        self, tenant_id: str, capability_kind: str, capability_name: str,
        trigger_type: str = state.TRIGGER_MANUAL, trigger_ref: str = "",
        repository_scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Idempotently create a job; a duplicate active event returns the
        existing job instead of creating a second one."""
        tenant_id = tenant_id or "default"
        capability_kind = capability_kind or ""
        capability_name = (capability_name or "").strip().lower()
        if capability_kind not in state.CAPABILITY_KINDS:
            raise EvolutionJobError(
                "unsupported capability_kind: %s" % capability_kind)
        if not capability_name:
            raise EvolutionJobError("capability_name is required")
        if trigger_type not in state.TRIGGER_TYPES:
            raise EvolutionJobError("unsupported trigger_type: %s" % trigger_type)

        key = state.build_idempotency_key(
            tenant_id, capability_kind, capability_name, trigger_type, trigger_ref)
        with self._lock:
            existing = self.store.find_active_evolution_job(tenant_id, key)
            if existing:
                return {"job": existing, "created": False}
            # A previous terminal job may still hold the unique key; retry once
            # with a generation suffix so a brand-new round can be created.
            job = self._insert_job(
                tenant_id, repository_scope, capability_kind, capability_name,
                trigger_type, trigger_ref, key)
            if job is None:
                generation = _utc_now().strftime("%Y%m%d%H%M%S%f")
                key = state.build_idempotency_key(
                    tenant_id, capability_kind, capability_name,
                    trigger_type, "%s:%s" % (trigger_ref or "manual", generation))
                job = self._insert_job(
                    tenant_id, repository_scope, capability_kind, capability_name,
                    trigger_type, trigger_ref, key)
                if job is None:
                    raise EvolutionJobError("could not create a unique evolution job")
        metrics.inc("evolution_jobs_total")
        return {"job": job, "created": True}

    def _insert_job(
        self, tenant_id, repository_scope, capability_kind, capability_name,
        trigger_type, trigger_ref, idempotency_key,
    ) -> Optional[Dict[str, Any]]:
        job_id = uuid.uuid4().hex
        return self.store.create_evolution_job(
            job_id, tenant_id, repository_scope, capability_kind, capability_name,
            trigger_type, trigger_ref, idempotency_key,
            self._budget(), self.settings.evolution_job_max_retries,
        )

    # ------------------------------------------------------------------
    def get_job(self, job_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_evolution_job(job_id, tenant_id or "default")

    def list_jobs(self, tenant_id: str, limit: int = 50) -> list:
        return self.store.list_evolution_jobs(tenant_id or "default", limit)

    # ------------------------------------------------------------------
    def run_job(self, job_id: str, tenant_id: str) -> Dict[str, Any]:
        """Claim a pending job and execute its capability (exactly-once lease)."""
        tenant_id = tenant_id or "default"
        lease_until = _iso(_utc_now() + timedelta(seconds=self.lease_seconds))
        if not self.store.acquire_evolution_job_lease(
            job_id, tenant_id, self._worker_id, lease_until):
            raise EvolutionJobError("job is not available for execution")

        job = self.store.get_evolution_job(job_id, tenant_id)
        try:
            self.store.update_evolution_job_checkpoint(
                job_id, tenant_id, state.STEP_COLLECTING,
                {"step": state.STEP_COLLECTING, "worker": self._worker_id})
            result = self._execute(job)
            self.store.update_evolution_job_checkpoint(
                job_id, tenant_id, state.STEP_DONE,
                {"step": state.STEP_DONE, "decision": result.get("decision")})
            self.store.update_evolution_job(
                job_id, tenant_id, status=state.JOB_COMPLETED,
                current_step=state.STEP_DONE,
                candidate_version=self._candidate_version(result),
                evolution_run_id=result.get("run_id"),
                finished_at=_iso(_utc_now()), error=None)
            metrics.inc("evolution_jobs_completed_total")
            return {"job": self.store.get_evolution_job(job_id, tenant_id),
                    "result": result}
        except Exception as exc:  # noqa: BLE001 - must mark the job failed
            self.store.update_evolution_job(
                job_id, tenant_id, status=state.JOB_FAILED,
                current_step=self._current_step(job_id, tenant_id),
                error=str(exc)[:2000], finished_at=_iso(_utc_now()))
            metrics.inc("evolution_jobs_failed_total")
            raise
        finally:
            self.store.release_evolution_job_lease(job_id, tenant_id, self._worker_id)

    def _current_step(self, job_id: str, tenant_id: str) -> str:
        job = self.store.get_evolution_job(job_id, tenant_id)
        return job.get("current_step") if job else state.STEP_COLLECTING

    @staticmethod
    def _candidate_version(result: Dict[str, Any]) -> Optional[int]:
        version = result.get("version")
        if isinstance(version, dict):
            return version.get("version")
        return None

    def _execute(self, job: Dict[str, Any]) -> Dict[str, Any]:
        if job["capability_kind"] == state.CAPABILITY_PROMPT:
            return self.prompt_engine.auto_propose(
                job["capability_name"], job["tenant_id"])
        if job["capability_kind"] == state.CAPABILITY_RULE_SKILL:
            return self.skill_engine.auto_propose(
                job["capability_name"], job["tenant_id"])
        raise EvolutionJobError(
            "unsupported capability_kind: %s" % job["capability_kind"])

    # ------------------------------------------------------------------
    def pause(self, job_id: str, tenant_id: str) -> Dict[str, Any]:
        tenant_id = tenant_id or "default"
        job = self._require_job(job_id, tenant_id)
        if job["status"] not in {state.JOB_PENDING, state.JOB_RUNNING}:
            raise EvolutionJobError("only pending/running jobs can be paused")
        updated = self.store.update_evolution_job(
            job_id, tenant_id, status=state.JOB_PAUSED)
        return {"job": updated, "paused": True}

    def resume(self, job_id: str, tenant_id: str) -> Dict[str, Any]:
        tenant_id = tenant_id or "default"
        job = self._require_job(job_id, tenant_id)
        if job["status"] != state.JOB_PAUSED:
            raise EvolutionJobError("only paused jobs can be resumed")
        updated = self.store.update_evolution_job(
            job_id, tenant_id, status=state.JOB_PENDING,
            lease_owner=None, lease_until=None, error=None)
        return {"job": updated, "resumed": True}

    def cancel(self, job_id: str, tenant_id: str) -> Dict[str, Any]:
        tenant_id = tenant_id or "default"
        job = self._require_job(job_id, tenant_id)
        if job["status"] in {state.JOB_COMPLETED, state.JOB_CANCELLED}:
            raise EvolutionJobError("job is already terminal")
        updated = self.store.update_evolution_job(
            job_id, tenant_id, status=state.JOB_CANCELLED,
            finished_at=_iso(_utc_now()))
        return {"job": updated, "cancelled": True}

    def retry(self, job_id: str, tenant_id: str) -> Dict[str, Any]:
        tenant_id = tenant_id or "default"
        job = self._require_job(job_id, tenant_id)
        if job["status"] != state.JOB_FAILED:
            raise EvolutionJobError("only failed jobs can be retried")
        retry_count = int(job.get("retry_count", 0))
        if retry_count >= int(job.get("max_retries", self.settings.evolution_job_max_retries)):
            raise EvolutionJobError("job has exhausted its retry budget")
        updated = self.store.update_evolution_job(
            job_id, tenant_id, status=state.JOB_PENDING,
            retry_count=retry_count + 1, lease_owner=None, lease_until=None,
            error=None, finished_at=None)
        return {"job": updated, "retried": True}

    def _require_job(self, job_id: str, tenant_id: str) -> Dict[str, Any]:
        job = self.store.get_evolution_job(job_id, tenant_id)
        if not job:
            raise EvolutionJobError("evolution job not found")
        return job

    # ------------------------------------------------------------------
    def recover_expired(self) -> int:
        """Reclaim running jobs whose lease expired (crash recovery).

        Returns the number of jobs recovered.  A job whose retry budget is not
        exhausted is reset to pending so another worker can take it over.
        """
        recovered = 0
        for job in self.store.list_expired_evolution_jobs(_iso(_utc_now())):
            retry_count = int(job.get("retry_count", 0))
            max_retries = int(job.get("max_retries", self.settings.evolution_job_max_retries))
            if retry_count < max_retries:
                self.store.update_evolution_job(
                    job["id"], job["tenant_id"], status=state.JOB_PENDING,
                    retry_count=retry_count + 1, lease_owner=None, lease_until=None,
                    error="recovered after lease expiry")
            else:
                self.store.update_evolution_job(
                    job["id"], job["tenant_id"], status=state.JOB_FAILED,
                    error="lease expired and retry budget exhausted",
                    finished_at=_iso(_utc_now()))
            metrics.inc("evolution_jobs_recovered_total")
            recovered += 1
        return recovered

    def reflect(self, tenant_id: str, job_id: str = "") -> list:
        """Reflect corroborated Experience into structured Hypotheses (WP2).

        Idempotent bypass step: experiences already cited by an existing
        Hypothesis are skipped, so repeated calls do not duplicate proposals.
        Returns the persisted Hypotheses (empty when the controller is disabled
        or there is no new evidence).
        """
        if not self.enabled:
            return []
        tenant_id = tenant_id or "default"
        experiences = self.store.list_experiences(tenant_id, status="corroborated")
        if not experiences:
            return []
        used = set()
        for existing in self.store.list_hypotheses(tenant_id, limit=500):
            used.update(existing.get("evidence_ids", []) or [])
        fresh = [e for e in experiences if e["id"] not in used]
        if not fresh:
            return []
        case_ids_by_experience = {
            e["id"]: self.store.find_failure_case_ids_for_experiences([e])
            for e in fresh
        }
        hypotheses = reflection.reflect(
            experiences=fresh,
            case_ids_by_experience=case_ids_by_experience,
        )
        persisted = []
        for hypothesis in hypotheses:
            hypothesis["job_id"] = job_id or ""
            persisted.append(self.store.create_hypothesis(hypothesis))
        return persisted

    def scan_once(self) -> int:
        """Event trigger: enqueue a job for each tenant/capability that has a
        corroborated rule candidate.  Returns the number of jobs created."""
        if not self.enabled:
            return 0
        created = 0
        for tenant_id in self._corroborated_tenants():
            for capability_name in ("evolved-review",):
                ref = _fingerprint_ref([tenant_id, capability_name])
                outcome = self.enqueue(
                    tenant_id, state.CAPABILITY_RULE_SKILL, capability_name,
                    state.TRIGGER_EVENT, ref)
                if outcome["created"]:
                    created += 1
        return created

    def _corroborated_tenants(self) -> list:
        """Tenants that currently hold corroborated rule-candidate experience."""
        tenants = []
        try:
            # Best-effort distinct tenant scan over corroborated candidates.
            for tenant_id in self.store.list_distinct_experience_tenants():
                if self.store.list_corroborated_rule_candidates(tenant_id):
                    tenants.append(tenant_id)
        except AttributeError:
            # Backend without the distinct-tenant helper falls back to default.
            if self.store.list_corroborated_rule_candidates("default"):
                tenants.append("default")
        return tenants

    def start_scanner(self) -> None:
        if not self.enabled or self.settings.continuous_eval_seconds <= 0:
            return
        if self._scanner_thread and self._scanner_thread.is_alive():
            return
        self._scanner_thread = threading.Thread(
            target=self._scanner_loop, name="evoagent-evolution-scanner",
            daemon=True)
        self._scanner_thread.start()

    def _scanner_loop(self) -> None:
        interval = self.settings.continuous_eval_seconds
        while not self._stop.is_set():
            self._stop.wait(interval)
            if self._stop.is_set():
                return
            try:
                self.recover_expired()
                self.scan_once()
            except Exception:  # noqa: BLE001 - scanner must never crash the process
                logger.warning("evolution scanner iteration failed", exc_info=True)

    def close(self) -> None:
        self._stop.set()
        if self._scanner_thread and self._scanner_thread.is_alive():
            self._scanner_thread.join(timeout=2)
        self._scanner_thread = None
