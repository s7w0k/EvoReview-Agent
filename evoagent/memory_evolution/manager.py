"""In-memory memory manager with evolution feedback (plan section 11).

Keeps the memory corpus, exposes retrieval, and lets the evolution loop update
usefulness --- turning memory itself into an evaluable agent capability.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .retrieval import MemoryRetriever, RetrievedMemory
from .utility import MemoryMetadata, record_helpful, record_unhelpful


@dataclass
class MemoryEntry:
    memory_id: str
    content: str
    metadata: MemoryMetadata = field(default_factory=MemoryMetadata)

    def to_dict(self) -> dict:
        return {"memory_id": self.memory_id, "content": self.content,
                "metadata": self.metadata.to_dict()}


class MemoryManager:
    """Stores memory entries and delegates retrieval."""

    def __init__(self, retriever: Optional[MemoryRetriever] = None):
        self._retriever = retriever or MemoryRetriever()
        self._entries: Dict[str, MemoryEntry] = {}

    def add(self, memory_id: str, content: str,
            metadata: Optional[MemoryMetadata] = None) -> MemoryEntry:
        entry = MemoryEntry(memory_id=memory_id, content=content,
                            metadata=metadata or MemoryMetadata())
        self._entries[memory_id] = entry
        return entry

    def get(self, memory_id: str) -> Optional[MemoryEntry]:
        return self._entries.get(memory_id)

    def search(self, query: str, **kwargs) -> List[RetrievedMemory]:
        candidates = [
            (entry.memory_id, entry.content, entry.metadata)
            for entry in self._entries.values()
        ]
        return self._retriever.retrieve(query, candidates, **kwargs)

    # -- evolution feedback -------------------------------------------------

    def confirm_helpful(self, memory_id: str) -> None:
        entry = self._ensure(memory_id)
        record_helpful(entry.metadata)

    def confirm_unhelpful(self, memory_id: str) -> None:
        entry = self._ensure(memory_id)
        record_unhelpful(entry.metadata)

    def _ensure(self, memory_id: str) -> MemoryEntry:
        entry = self._entries.get(memory_id)
        if entry is None:
            raise KeyError(f"no memory entry {memory_id!r}")
        return entry

    def __len__(self) -> int:
        return len(self._entries)