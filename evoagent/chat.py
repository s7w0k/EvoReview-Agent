"""Shared schema constants for the report-chat feature.

Work Package 0 freezes the set of states, categories, citation types, field
lengths and normalization helpers used across the chat working package.  These
are deliberately kept independent of any model or store implementation so every
layer (API, service, store, frontend) agrees on the same vocabulary.

The insight categories intentionally reuse the semantics defined in
``evoagent.experience`` so that confirmed chat insights can flow into the
existing feedback chain without introducing a parallel taxonomy.
"""
import hashlib
import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .experience import (
    ACCEPTED,
    BAD_FIX,
    FALSE_POSITIVE,
    MISSED_ISSUE,
    mask_secrets,
)


# --- Session lifecycle -----------------------------------------------------
CHAT_SESSION_ACTIVE = "active"
CHAT_SESSION_STALE = "stale"
CHAT_SESSION_ARCHIVED = "archived"
CHAT_SESSION_STATUSES = frozenset(
    {CHAT_SESSION_ACTIVE, CHAT_SESSION_STALE, CHAT_SESSION_ARCHIVED}
)

# --- Message lifecycle -----------------------------------------------------
CHAT_MESSAGE_PENDING = "pending"
CHAT_MESSAGE_COMPLETED = "completed"
CHAT_MESSAGE_FAILED = "failed"
CHAT_MESSAGE_STATUSES = frozenset(
    {CHAT_MESSAGE_PENDING, CHAT_MESSAGE_COMPLETED, CHAT_MESSAGE_FAILED}
)

# --- Insight lifecycle -----------------------------------------------------
# ``confirming`` is an internal transient state used only to make the
# confirm->feedback hand-off atomic; it is never surfaced to clients.
CHAT_INSIGHT_DRAFT = "draft"
CHAT_INSIGHT_CONFIRMING = "confirming"
CHAT_INSIGHT_CONFIRMED = "confirmed"
CHAT_INSIGHT_REJECTED = "rejected"
CHAT_INSIGHT_SUPERSEDED = "superseded"
CHAT_INSIGHT_STATUSES = frozenset(
    {
        CHAT_INSIGHT_DRAFT,
        CHAT_INSIGHT_CONFIRMING,
        CHAT_INSIGHT_CONFIRMED,
        CHAT_INSIGHT_REJECTED,
        CHAT_INSIGHT_SUPERSEDED,
    }
)

# --- Insight categories (reuse feedback semantics) -------------------------
CHAT_INSIGHT_CATEGORIES = frozenset(
    {FALSE_POSITIVE, MISSED_ISSUE, BAD_FIX, ACCEPTED}
)

# --- Citation types --------------------------------------------------------
CITATION_REPORT = "report"
CITATION_FINDING = "finding"
CITATION_DIFF = "diff"
CITATION_TRACE = "trace"
CITATION_MEMORY = "memory"
CITATION_TYPES = frozenset(
    {CITATION_REPORT, CITATION_FINDING, CITATION_DIFF, CITATION_TRACE, CITATION_MEMORY}
)

# --- Field length limits ---------------------------------------------------
SESSION_TITLE_MAX = 200
REPOSITORY_MAX = 200
MESSAGE_CONTENT_MAX = 8000
INSIGHT_NOTE_MAX = 2000
CLIENT_REQUEST_ID_MAX = 128
PROVIDER_MAX = 64
MODEL_MAX = 128
PROMPT_VERSION_MAX = 128
SOURCE_KEY_PREFIX = "chat_insight"


def normalize_text(value: str, max_length: int) -> str:
    """Strip control-free whitespace and truncate to ``max_length``."""
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if len(text) > max_length:
        text = text[:max_length]
    return text


def normalize_identifier(value: str, max_length: int) -> str:
    """Trim an identifier-like field without collapsing internal whitespace."""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > max_length:
        text = text[:max_length]
    return text


def insight_source_key(insight_id: str) -> str:
    """Stable idempotency key used on ``failure_cases.source_key``."""
    return "%s:%s" % (SOURCE_KEY_PREFIX, insight_id)


