"""Metadata-filtered memory retrieval (plan section 11.1-11.2).

Following the plan, retrieval is layered: a metadata filter runs first, then a
lexical relevance scorer, then a usefulness weight.  The default relevance here
is deterministic token overlap; a richer embedding scorer can be injected.
"""
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from .utility import MemoryMetadata, memory_score

# relevance scorers: (query_text, entry_text) -> 0..1
RelevanceScorer = Callable[[str, str], float]


def token_overlap_relevance(query: str, text: str) -> float:
    """Deterministic lexical overlap in 0..1."""
    q = set(_tokens(query))
    if not q:
        return 0.0
    doc = set(_tokens(text))
    if not doc:
        return 0.0
    return len(q & doc) / len(q)


def _tokens(text: str) -> List[str]:
    return [w for w in text.lower().replace("_", " ").split() if w]


@dataclass
class RetrievedMemory:
    memory_id: str
    content: str
    metadata: MemoryMetadata
    score: float

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "score": round(self.score, 4),
            "metadata": self.metadata.to_dict(),
        }


class MemoryRetriever:
    """Filters by metadata, then ranks by the usefulness-weighted score."""

    def __init__(self,
                 relevance_scorer: RelevanceScorer = token_overlap_relevance,
                 *,
                 recency_decay: float = 0.0):
        self._relevance = relevance_scorer
        self._decay = recency_decay

    def retrieve(
        self,
        query: str,
        candidates: Sequence,
        *,
        limit: int = 5,
        source_type: Optional[str] = None,
        min_confidence: float = 0.0,
        now: float = 0.0,
    ) -> List[RetrievedMemory]:
        """``candidates`` is a sequence of ``(memory_id, content, metadata)``."""
        results = []
        for memory_id, content, metadata in candidates:
            if source_type is not None and metadata.source_type != source_type:
                continue
            if metadata.confidence < min_confidence:
                continue
            relevance = self._relevance(query, content)
            if relevance <= 0:
                continue
            freshness = self._freshness(metadata, now)
            score = memory_score(
                relevance,
                metadata.usefulness_score,
                metadata.confidence,
                freshness,
            )
            results.append(RetrievedMemory(
                memory_id=memory_id, content=content,
                metadata=metadata, score=round(score, 4)))

        results.sort(key=lambda item: item.score, reverse=True)
        return results[:limit]

    def _freshness(self, metadata: MemoryMetadata, now: float) -> float:
        if self._decay <= 0 or now <= 0 or metadata.last_used_at <= 0:
            return 1.0
        age = max(0.0, now - metadata.last_used_at)
        return max(0.0, 1.0 - self._decay * age)