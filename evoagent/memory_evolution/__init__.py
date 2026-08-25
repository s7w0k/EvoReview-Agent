"""Evolvable memory capability (plan section 11).

This is distinct from the tenant-aware ``evoagent.memory`` runtime store: this
package models memory as an *evolvable metric* -- entries carry learnable
usefulness metadata, are retrieved through a filtered, usefulness-weighted
pipeline, and have their usefulness updated by evolution feedback.
"""
from .manager import MemoryEntry, MemoryManager
from .retrieval import (
    MemoryRetriever,
    RelevanceScorer,
    RetrievedMemory,
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
    "MemoryRetriever",
    "RelevanceScorer",
    "RetrievedMemory",
    "compute_usefulness",
    "memory_score",
    "record_helpful",
    "record_unhelpful",
    "token_overlap_relevance",
]