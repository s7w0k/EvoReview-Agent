"""Unified Risk Signal Catalog (plan §Phase 1 / §3.2).

A single source of truth for semantic risk signals shared by
``profile_risk()``, ``semantic_change_summary()``, the SemanticPlanner and the
FallbackPlanner.  The catalog only inspects the raw diff + parsed added lines
(never case ids, expected labels or holdout answers), so routing stays true to
the production signal while satisfying the evaluation isolation rules.

Each signal is declared declaratively (regex over lower-cased added lines) plus
an optional ``needs``/``excludes`` predicate for the semantic-confirmation rules
described in plan §Phase 4 (e.g. path-traversal needs external input; weak-hash
must be used for credentials rather than simulation).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# --- domain constants --------------------------------------------------------
SECURITY = "security"
RELIABILITY = "reliability"

_AGENT_SECURITY = "security-agent"
_AGENT_RELIABILITY = "reliability-agent"

# Centralized CWE -> domain ownership (plan §Phase 3).  Avoids re-encoding the
# domain split in the Adapter or Evolution protocol.  A candidate rule carries
# an explicit ``domain``; this map is the fallback for stable rules / CWE-led
# heuristics so specialist composition stays consistent.
CWE_TO_DOMAIN: Dict[Any, str] = {
    # security families
    22: SECURITY, 78: SECURITY, 89: SECURITY, 95: SECURITY, 117: SECURITY,
    328: SECURITY, 330: SECURITY, 377: SECURITY, 502: SECURITY, 601: SECURITY,
    614: SECURITY, 617: SECURITY, 798: SECURITY,
    # reliability families
    362: RELIABILITY, 367: RELIABILITY, 400: RELIABILITY, 682: RELIABILITY,
    703: RELIABILITY, 772: RELIABILITY, 835: RELIABILITY,
}

SHARED = "shared"


def cwe_domain(cwe: Any, default: str = SHARED) -> str:
    """Map a CWE identifier to its owning specialist domain."""
    try:
        return CWE_TO_DOMAIN.get(int(cwe), default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RiskSignal:
    code: str
    domain: str
    family: str            # canonical issue family / CWE family
    severity_floor: str    # low | medium | high | critical
    pattern: re.Pattern    # matched against the lower-cased blob / added lines
    description: str = ""
    # Semantic-confirmation: blob must contain one of these too.
    needs: tuple = ()
    # Semantic-exclusion: blob containing any of these cancels the signal.
    excludes: tuple = ("# pragma: noqa", "# nosec", "_test", "fixture")
    # Set only when the signal should be a high-confidence *routing* driver.
    routes_security: bool = False
    routes_reliability: bool = False


def _rx(*tokens: str) -> re.Pattern:
    return re.compile(r"|".join(re.escape(t) for t in tokens))


# --------------------------------------------------------------------------- #
# Security signal families (plan §1 security list)
# --------------------------------------------------------------------------- #
SECURITY_SIGNALS: List[RiskSignal] = [
    RiskSignal("PROCESS_EXECUTION", SECURITY, "command-injection", "high",
               _rx("subprocess", "os.system", "os.popen", "shell=True", "winreg"),
               "Command / shell execution", routes_security=True),
    RiskSignal("DANGEROUS_EVAL", SECURITY, "code-injection", "critical",
               _rx("eval(", "exec(", "compile("),
               "Dynamic code execution", routes_security=True),
    RiskSignal("SQL_INJECTION", SECURITY, "sql-injection", "high",
               _rx("execute(", "executemany", "cursor.execute", "%s", "%d"),
               "Dynamic SQL execution", needs=("sql", "select ", "insert", "update", "delete from",
                                               "cursor", "db.", "query"),
               routes_security=True),
    RiskSignal("HARDCODED_CREDENTIAL", SECURITY, "hardcoded-credential", "high",
               re.compile(r"(?:password|passwd|api[_-]?key|secret|client[_-]?secret|token)"
                          r"\s*[:=]\s*['\"][^'\"]{4,}['\"]", re.I),
               "Literal credentials", routes_security=True),
    RiskSignal("CREDENTIAL_VARIABLE", SECURITY, "credential", "medium",
               _rx("password", "passwd", "secret", "api_key", "token"),
               "Credential references", routes_security=True),
    RiskSignal("INSECURE_DESERIALIZATION", SECURITY, "insecure-deserialization", "high",
               _rx("pickle.load", "pickle.dumps", "yaml.load(", "yaml.unsafe_load",
                   "marshal.load", "shelve"),
               "Unsafe object deserialization", needs=("pickle", "yaml", "load", "unpickle"),
               routes_security=True),
    RiskSignal("PATH_TRAVERSAL", SECURITY, "path-traversal", "high",
               _rx("os.path.join", "join(", "open(", "Path("),
               "User input used to build a filesystem path",
               needs=("input", "request", "args", "getenv", "argv", "filename", "user"),
               routes_security=True),
    RiskSignal("WEAK_HASH", SECURITY, "weak-hash", "medium",
               _rx("md5(", "sha1("),
               "Weak message digest used for credentials/signatures",
               needs=("password", "secret", "token", "auth", "sign", "hash"),
               routes_security=False),
    RiskSignal("INSECURE_RANDOM", SECURITY, "insecure-random", "medium",
               _rx("random.random", "random.randint", "random.choice", "np.random"),
               "CSPRNG required but mundane PRNG used for secrets/security tokens",
               needs=("token", "secret", "password", "salt", "otp", "nonce"),
               excludes=("_test", "fixture", "mock", "benchmark"),
               routes_security=True),
    RiskSignal("INSECURE_TEMPFILE", SECURITY, "insecure-tempfile", "medium",
               _rx("tempfile.mktemp", "/tmp/", "os.tmpnam"),
               "Unsafe temporary file handling", routes_security=True),
    RiskSignal("ASSERT_AUTH", SECURITY, "auth-assertion", "medium",
               _rx("assert", "verify_signature", "token_verify"),
               "Authorization/identity enforced via assert",
               needs=("auth", "login", "permission", "role", "user", "token"),
               routes_security=True),
    RiskSignal("INSECURE_COOKIE", SECURITY, "insecure-cookie", "medium",
               _rx("set_cookie", "cookie", "session.cookie", "Set-Cookie"),
               "Cookie without Secure / HttpOnly / SameSite",
               needs=("cookie", "session"),
               routes_security=False),
    RiskSignal("OPEN_REDIRECT", SECURITY, "open-redirect", "medium",
               _rx("redirect", "location", "next=", "return_url", "target_url"),
               "Unvalidated redirect target", needs=("redirect", "next", "return",
                                                     "target", "url"),
               routes_security=True),
    RiskSignal("LOG_FORGING", SECURITY, "log-forging", "medium",
               _rx("log.info", "log.error", "logger.", "logging.", "printf", "print("),
               "External input fed into newline-sensitive logs",
               needs=("log", "print", "printf"),
               routes_security=False),
    RiskSignal("EXTERNAL_INPUT", SECURITY, "external-input", "medium",
               _rx("input(", "request.", "sys.argv", "args", "os.getenv", "stdin",
                   "form[", "body[", "get_json", "argparse"),
               "External input boundary", routes_security=True),
    RiskSignal("EXTERNAL_INPUT_TO_SINK", SECURITY, "dataflow", "high",
               _rx("exec(", "eval(", "shell=True", "os.system", "cursor.execute",
                   "pickle.load", "subprocess"),
               "External input reaching a dangerous sink",
               needs=("input", "request", "args", "getenv", "argv", "stdin"),
               routes_security=True),
]

# --------------------------------------------------------------------------- #
# Reliability signal families (plan §1 reliability list)
# --------------------------------------------------------------------------- #
RELIABILITY_SIGNALS: List[RiskSignal] = [
    RiskSignal("UNBOUNDED_RETRY", RELIABILITY, "unbounded-retry", "high",
               _rx("while True", "while 1", "for attempt", "while not", "retry"),
               "Unbounded retry / busy loop", needs=("while", "for", "retry"),
               excludes=("_test", "fixture", "timeout", "sleep", "break"),
               routes_reliability=True),
    RiskSignal("FLOAT_MONEY", RELIABILITY, "float-money", "medium",
               _rx("price", "amount", "balance", "total", "money", "cost", "usd", "currency"),
               "Floating point used for money / precision-sensitive values",
               needs=("price", "amount", "balance", "total", "cost", "usd", "currency"),
               excludes=("_test", "fixture", "mock"),
               routes_reliability=True),
    RiskSignal("NAIVE_DATETIME", RELIABILITY, "time-race", "medium",
               _rx("datetime.now", "utcnow", "time.time", "strftime", "fromtimestamp"),
               "Time handling that is not timezone-safe / race-prone",
               needs=("now", "utc", "time", "date"),
               routes_reliability=True),
    RiskSignal("BLOCKING_ASYNC", RELIABILITY, "blocking-async", "high",
               _rx("async def", "await ", "asyncio", "import asyncio"),
               "Blocking call inside an async context",
               needs=("async", "await", "asyncio", "time.sleep", "requests.get",
                      "urlopen", "open("),
               routes_reliability=True),
    RiskSignal("NONATOMIC_WRITE", RELIABILITY, "non-atomic-write", "medium",
               _rx("open(", "write(", "os.rename", "shutil", "tempfile", ".json"),
               "Non-atomic state / file write",
               needs=("open(", "write", "rename", "file", "json", "save"),
               routes_reliability=True),
    RiskSignal("EMPTY_EXCEPT", RELIABILITY, "exception-swallow", "medium",
               re.compile(r"except\s*(Exception)?\s*:\s*(pass|continue|return None)\s*$"),
               "Exception swallowed", routes_reliability=True),
    RiskSignal("SHARED_STATE_THREAD", RELIABILITY, "shared-state", "medium",
               _rx("threading", "Thread(", "lock", "mutex", "semaphore", "RWMutex", "go func"),
               "Shared state written without synchronization",
               needs=("thread", "lock", "mutex", "semaphore", "concurrent", "shared"),
               routes_reliability=True),
    RiskSignal("RESOURCE_LIFECYCLE", RELIABILITY, "resource-leak", "medium",
               _rx("conn", "cursor", "open(", "session", "client", "socket"),
               "Unclosed / unconditionally acquired resource",
               needs=("conn", "cursor", "open(", "client", "socket"),
               excludes=("with ", "finally", "self.conn", "close("),
               routes_reliability=False),
    RiskSignal("BLOCKING_READ", RELIABILITY, "blocking-io", "medium",
               _rx(".read()", "readline", "readlines"),
               "Blocking / unbounded read", needs=("read", "stdin", "file"),
               routes_reliability=False),
    RiskSignal("DEBUG_PRINT", RELIABILITY, "debug-output", "low",
               _rx("print(", "console.log", "printf", "sys.stdout.write"),
               "Debug output / unguarded logging",
               needs=("print", "console", "printf", "stdout", "log"),
               routes_reliability=True),
]


def _hits(signal: RiskSignal, blob: str) -> bool:
    if not signal.pattern.search(blob):
        return False
    if signal.needs and not any(t in blob for t in signal.needs):
        return False
    if signal.excludes and any(t in blob for t in signal.excludes):
        return False
    return True


def _provided_lines(parsed) -> Sequence[str]:
    items = list(getattr(parsed, "added_lines", None) or [])
    lines = []
    for item in items:
        text = str(getattr(item, "content", "") or "")
        if text.strip():
            lines.append(text)
    return lines


def classify_risk(diff: str, parsed=None) -> Dict[str, Any]:
    """Classify a diff into a structured risk profile.

    Returns ``{level, domains, agents, signal_codes, confidence, rationale}``.
    ``level`` is the high-water severity floor across matched signals (low /
    medium / high / critical).  A high-level diff is dual-routed by the planner.
    """
    all_lines = _provided_lines(parsed)
    sections: List[RiskSignal] = []
    low_holder: str = (diff or "").lower()
    added_blob = ("\n".join(all_lines)).lower()
    # Signals that are meaningful only on added lines must not fire on deleted
    # lines, so prefer scanning the added-line blob; fall back to the full diff
    # when the diff parser produced no added lines.
    blob = added_blob if added_blob else low_holder

    security_hits: List[str] = []
    reliability_hits: List[str] = []
    levels: List[str] = []
    for signal in SECURITY_SIGNALS + RELIABILITY_SIGNALS:
        if _hits(signal, blob):
            sections.append(signal)
            levels.append(signal.severity_floor)
            if signal.code == "EXTERNAL_INPUT_TO_SINK" or signal.routes_security:
                security_hits.append(signal.code)
            elif signal.routes_reliability:
                reliability_hits.append(signal.code)
            else:
                # family-level confinement without a positive route driver;
                # still surface the code in signal_codes but don't force a route.
                security_hits.append(signal.code)

    _rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    level = max(levels, key=lambda s: _rank.get(s, 0)) if levels else "low"
    if not reliability_hits:
        reliability_hits = [
            s.code for s in sections if s.domain == RELIABILITY]
    domains: List[str] = []
    if security_hits:
        domains.append(SECURITY)
    if reliability_hits:
        domains.append(RELIABILITY)
    if not domains:
        domains = [RELIABILITY]  # every PR keeps a lightweight reliability pass

    # Plan §2 / §Phase 2: a high-risk (or critical) diff is dual-routed so a
    # security specialist can never be silently dropped.  The profiler surfaces
    # this so the legacy v1 ``_build_graph`` (which reads ``risk.agents``) and
    # the v2 planner both honour the specialist suggestions.
    if level in ("high", "critical"):
        domains = [d for d in (SECURITY, RELIABILITY) if d not in domains] + domains

    agents: List[str] = []
    if SECURITY in domains:
        agents.append(_AGENT_SECURITY)
    if RELIABILITY in domains:
        agents.append(_AGENT_RELIABILITY)
    if not agents:
        agents = [_AGENT_RELIABILITY]

    security_conf = sum(1 for s in sections if s.domain == SECURITY)
    reliability_conf = sum(1 for s in sections if s.domain == RELIABILITY)
    confidence = min(0.99, 0.5 + 0.1 * (security_conf + reliability_conf))

    return {
        "level": level,
        "domains": domains,
        "agents": agents,
        "signal_codes": sorted({s.code for s in sections}),
        "security_hits": sorted(set(security_hits)),
        "reliability_hits": sorted(set(reliability_hits)),
        "confidence": round(confidence, 3),
        "rationale": {
            "signals": sorted({s.code for s in sections}),
            "count": len(sections),
        },
    }


__all__ = [
    "RiskSignal", "classify_risk", "SECURITY_SIGNALS", "RELIABILITY_SIGNALS",
    "SECURITY", "RELIABILITY", "SHARED", "AGENT_SECURITY", "AGENT_RELIABILITY",
    "CWE_TO_DOMAIN", "cwe_domain",
    "should_route_security", "should_route_reliability",
]
AGENT_SECURITY = _AGENT_SECURITY
AGENT_RELIABILITY = _AGENT_RELIABILITY


# --------------------------------------------------------------------------- #
# Shared planner routing predicates (plan §3.2 / §Phase 2).
#
# Both the SemanticPlanner and the FallbackPlanner must make *identical* routing
# decisions from the structured risk profile -- the profiler's ``agents`` +
# ``domains`` are strong inputs, and a high-risk diff is dual-routed so a
# security specialist can never be silently dropped by re-deriving the domain
# from the summary alone.
# --------------------------------------------------------------------------- #
def _level(risk: Dict[str, Any]) -> str:
    return str((risk or {}).get("level") or "low")


def _agents(risk: Dict[str, Any], summary: Dict[str, Any]) -> set:
    return set((risk or {}).get("agents") or []) | set((summary or {}).get("agents") or [])


def _domains(risk: Dict[str, Any], summary: Dict[str, Any]) -> set:
    return set((risk or {}).get("domains") or []) | set((summary or {}).get("domains") or [])


def _change_types(summary: Dict[str, Any]) -> set:
    return set((summary or {}).get("change_types") or [])


def should_route_security(risk: Dict[str, Any], summary: Dict[str, Any] = None) -> bool:
    agents = _agents(risk, summary)
    domains = _domains(risk, summary)
    if AGENT_SECURITY in agents or SECURITY in domains:
        return True
    change_types = _change_types(summary)
    if change_types & {"security", "sql", "authentication", "input"}:
        return True
    if (summary or {}).get("new_external_inputs"):
        return True
    # High-risk diffs are dual-routed (plan §2 / §Phase 2).
    if _level(risk) in {"high", "critical"}:
        return True
    return False


def should_route_reliability(risk: Dict[str, Any], summary: Dict[str, Any] = None) -> bool:
    agents = _agents(risk, summary)
    domains = _domains(risk, summary)
    if AGENT_RELIABILITY in agents or RELIABILITY in domains:
        return True
    change_types = _change_types(summary)
    if change_types & {"exception", "concurrency", "resource", "runtime",
                       "control-flow"}:
        return True
    # High-risk diffs are dual-routed (plan §2 / §Phase 2): security and
    # reliability specialists both get a chance to inspect the change.
    if _level(risk) in {"high", "critical"}:
        return True
    # A genuinely clean / no-signal diff is *not* reliability-routed here; the
    # planner adds the lightweight CLEAN_BASELINE pass instead (plan §Phase 2).
    return False