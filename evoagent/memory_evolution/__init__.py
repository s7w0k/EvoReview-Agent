"""Evolvable memory capability (plan section 11).

This is distinct from the tenant-aware ``evoagent.memory`` runtime store: this
package models memory as an *evolvable metric* -- entries carry learnable
usefulness metadata, are retrieved through a filtered, usefulness-weighted
pipeline, and have their usefulness updated by evolution feedback.
"""
from .manager import MemoryEntry, MemoryManager
from .feedback import MemoryOutcomeFeedback, MemoryUseTracker
from .retrieval import (
    MemoryReranker,
    MemoryRetriever,
    RelevanceScorer,
    RetrievedMemory,
    bm25_relevance,
    token_overlap_relevance,
)
from .utility import (
    MemoryMetadata,
    compute_usefulness,
    memory_score,
    record_helpful,
    record_unhelpful,
)

__all__ = [
    "MemoryEntry",
    "MemoryManager",
    "MemoryMetadata",
    "MemoryOutcomeFeedback",
    "MemoryReranker",
    "MemoryRetriever",
    "MemoryUseTracker",
    "RelevanceScorer",
    "RetrievedMemory",
    "bm25_relevance",
    "compute_usefulness",
    "memory_score",
    "record_helpful",
    "record_unhelpful",
    "token_overlap_relevance",
]