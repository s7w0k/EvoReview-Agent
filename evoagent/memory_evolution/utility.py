"""Memory usefulness scoring (plan section 11.3-11.4).

Memory entries carry learnable metadata.  The retrieval score is a product of
relevance, usefulness, confidence and freshness, and *evolution feedback* moves
usefulness up when a memory helps find a verified finding and down when it is
used but did not help.
"""
from dataclasses import dataclass
from typing import Dict


@dataclass
class MemoryMetadata:
    """Learnable usage statistics attached to a memory entry."""

    success_count: int = 0
    failure_count: int = 0
    last_used_at: float = 0.0
    usefulness_score: float = 1.0
    confidence: float = 1.0
    source_type: str = ""
    source_version: str = ""

    def to_dict(self) -> Dict:
        return {
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_used_at": self.last_used_at,
            "usefulness_score": self.usefulness_score,
            "confidence": self.confidence,
            "source_type": self.source_type,
            "source_version": self.source_version,
        }


def compute_usefulness(success_count: int, failure_count: int) -> float:
    """usefulness = success / (success + failure + 1)."""
    return success_count / (success_count + failure_count + 1)


def memory_score(
    relevance: float,
    usefulness: float,
    confidence: float,
    freshness: float,
) -> float:
    """Product-based memory score (section 11.3)."""
    return relevance * usefulness * confidence * freshness


def record_helpful(metadata: MemoryMetadata) -> MemoryMetadata:
    """Bump usefulness after a verified-finding success."""
    metadata.success_count += 1
    metadata.usefulness_score = compute_usefulness(
        metadata.success_count, metadata.failure_count)
    return metadata


def record_unhelpful(metadata: MemoryMetadata) -> MemoryMetadata:
    """Lower usefulness when a memory was used but did not help."""
    metadata.failure_count += 1
    metadata.usefulness_score = compute_usefulness(
        metadata.success_count, metadata.failure_count)
    return metadata