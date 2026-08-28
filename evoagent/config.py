import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional


_DOTENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_dotenv(paths: Optional[Iterable[str]] = None) -> None:
    """Load local dotenv files without overriding real process environment values.

    The project-root file has priority over ``evoagent/.env``.  This allows the
    latter to remain compatible with existing local setups while keeping the
    conventional root-level ``.env`` as the recommended location.
    """
    package_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(package_dir)
    candidates = list(paths) if paths is not None else [
        os.path.join(project_root, ".env"),
        os.path.join(package_dir, ".env"),
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if not _DOTENV_KEY.fullmatch(key):
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)


load_dotenv()


def _int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError("%s must be positive" % name)
    return value


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _non_negative_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError("%s must be non-negative" % name)
    return value


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_path: str
    max_diff_bytes: int
    max_steps: int
    timeout_seconds: int
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    github_webhook_secret: str
    github_token: str
    auto_post_review: bool
    database_url: str = ""
    redis_url: str = ""
    # Durable control-plane backend selection (hardening plan Phase 7):
    # sqlite (default dev) | postgres (production) | json (test fallback).
    control_plane_backend: str = "sqlite"
    control_plane_path: str = ""
    async_workers: int = 2
    agent_max_workers: int = 4
    agent_retries: int = 1
    collaboration_rounds: int = 2
    agent_loop_max_steps: int = 4
    agent_loop_timeout_seconds: int = 45
    # Closed-loop runtime-policy recovery budgets (plan section 7.6).
    recovery_max_attempts: int = 3
    recovery_max_replans: int = 2
    recovery_max_model_switches: int = 2
    context_max_tokens: int = 12000
    context_reserved_tokens: int = 2500
    memory_enabled: bool = True
    memory_recall_limit: int = 6
    memory_working_ttl_seconds: int = 86400
    skills_dir: str = "skills"
    github_app_id: str = ""
    github_app_slug: str = ""
    github_private_key_path: str = ""
    public_base_url: str = "http://127.0.0.1:8080"
    llm_provider: str = "local"
    deepseek_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_site_url: str = ""
    openrouter_app_name: str = "EvoAgent"
    eval_max_cases: int = 5
    eval_min_cases: int = 3
    eval_min_improvement: float = 0.01
    eval_min_holdout_cases: int = 2
    eval_max_metric_regression: float = 0.0
    eval_source: str = "builtin"
    auth_required: bool = False
    auth_secret: str = ""
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""
    default_tenant_id: str = "default"
    session_ttl_seconds: int = 3600
    webhook_max_age_seconds: int = 600
    queue_max_attempts: int = 3
    queue_lease_seconds: int = 60
    skill_timeout_seconds: int = 30
    skill_memory_mb: int = 256
    skill_sandbox: bool = True
    skill_signing_key: str = ""
    skill_container_image: str = ""
    skill_lifecycle_enabled: bool = True
    repair_test_command: str = ""
    repair_verify_timeout_seconds: int = 120
    otel_endpoint: str = ""
    otel_service_name: str = "evoagent"
    alert_failure_rate: float = 0.20
    alert_min_samples: int = 10
    alert_window_seconds: int = 900
    alert_webhook_url: str = ""
    alert_smtp_host: str = ""
    alert_email_to: str = ""
    continuous_eval_seconds: int = 0
    experience_mode: str = "off"
    evolution_min_evidence: int = 2
    evolution_min_distinct_tasks: int = 2
    skill_marginal_gate: str = "off"
    skill_min_unique_tp: int = 1
    skill_max_new_fp: int = 0
    curator_enabled: bool = True
    curator_min_samples: int = 20
    curator_stale_days: int = 30
    static_analyzer: str = "off"
    finding_clustering: str = "off"
    confidence_enhance: bool = False
    confidence_buckets: str = "0.8,0.5"
    ast_fixer_enabled: bool = False
    ast_fix_max_files: int = 3
    ast_fix_max_lines: int = 10
    # Work Package 9: feedback trust and overfitting protection (all default
    # values preserve the pre-WP9 behavior exactly).
    feedback_min_confirmers: int = 1
    feedback_trust_enabled: bool = False
    feedback_trust_min_accepted_ratio: float = 0.5
    evolution_compare_history: int = 1
    evolution_cooldown_minutes: int = 0
    holdout_rotation: int = 0
    # Work Package 3: report-chat feature switches (all default off).
    chat_enabled: bool = False
    chat_insights_enabled: bool = False
    chat_feedback_enabled: bool = False
    # Work Package 6: chat budget, rate limiting and retention.  Defaults
    # preserve the pre-WP6 behavior exactly.
    chat_max_rounds: int = 30
    chat_max_message_chars: int = 8000
    chat_context_tokens: int = 10000
    chat_max_output_tokens: int = 1600
    chat_timeout_seconds: int = 60
    chat_max_citations: int = 20
    chat_max_insights: int = 10
    chat_max_concurrent_per_session: int = 1
    chat_retention_days: int = 0
    # Work Package 0 (closed-loop evolution): master switches and controller
    # budget.  ``false/manual/always`` preserve the current manual path: no
    # automatic scheduling, no automatic deployment, all production activation
    # requires approval.  These values are only consumed by later work packages
    # (WP1+) and have no runtime effect while the controller stays disabled.
    evolution_controller_enabled: bool = False
    evolution_trigger_mode: str = "manual"          # manual | scheduled | event
    evolution_approval_policy: str = "always"       # always | high-risk | never
    evolution_production_profile: bool = False
    evolution_max_concurrent_jobs: int = 1
    evolution_job_timeout_seconds: int = 3600
    evolution_job_max_retries: int = 3
    evolution_lease_seconds: int = 60
    # Work Package 3: enforce forgetting/generalization/production-source gates.
    evolution_quality_gates_enabled: bool = False
    # A2A remote reviewing (Phase 3/4 production integration).  Off by default:
    # an empty ``a2a_endpoints`` keeps the service on the local specialists.
    a2a_endpoints: str = ""
    a2a_token: str = ""
    a2a_timeout_seconds: float = 10.0
    # Six-core-agent architecture switch (plan Phase 0).  ``legacy`` keeps the
    # staged Multi-AgentCoordinator behaviour unchanged; ``six-agent`` enables
    # the loop-based Coordinator + five specialist agents.
    agent_architecture: str = "legacy"

    def resolved_llm(self) -> Dict[str, object]:
        """Resolve a named provider to the existing OpenAI-compatible transport."""
        provider = self.llm_provider.strip().lower()
        if provider in {"", "local", "none"}:
            if self.llm_base_url or self.llm_api_key or self.llm_model:
                provider = "custom"
            else:
                return {}

        if provider == "deepseek":
            api_key = self.deepseek_api_key or self.llm_api_key
            if not api_key:
                raise ValueError("DeepSeek requires EVOAGENT_DEEPSEEK_API_KEY")
            return {
                "provider": "deepseek",
                "base_url": self.llm_base_url or "https://api.deepseek.com",
                "api_key": api_key,
                "model": self.llm_model or "deepseek-v4-flash",
                "headers": {},
            }

        if provider in {"openrouter-deepseek-free", "openrouter_deepseek_free"}:
            api_key = self.openrouter_api_key or self.llm_api_key
            if not api_key:
                raise ValueError("OpenRouter requires EVOAGENT_OPENROUTER_API_KEY")
            headers = {}
            if self.openrouter_site_url:
                headers["HTTP-Referer"] = self.openrouter_site_url
            if self.openrouter_app_name:
                headers["X-Title"] = self.openrouter_app_name
            return {
                "provider": "openrouter-deepseek-free",
                "base_url": self.llm_base_url or "https://openrouter.ai/api/v1",
                "api_key": api_key,
                "model": self.llm_model or "deepseek/deepseek-chat-v3-0324:free",
                "headers": headers,
            }

        if provider == "openrouter-free":
            api_key = self.openrouter_api_key or self.llm_api_key
            if not api_key:
                raise ValueError("OpenRouter requires EVOAGENT_OPENROUTER_API_KEY")
            headers = {}
            if self.openrouter_site_url:
                headers["HTTP-Referer"] = self.openrouter_site_url
            if self.openrouter_app_name:
                headers["X-Title"] = self.openrouter_app_name
            return {
                "provider": "openrouter-free",
                "base_url": self.llm_base_url or "https://openrouter.ai/api/v1",
                "api_key": api_key,
                "model": self.llm_model or "openrouter/free",
                "headers": headers,
            }

        if provider == "custom":
            if not (self.llm_base_url and self.llm_api_key and self.llm_model):
                raise ValueError(
                    "Custom LLM requires EVOAGENT_LLM_BASE_URL, "
                    "EVOAGENT_LLM_API_KEY and EVOAGENT_LLM_MODEL"
                )
            return {
                "provider": "custom",
                "base_url": self.llm_base_url,
                "api_key": self.llm_api_key,
                "model": self.llm_model,
                "headers": {},
            }
        raise ValueError("unsupported EVOAGENT_LLM_PROVIDER: %s" % self.llm_provider)

    def validate_evolution(self) -> None:
        if self.eval_min_cases > self.eval_max_cases:
            raise ValueError("EVOAGENT_EVAL_MIN_CASES cannot exceed EVOAGENT_EVAL_MAX_CASES")
        valid_experience_modes = {"off", "shadow", "enforce"}
        if self.experience_mode not in valid_experience_modes:
            raise ValueError(
                "EVOAGENT_EXPERIENCE_MODE must be one of: %s"
                % ", ".join(sorted(valid_experience_modes))
            )
        if self.skill_marginal_gate not in {"off", "shadow", "enforce"}:
            raise ValueError("EVOAGENT_SKILL_MARGINAL_GATE must be one of: off, shadow, enforce")
        if self.static_analyzer not in {"off", "ast", "bandit", "ruff", "composite"}:
            raise ValueError(
                "EVOAGENT_STATIC_ANALYZER must be one of: off, ast, bandit, ruff, composite"
            )
        if self.finding_clustering not in {"off", "shadow", "on"}:
            raise ValueError(
                "EVOAGENT_FINDING_CLUSTERING must be one of: off, shadow, on"
            )
        from .confidence import parse_buckets
        parse_buckets(self.confidence_buckets)
        if not 0.0 <= self.eval_min_improvement <= 1.0:
            raise ValueError("EVOAGENT_EVAL_MIN_IMPROVEMENT must be between 0 and 1")
        if self.eval_min_holdout_cases > self.eval_max_cases:
            raise ValueError(
                "EVOAGENT_EVAL_MIN_HOLDOUT_CASES cannot exceed EVOAGENT_EVAL_MAX_CASES"
            )
        if not 0.0 <= self.eval_max_metric_regression <= 1.0:
            raise ValueError("EVOAGENT_EVAL_MAX_METRIC_REGRESSION must be between 0 and 1")
        if self.eval_source not in {"builtin", "github-real", "all"}:
            raise ValueError(
                "EVOAGENT_EVAL_SOURCE must be one of: builtin, github-real, all"
            )
        if self.feedback_min_confirmers < 1:
            raise ValueError("EVOAGENT_FEEDBACK_MIN_CONFIRMERS must be at least 1")
        if not 0.0 <= self.feedback_trust_min_accepted_ratio <= 1.0:
            raise ValueError(
                "EVOAGENT_FEEDBACK_TRUST_MIN_ACCEPTED_RATIO must be between 0 and 1"
            )
        if self.evolution_compare_history < 1:
            raise ValueError("EVOAGENT_EVOLUTION_COMPARE_HISTORY must be at least 1")
        if self.auth_required and len(self.auth_secret.encode("utf-8")) < 32:
            raise ValueError(
                "EVOAGENT_AUTH_SECRET must contain at least 32 bytes when authentication is enabled"
            )
        if bool(self.bootstrap_admin_username) != bool(self.bootstrap_admin_password):
            raise ValueError("bootstrap admin username and password must be configured together")
        if not 0.0 <= self.alert_failure_rate <= 1.0:
            raise ValueError("EVOAGENT_ALERT_FAILURE_RATE must be between 0 and 1")
        if self.agent_max_workers < 1:
            raise ValueError("EVOAGENT_AGENT_MAX_WORKERS must be at least 1")
        if self.agent_retries < 0:
            raise ValueError("EVOAGENT_AGENT_RETRIES cannot be negative")
        if self.collaboration_rounds < 1:
            raise ValueError("EVOAGENT_COLLABORATION_ROUNDS must be at least 1")
        if self.agent_loop_max_steps < 1:
            raise ValueError("EVOAGENT_AGENT_LOOP_MAX_STEPS must be at least 1")
        if self.context_max_tokens < 512:
            raise ValueError("EVOAGENT_CONTEXT_MAX_TOKENS must be at least 512")
        if not 0 <= self.context_reserved_tokens < self.context_max_tokens:
            raise ValueError(
                "EVOAGENT_CONTEXT_RESERVED_TOKENS must be smaller than the context budget"
            )
        if self.chat_feedback_enabled and not (self.chat_enabled and self.chat_insights_enabled):
            raise ValueError(
                "EVOAGENT_CHAT_FEEDBACK_ENABLED requires both "
                "EVOAGENT_CHAT_ENABLED and EVOAGENT_CHAT_INSIGHTS_ENABLED"
            )
        for name, value in (
            ("EVOAGENT_CHAT_MAX_ROUNDS", self.chat_max_rounds),
            ("EVOAGENT_CHAT_MAX_MESSAGE_CHARS", self.chat_max_message_chars),
            ("EVOAGENT_CHAT_MAX_OUTPUT_TOKENS", self.chat_max_output_tokens),
            ("EVOAGENT_CHAT_MAX_CITATIONS", self.chat_max_citations),
            ("EVOAGENT_CHAT_MAX_INSIGHTS", self.chat_max_insights),
            ("EVOAGENT_CHAT_MAX_CONCURRENT_PER_SESSION", self.chat_max_concurrent_per_session),
        ):
            if value < 1:
                raise ValueError("%s must be at least 1" % name)
        if self.chat_context_tokens < 1 or self.chat_context_tokens >= self.context_max_tokens:
            raise ValueError(
                "EVOAGENT_CHAT_CONTEXT_TOKENS must be smaller than "
                "EVOAGENT_CONTEXT_MAX_TOKENS"
            )
        if self.chat_timeout_seconds < 1:
            raise ValueError("EVOAGENT_CHAT_TIMEOUT_SECONDS must be at least 1")
        if self.chat_retention_days < 0:
            raise ValueError("EVOAGENT_CHAT_RETENTION_DAYS cannot be negative")
        # Work Package 0: closed-loop evolution master switches.  Unknown values
        # must fail fast at startup rather than silently selecting a risky mode.
        if self.evolution_trigger_mode not in {"manual", "scheduled", "event"}:
            raise ValueError(
                "EVOAGENT_EVOLUTION_TRIGGER_MODE must be one of: manual, scheduled, event"
            )
        if self.evolution_approval_policy not in {"always", "high-risk", "never"}:
            raise ValueError(
                "EVOAGENT_EVOLUTION_APPROVAL_POLICY must be one of: always, high-risk, never"
            )
        if self.evolution_max_concurrent_jobs < 1:
            raise ValueError("EVOAGENT_EVOLUTION_MAX_CONCURRENT_JOBS must be at least 1")
        if self.evolution_job_timeout_seconds < 1:
            raise ValueError("EVOAGENT_EVOLUTION_JOB_TIMEOUT_SECONDS must be at least 1")
        if self.evolution_job_max_retries < 0:
            raise ValueError("EVOAGENT_EVOLUTION_JOB_MAX_RETRIES cannot be negative")
        if self.evolution_lease_seconds < 1:
            raise ValueError("EVOAGENT_EVOLUTION_LEASE_SECONDS must be at least 1")
        if self.a2a_timeout_seconds <= 0:
            raise ValueError("EVOAGENT_A2A_TIMEOUT_SECONDS must be positive")
        if self.agent_architecture not in {"legacy", "six-agent"}:
            raise ValueError(
                "EVOAGENT_AGENT_ARCHITECTURE must be one of: legacy, six-agent"
            )
        self.validate_production_profile()

    def validate_production_profile(self) -> None:
        """Fail fast when the production profile is missing required safeguards."""
        if not self.evolution_production_profile:
            return
        if not self.auth_required:
            raise ValueError(
                "production profile requires EVOAGENT_AUTH_REQUIRED=true"
            )
        if len(self.auth_secret.encode("utf-8")) < 32:
            raise ValueError(
                "production profile requires a strong EVOAGENT_AUTH_SECRET (>=32 bytes)"
            )
        if self.eval_source == "builtin":
            raise ValueError(
                "production profile requires a real holdout dataset "
                "(EVOAGENT_EVAL_SOURCE must not be builtin)"
            )
        if self.eval_min_holdout_cases < 1:
            raise ValueError(
                "production profile requires EVOAGENT_EVAL_MIN_HOLDOUT_CASES >= 1"
            )
        if not self.bootstrap_admin_username:
            raise ValueError(
                "production profile requires a configured approver "
                "(EVOAGENT_BOOTSTRAP_ADMIN_USERNAME)"
            )
        if self.evolution_approval_policy == "never":
            raise ValueError(
                "production profile cannot use EVOAGENT_EVOLUTION_APPROVAL_POLICY=never"
            )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("EVOAGENT_HOST", "127.0.0.1"),
            port=_int("EVOAGENT_PORT", 8080),
            db_path=os.getenv("EVOAGENT_DB_PATH", "evoagent.db"),
            max_diff_bytes=_int("EVOAGENT_MAX_DIFF_BYTES", 1024 * 1024),
            max_steps=_int("EVOAGENT_MAX_STEPS", 8),
            timeout_seconds=_int("EVOAGENT_TIMEOUT_SECONDS", 120),
            llm_base_url=os.getenv("EVOAGENT_LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("EVOAGENT_LLM_API_KEY", ""),
            llm_model=os.getenv("EVOAGENT_LLM_MODEL", ""),
            github_webhook_secret=os.getenv("EVOAGENT_GITHUB_WEBHOOK_SECRET", ""),
            github_token=os.getenv("EVOAGENT_GITHUB_TOKEN", ""),
            auto_post_review=_bool("EVOAGENT_AUTO_POST_REVIEW"),
            database_url=os.getenv("EVOAGENT_DATABASE_URL", ""),
            redis_url=os.getenv("EVOAGENT_REDIS_URL", ""),
            control_plane_backend=os.getenv(
                "CONTROL_PLANE_BACKEND", "sqlite"
            ).strip().lower(),
            control_plane_path=os.getenv("CONTROL_PLANE_PATH", ""),
            async_workers=_int("EVOAGENT_ASYNC_WORKERS", 2),
            agent_max_workers=_int("EVOAGENT_AGENT_MAX_WORKERS", 4),
            agent_retries=_non_negative_int("EVOAGENT_AGENT_RETRIES", 1),
            collaboration_rounds=_int("EVOAGENT_COLLABORATION_ROUNDS", 2),
            agent_loop_max_steps=_int("EVOAGENT_AGENT_LOOP_MAX_STEPS", 4),
            agent_loop_timeout_seconds=_int("EVOAGENT_AGENT_LOOP_TIMEOUT_SECONDS", 45),
            context_max_tokens=_int("EVOAGENT_CONTEXT_MAX_TOKENS", 12000),
            context_reserved_tokens=_non_negative_int(
                "EVOAGENT_CONTEXT_RESERVED_TOKENS", 2500
            ),
            memory_enabled=_bool("EVOAGENT_MEMORY_ENABLED", True),
            memory_recall_limit=_int("EVOAGENT_MEMORY_RECALL_LIMIT", 6),
            memory_working_ttl_seconds=_int(
                "EVOAGENT_MEMORY_WORKING_TTL_SECONDS", 86400
            ),
            skills_dir=os.getenv("EVOAGENT_SKILLS_DIR", "skills"),
            github_app_id=os.getenv("EVOAGENT_GITHUB_APP_ID", ""),
            github_app_slug=os.getenv("EVOAGENT_GITHUB_APP_SLUG", ""),
            github_private_key_path=os.getenv("EVOAGENT_GITHUB_PRIVATE_KEY_PATH", ""),
            public_base_url=os.getenv("EVOAGENT_PUBLIC_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
            llm_provider=os.getenv("EVOAGENT_LLM_PROVIDER", "local"),
            deepseek_api_key=os.getenv("EVOAGENT_DEEPSEEK_API_KEY", ""),
            openrouter_api_key=os.getenv("EVOAGENT_OPENROUTER_API_KEY", ""),
            openrouter_site_url=os.getenv("EVOAGENT_OPENROUTER_SITE_URL", ""),
            openrouter_app_name=os.getenv("EVOAGENT_OPENROUTER_APP_NAME", "EvoAgent"),
            eval_max_cases=_int("EVOAGENT_EVAL_MAX_CASES", 5),
            eval_min_cases=_int("EVOAGENT_EVAL_MIN_CASES", 3),
            eval_min_improvement=float(os.getenv("EVOAGENT_EVAL_MIN_IMPROVEMENT", "0.01")),
            eval_min_holdout_cases=_non_negative_int("EVOAGENT_EVAL_MIN_HOLDOUT_CASES", 2),
            eval_max_metric_regression=float(
                os.getenv("EVOAGENT_EVAL_MAX_METRIC_REGRESSION", "0")
            ),
            eval_source=os.getenv("EVOAGENT_EVAL_SOURCE", "builtin").strip().lower(),
            auth_required=_bool("EVOAGENT_AUTH_REQUIRED", False),
            auth_secret=os.getenv("EVOAGENT_AUTH_SECRET", ""),
            bootstrap_admin_username=os.getenv("EVOAGENT_BOOTSTRAP_ADMIN_USERNAME", ""),
            bootstrap_admin_password=os.getenv("EVOAGENT_BOOTSTRAP_ADMIN_PASSWORD", ""),
            default_tenant_id=os.getenv("EVOAGENT_DEFAULT_TENANT_ID", "default"),
            session_ttl_seconds=_int("EVOAGENT_SESSION_TTL_SECONDS", 3600),
            webhook_max_age_seconds=_int("EVOAGENT_WEBHOOK_MAX_AGE_SECONDS", 600),
            queue_max_attempts=_int("EVOAGENT_QUEUE_MAX_ATTEMPTS", 3),
            queue_lease_seconds=_int("EVOAGENT_QUEUE_LEASE_SECONDS", 60),
            skill_timeout_seconds=_int("EVOAGENT_SKILL_TIMEOUT_SECONDS", 30),
            skill_memory_mb=_int("EVOAGENT_SKILL_MEMORY_MB", 256),
            skill_sandbox=_bool("EVOAGENT_SKILL_SANDBOX", True),
            skill_signing_key=os.getenv("EVOAGENT_SKILL_SIGNING_KEY", ""),
            skill_container_image=os.getenv("EVOAGENT_SKILL_CONTAINER_IMAGE", ""),
            skill_lifecycle_enabled=_bool("EVOAGENT_SKILL_LIFECYCLE_ENABLED", True),
            repair_test_command=os.getenv("EVOAGENT_REPAIR_TEST_COMMAND", ""),
            repair_verify_timeout_seconds=_int("EVOAGENT_REPAIR_VERIFY_TIMEOUT_SECONDS", 120),
            otel_endpoint=os.getenv("EVOAGENT_OTEL_ENDPOINT", ""),
            otel_service_name=os.getenv("EVOAGENT_OTEL_SERVICE_NAME", "evoagent"),
            alert_failure_rate=float(os.getenv("EVOAGENT_ALERT_FAILURE_RATE", "0.20")),
            alert_min_samples=_int("EVOAGENT_ALERT_MIN_SAMPLES", 10),
            alert_window_seconds=_int("EVOAGENT_ALERT_WINDOW_SECONDS", 900),
            alert_webhook_url=os.getenv("EVOAGENT_ALERT_WEBHOOK_URL", ""),
            alert_smtp_host=os.getenv("EVOAGENT_ALERT_SMTP_HOST", ""),
            alert_email_to=os.getenv("EVOAGENT_ALERT_EMAIL_TO", ""),
            continuous_eval_seconds=_non_negative_int(
                "EVOAGENT_CONTINUOUS_EVAL_SECONDS", 0
            ),
            experience_mode=os.getenv("EVOAGENT_EXPERIENCE_MODE", "off").strip().lower(),
            evolution_min_evidence=_int("EVOAGENT_EVOLUTION_MIN_EVIDENCE", 2),
            evolution_min_distinct_tasks=_int("EVOAGENT_EVOLUTION_MIN_DISTINCT_TASKS", 2),
            skill_marginal_gate=os.getenv("EVOAGENT_SKILL_MARGINAL_GATE", "off").strip().lower(),
            skill_min_unique_tp=_int("EVOAGENT_SKILL_MIN_UNIQUE_TP", 1),
            skill_max_new_fp=_non_negative_int("EVOAGENT_SKILL_MAX_NEW_FP", 0),
            curator_enabled=_bool(
                "EVOAGENT_SKILL_CURATOR_ENABLED",
                _bool("EVOAGENT_CURATOR_ENABLED", True),
            ),
            curator_min_samples=_int("EVOAGENT_CURATOR_MIN_SAMPLES", 20),
            curator_stale_days=_int("EVOAGENT_CURATOR_STALE_DAYS", 30),
            static_analyzer=os.getenv("EVOAGENT_STATIC_ANALYZER", "off").strip().lower(),
            finding_clustering=os.getenv("EVOAGENT_FINDING_CLUSTERING", "off").strip().lower(),
            confidence_enhance=_bool("EVOAGENT_CONFIDENCE_ENHANCE", False),
            confidence_buckets=os.getenv("EVOAGENT_CONFIDENCE_BUCKETS", "0.8,0.5"),
            ast_fixer_enabled=_bool("EVOAGENT_AST_FIXER_ENABLED", False),
            ast_fix_max_files=_int("EVOAGENT_AST_FIX_MAX_FILES", 3),
            ast_fix_max_lines=_int("EVOAGENT_AST_FIX_MAX_LINES", 10),
            feedback_min_confirmers=_int("EVOAGENT_FEEDBACK_MIN_CONFIRMERS", 1),
            feedback_trust_enabled=_bool("EVOAGENT_FEEDBACK_TRUST_ENABLED", False),
            feedback_trust_min_accepted_ratio=float(
                os.getenv("EVOAGENT_FEEDBACK_TRUST_MIN_ACCEPTED_RATIO", "0.5")
            ),
            evolution_compare_history=_int("EVOAGENT_EVOLUTION_COMPARE_HISTORY", 1),
            evolution_cooldown_minutes=_non_negative_int(
                "EVOAGENT_EVOLUTION_COOLDOWN_MINUTES", 0
            ),
            holdout_rotation=_non_negative_int("EVOAGENT_HOLDOUT_ROTATION", 0),
            chat_enabled=_bool("EVOAGENT_CHAT_ENABLED", False),
            chat_insights_enabled=_bool("EVOAGENT_CHAT_INSIGHTS_ENABLED", False),
            chat_feedback_enabled=_bool("EVOAGENT_CHAT_FEEDBACK_ENABLED", False),
            chat_max_rounds=_int("EVOAGENT_CHAT_MAX_ROUNDS", 30),
            chat_max_message_chars=_int("EVOAGENT_CHAT_MAX_MESSAGE_CHARS", 8000),
            chat_context_tokens=_int("EVOAGENT_CHAT_CONTEXT_TOKENS", 10000),
            chat_max_output_tokens=_int("EVOAGENT_CHAT_MAX_OUTPUT_TOKENS", 1600),
            chat_timeout_seconds=_int("EVOAGENT_CHAT_TIMEOUT_SECONDS", 60),
            chat_max_citations=_int("EVOAGENT_CHAT_MAX_CITATIONS", 20),
            chat_max_insights=_int("EVOAGENT_CHAT_MAX_INSIGHTS", 10),
            chat_max_concurrent_per_session=_int(
                "EVOAGENT_CHAT_MAX_CONCURRENT_PER_SESSION", 1
            ),
            chat_retention_days=_non_negative_int("EVOAGENT_CHAT_RETENTION_DAYS", 0),
            evolution_controller_enabled=_bool(
                "EVOAGENT_EVOLUTION_CONTROLLER_ENABLED", False
            ),
            evolution_trigger_mode=os.getenv(
                "EVOAGENT_EVOLUTION_TRIGGER_MODE", "manual"
            ).strip().lower(),
            evolution_approval_policy=os.getenv(
                "EVOAGENT_EVOLUTION_APPROVAL_POLICY", "always"
            ).strip().lower(),
            evolution_production_profile=_bool(
                "EVOAGENT_EVOLUTION_PRODUCTION_PROFILE", False
            ),
            evolution_max_concurrent_jobs=_int(
                "EVOAGENT_EVOLUTION_MAX_CONCURRENT_JOBS", 1
            ),
            evolution_job_timeout_seconds=_int(
                "EVOAGENT_EVOLUTION_JOB_TIMEOUT_SECONDS", 3600
            ),
            evolution_job_max_retries=_non_negative_int(
                "EVOAGENT_EVOLUTION_JOB_MAX_RETRIES", 3
            ),
            evolution_lease_seconds=_int("EVOAGENT_EVOLUTION_LEASE_SECONDS", 60),
            evolution_quality_gates_enabled=_bool(
                "EVOAGENT_EVOLUTION_QUALITY_GATES_ENABLED", False
            ),
            a2a_endpoints=os.getenv("EVOAGENT_A2A_ENDPOINTS", "").strip(),
            a2a_token=os.getenv("EVOAGENT_A2A_TOKEN", ""),
            a2a_timeout_seconds=float(
                os.getenv("EVOAGENT_A2A_TIMEOUT_SECONDS", "10")
            ),
            agent_architecture=os.getenv(
                "EVOAGENT_AGENT_ARCHITECTURE", "legacy"
            ).strip().lower(),
        )