def is_valid_category(category: str) -> bool:
    return category in CHAT_INSIGHT_CATEGORIES


# =============================================================================
# Work Package 2: report context, structured-model-output validation and a
# thin OpenAI-compatible transport.  Nothing here touches the review pipeline,
# the evolution engine or the existing feedback chain.
# =============================================================================

# --- context build limits (kept as module constants so WP2 is independently
# --- testable; they may move to Settings in a later work package) ----------
CHAT_MAX_FINDINGS = 20
CHAT_MAX_DIFF_LINES = 120
CHAT_MAX_TRACE_ITEMS = 30
CHAT_MEMORY_LIMIT = 4
CHAT_CONTEXT_TOKENS = 10000
CHAT_MAX_OUTPUT_TOKENS = 1600
CHAT_TIMEOUT_SECONDS = 60


class ChatModelNotConfigured(Exception):
    """Raised when no LLM is configured but a chat reply is requested."""


class ChatModelError(Exception):
    """Stable domain error wrapping transport/parse failures.

    ``reason`` is one of: timeout | http | invalid_output | rate_limit.
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail


class ChatBusyError(Exception):
    """A chat request for the same session is already in flight (WP6 6.2)."""


# Work Package 6: hard caps on model-derived output (defaults; the service
# overrides them from Settings when building the transport).
CHAT_MAX_CITATIONS = 20
CHAT_MAX_INSIGHTS = 10
CHAT_MAX_JSON_DEPTH = 20


def report_fingerprint(report: Any) -> str:
    """Stable SHA-256 over the canonical JSON of a task report."""
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def context_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ChatModelClient:
    """Minimal OpenAI-compatible chat transport.

    Deliberately independent of ``OpenAICompatibleReviewer`` (which binds its
    payload to the Finding output protocol).  Reuses ``settings.resolved_llm()``
    fields: base_url, api_key, model, provider, headers, timeout.
    """

    def __init__(
        self, base_url: str, api_key: str, model: str, provider: str = "custom",
        headers: Optional[Dict[str, str]] = None, timeout: int = CHAT_TIMEOUT_SECONDS,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.provider = provider
        self.extra_headers = headers or {}
        self.timeout = timeout
        # Work Package 7: token usage from the last completed response, so the
        # service can persist and expose it without changing complete()'s API.
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def complete(
        self, system: str, messages: List[Dict[str, str]],
        max_output_tokens: int = CHAT_MAX_OUTPUT_TOKENS,
    ) -> str:
        if not self.base_url or not self.api_key or not self.model:
            raise ChatModelNotConfigured("chat model is not configured")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "max_tokens": int(max_output_tokens),
            "temperature": 0.2,
        }
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers.update(self.extra_headers)
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ChatModelError("rate_limit", "HTTP %d" % exc.code) from exc
            raise ChatModelError("http", "HTTP %d" % exc.code) from exc
        except (urllib.error.URLError, socket.timeout) as exc:
            raise ChatModelError("timeout", str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise ChatModelError("invalid_output", str(exc)) from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatModelError("invalid_output", "missing assistant content") from exc
        if not isinstance(content, str):
            raise ChatModelError("invalid_output", "assistant content is not text")
        # Work Package 7: capture token usage (missing usage is tolerated).
        usage = body.get("usage") if isinstance(body, dict) else None
        if isinstance(usage, dict):
            self.last_usage = {
                "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            }
        return content


class ChatContextBuilder:
    """Assemble a bounded, tenant/task-scoped context for a chat turn.

    Returns a dict with ``text``, ``references``, ``report_fingerprint``,
    ``context_fingerprint`` and ``truncation``.  All report/diff/trace/memory/
    history content is treated as untrusted data and kept out of any system
    prompt control region (the caller owns the system prompt).
    """

    def __init__(
        self, max_findings: int = CHAT_MAX_FINDINGS,
        max_diff_lines: int = CHAT_MAX_DIFF_LINES,
        max_trace_items: int = CHAT_MAX_TRACE_ITEMS,
        memory_limit: int = CHAT_MEMORY_LIMIT,
        context_tokens: int = CHAT_CONTEXT_TOKENS,
    ):
        self.max_findings = max_findings
        self.max_diff_lines = max_diff_lines
        self.max_trace_items = max_trace_items
        self.memory_limit = memory_limit
        self.context_tokens = context_tokens

    def build(
        self, *, repository: str, risk: str, report: dict,
        findings: List[dict], added_lines: List[dict], trace: List[dict],
        memories: List[dict], question: str, history: List[dict],
    ) -> dict:
        truncation = {"findings": 0, "diff_lines": 0, "trace_items": 0,
                      "memories": 0, "history": 0}
        references: List[dict] = [{"type": CITATION_REPORT, "ref": "report"}]

        # 1) Report facts.
        lines = ["# Task report", "repository: %s" % repository, "risk level: %s" % risk]
        summary = (report or {}).get("summary")
        if summary:
            lines.append("summary: %s" % str(summary)[:2000])

        # 2) Findings (with references).
        lines.append("# Findings")
        for index, finding in enumerate(findings[: self.max_findings]):
            references.append({
                "type": CITATION_FINDING, "ref": "finding:%d" % index,
                "rule_id": finding.get("rule_id"),
                "path": finding.get("path"), "line": finding.get("line"),
            })
            lines.append(
                "finding:%d [%s] %s at %s:%s conf=%.2f evidence=%s"
                % (index, finding.get("rule_id"), finding.get("title"),
                   finding.get("path"), finding.get("line"),
                   float(finding.get("confidence", 0)),
                   str(finding.get("evidence", ""))[:240])
            )
        if len(findings) > self.max_findings:
            truncation["findings"] = len(findings) - self.max_findings

        # 3) Added diff lines (evidence) with references.
        lines.append("# Added diff lines")
        for index, change in enumerate(added_lines[: self.max_diff_lines]):
            references.append({
                "type": CITATION_DIFF, "ref": "diff:%s:%s" % (change.get("path"), change.get("line")),
                "path": change.get("path"), "line": change.get("line"),
            })
            lines.append("diff:%s:%s\t+%s" % (
                change.get("path"), change.get("line"), change.get("content", "")))
        if len(added_lines) > self.max_diff_lines:
            truncation["diff_lines"] = len(added_lines) - self.max_diff_lines

        # 4) Trace/collaboration summary (never the raw unbounded payload).
        lines.append("# Trace summary")
        for index, item in enumerate(trace[: self.max_trace_items]):
            references.append({"type": CITATION_TRACE, "ref": "trace:%d" % index})
            lines.append("trace:%d\t%s" % (index, item.get("summary", str(item)[:120])))
        if len(trace) > self.max_trace_items:
            truncation["trace_items"] = len(trace) - self.max_trace_items

        # 5) Recalled memories (untrusted).
        lines.append("# Relevant memories")
        for index, memory in enumerate(memories[: self.memory_limit]):
            references.append({"type": CITATION_MEMORY, "ref": "memory:%d" % index})
            lines.append("memory:%d\t%s" % (index, str(memory.get("content", ""))[:200]))
        if len(memories) > self.memory_limit:
            truncation["memories"] = len(memories) - self.memory_limit

        # 6) Recent history (shallow, bounded).
        lines.append("# Prior conversation")
        for index, message in enumerate(history[-6:]):
            role = message.get("role", "?")
            content = str(message.get("content", ""))[:400]
            lines.append("%d %s: %s" % (index, role, content))
        if len(history) > 6:
            truncation["history"] = len(history) - 6

        text = "\n".join(lines)

        # Question is always preserved and given priority.
        text = "# Question\n%s\n\n%s" % (question, text)
        if len(text) > self.context_tokens * 4:
            text = text[: self.context_tokens * 4]

        return {
            "text": text,
            "references": references,
            "report_fingerprint": report_fingerprint(report),
            "context_fingerprint": context_fingerprint(text),
            "truncation": truncation,
        }


def validate_citations(
    citations: Any, references: List[dict], max_citations: int = CHAT_MAX_CITATIONS,
) -> list:
    """Keep only citations backed by a real context reference, up to a cap."""
    by_finding = {r["ref"]: r for r in references if r.get("type") == CITATION_FINDING}
    by_diff = {
        (r.get("path"), r.get("line")): r for r in references if r.get("type") == CITATION_DIFF
    }
    by_ref = {r["ref"]: r for r in references if r.get("type") in {
        CITATION_TRACE, CITATION_MEMORY, CITATION_REPORT}}
    valid: List[dict] = []
    for raw in citations if isinstance(citations, list) else []:
        if len(valid) >= max(1, int(max_citations)):
            break
        if not isinstance(raw, dict):
            continue
        ctype = raw.get("type")
        if ctype == CITATION_FINDING:
            if raw.get("ref") in by_finding:
                valid.append({"type": ctype, "ref": raw["ref"]})
        elif ctype == CITATION_DIFF:
            key = (raw.get("path"), raw.get("line"))
            if key in by_diff:
                valid.append({"type": ctype, "path": key[0], "line": key[1]})
        elif ctype in {CITATION_TRACE, CITATION_MEMORY, CITATION_REPORT}:
            if raw.get("ref") in by_ref:
                valid.append({"type": ctype, "ref": raw["ref"]})
    return valid


def validate_insights(
    insights: Any, findings: List[dict], max_insights: int = CHAT_MAX_INSIGHTS,
) -> list:
    """Sanitize model-suggested candidate conclusions.

    Category must be a known feedback category; confidence is clamped to [0, 1];
    a finding reference, when present, must point at a real finding in the
    report (the full finding is reconstructed server-side, never trusted from
    the model).  Invalid positions produce no high-confidence rule candidate.
    Notes are masked for known secret formats and capped in count.
    """
    valid: List[dict] = []
    for raw in insights if isinstance(insights, list) else []:
        if len(valid) >= max(1, int(max_insights)):
            break
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category", ""))
        if category not in CHAT_INSIGHT_CATEGORIES:
            continue
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        finding_ref = raw.get("finding_ref")
        finding = None
        if finding_ref and isinstance(finding_ref, str) and finding_ref.startswith("finding:"):
            try:
                index = int(finding_ref.split(":", 1)[1])
                finding = findings[index]
            except (ValueError, IndexError):
                finding = None
        note = mask_secrets(normalize_text(str(raw.get("note", "")), INSIGHT_NOTE_MAX))
        valid.append({
            "category": category,
            "finding_ref": finding_ref if finding is not None else None,
            "finding": finding,
            "note": note,
            "confidence": confidence,
        })
    return valid


def _json_depth(value: Any, depth: int = 0) -> int:
    if not isinstance(value, (dict, list)):
        return depth
    if isinstance(value, dict):
        return max([depth] + [_json_depth(v, depth + 1) for v in value.values()])
    return max([depth] + [_json_depth(v, depth + 1) for v in value])


def decode_model_output(
    content: str, references: List[dict], findings: List[dict],
    insights_enabled: bool,
    max_citations: int = CHAT_MAX_CITATIONS,
    max_insights: int = CHAT_MAX_INSIGHTS,
) -> dict:
    """Parse and validate the structured JSON from the model.

    Returns ``{"answer", "citations", "insights", "invalid_citation_count"}``.
    Raises ChatModelError on malformed output, oversized JSON depth or over-long
    fields.  Insights are dropped entirely when disabled.
    """
    try:
        result = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ChatModelError("invalid_output", "non-JSON response") from exc
    if not isinstance(result, dict):
        raise ChatModelError("invalid_output", "non-object JSON response")
    if _json_depth(result) > CHAT_MAX_JSON_DEPTH:
        raise ChatModelError("invalid_output", "JSON nesting too deep")
    answer_raw = result.get("answer")
    answer = str(answer_raw)[:MESSAGE_CONTENT_MAX] if answer_raw is not None else ""
    raw_citations = result.get("citations")
    citations = validate_citations(raw_citations, references, max_citations)
    insights = validate_insights(
        result.get("insights"), findings, max_insights) if insights_enabled else []
    raw_count = len(raw_citations) if isinstance(raw_citations, list) else 0
    return {
        "answer": answer,
        "citations": citations,
        "insights": insights,
        "invalid_citation_count": max(0, raw_count - len(citations)),
    }