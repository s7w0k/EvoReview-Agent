"""Base class for persistent repositories.

Every repository wraps a ``JSONFileStore`` table and exposes the same small
surface: ``save``, ``record``/``find``, ``all``, ``delete`` and ``count``.
Repositories subclass this and groups records by a natural key (task_id, policy
id, run id, ...).
"""
from typing import Any, Dict, List, Optional

from ...storage.json_store import JSONFileStore


class PersistentRepository:
    """A repository backed by one table in a :class:`JSONFileStore`."""

    table: str = "records"

    def __init__(self, store: JSONFileStore):
        self.store = store

    def save(self, record_id: str, record: Any) -> Any:
        self.store.save(self.table, record_id, record)
        return record

    def record(self, record_id: str) -> Optional[Any]:
        return self.store.get(self.table, record_id)

    def all(self) -> List[Any]:
        return self.store.all(self.table)

    def count(self) -> int:
        return self.store.count(self.table)

    def delete(self, record_id: str) -> bool:
        return self.store.delete(self.table, record_id)

    def _by(self, key: str, value: Any) -> List[Any]:
        return [item for item in self.all() if str(item.get(key, "")) == str(value)]


__all__ = ["PersistentRepository"]