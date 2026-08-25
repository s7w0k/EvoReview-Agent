"""Generic durable JSON store used by the storage repositories.

Phase 5 replaces scattered in-memory repositories with genuine persistence so a
worker restart can resume from the last durable state.  ``JSONFileStore`` is a
dependency-free, table-oriented document store: each table is a dict of
``id -> record`` and the whole store is flushed to a single JSON file on every
mutation, atomically (tmp-file + rename).  PostgreSQL backends can swap this
for an SQL store without changing repository code.
"""
import json
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional


class JSONFileStore:
    """Thread-safe, JSON-file-backed store with per-table namespaces."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                self._data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def flush(self) -> None:
        with self._lock:
            directory = os.path.dirname(os.path.abspath(self.path)) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._data, handle, ensure_ascii=False,
                              sort_keys=True, separators=(",", ":"))
                os.replace(tmp, self.path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def save(self, table: str, record_id: str, record: Any) -> None:
        with self._lock:
            self._data.setdefault(table, {})[record_id] = record
            self.flush()

    def save_many(self, table: str, items: Dict[str, Any]) -> None:
        with self._lock:
            bucket = self._data.setdefault(table, {})
            bucket.update(items)
            self.flush()

    def get(self, table: str, record_id: str) -> Optional[Any]:
        with self._lock:
            return self._data.get(table, {}).get(record_id)

    def get_many(self, table: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data.get(table, {}))

    def all(self, table: str) -> List[Any]:
        with self._lock:
            return list(self._data.get(table, {}).values())

    def count(self, table: str) -> int:
        with self._lock:
            return len(self._data.get(table, {}))

    def delete(self, table: str, record_id: str) -> bool:
        with self._lock:
            bucket = self._data.get(table)
            if bucket is None or record_id not in bucket:
                return False
            del bucket[record_id]
            self.flush()
            return True

    def tables(self) -> List[str]:
        with self._lock:
            return sorted(self._data)


__all__ = ["JSONFileStore"]