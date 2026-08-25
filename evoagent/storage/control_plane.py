"""Durable Control Plane (doc: EvoReview-Agent_Final_Engineering_Hardening_Plan).

Phase 4-7 abstract the control-plane persistence behind a backend-agnostic
:class:`ControlPlaneStore`.  Repositories must not know whether they are backed
by a JSON file, SQLite or PostgreSQL, so this module also exposes a legacy
``JSONFileStore``-compatible surface (``save/get/all/count/delete/save_many/
get_many/tables``) implemented on top of the protocol methods.  Existing
``storage/repositories/*`` therefore keep working with every backend unchanged.

Backends (selected via ``CONTROL_PLANE_BACKEND``):
- ``json``    -> :class:`JSONControlPlaneStore`      (smoke / debug / test fallback)
- ``sqlite``  -> :class:`SQLiteControlPlaneStore`    (default single-process dev)
- ``postgres``-> :class:`PostgresControlPlaneStore`  (recommended production)
"""
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Protocol

# Name of the single generic control-plane table shared by the SQL backends.
# ``collection`` mirrors the JSON store's table name and ``key`` its record id.
_GENERIC_TABLE = "control_records"


class ControlPlaneStore(Protocol):
    """Minimal durable control-plane surface (plan section 7/Phase 4)."""

    def get(self, collection: str, key: str) -> Optional[Any]: ...
    def put(self, collection: str, key: str, value: Any) -> Any: ...
    def delete(self, collection: str, key: str) -> bool: ...
    def list(self, collection: str) -> List[Any]: ...
    def transaction(self) -> Any: ...


class _LegacyStoreMixin:
    """Adapt the protocol methods onto the historical JSONFileStore surface.

    ``storage/repositories/base.PersistentRepository`` and friends call exactly
    these methods, so any control-plane store is a drop-in replacement.
    """

    def put(self, collection: str, key: str, value: Any) -> Any:
        raise NotImplementedError

    def list(self, collection: str) -> List[Any]:
        raise NotImplementedError

    def list_all(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def save(self, table: str, record_id: str, record: Any) -> Any:
        return self.put(table, record_id, record)

    def save_many(self, table: str, items: Dict[str, Any]) -> None:
        for key, value in items.items():
            self.put(table, key, value)

    def all(self, table: str) -> List[Any]:
        return self.list(table)

    def count(self, table: str) -> int:
        return len(self.list(table))

    def tables(self) -> List[str]:
        return sorted({v["_collection"] for v in self.list_all()})


class JSONControlPlaneStore(_LegacyStoreMixin):
    """Thread-safe, JSON-file-backed control plane (dev/test fallback)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        # data[collection][key] = value
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

    def _flush(self) -> None:
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

    # ---- protocol ----
    def get(self, collection: str, key: str) -> Optional[Any]:
        with self._lock:
            return self._data.get(collection, {}).get(key)

    def put(self, collection: str, key: str, value: Any) -> Any:
        with self._lock:
            self._data.setdefault(collection, {})[key] = value
            self._flush()
        return value

    def delete(self, collection: str, key: str) -> bool:
        with self._lock:
            bucket = self._data.get(collection)
            if bucket is None or key not in bucket:
                return False
            del bucket[key]
            self._flush()
            return True

    def list(self, collection: str) -> List[Any]:
        with self._lock:
            return list(self._data.get(collection, {}).values())

    def get_many(self, table: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data.get(table, {}))

    def list_all(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with self._lock:
            for collection, bucket in self._data.items():
                for key, value in bucket.items():
                    out.append({"_collection": collection, "_key": key, "value": value})
        return out

    @contextmanager
    def transaction(self) -> Iterator["JSONControlPlaneStore"]:
        # JSON has no multi-record atomicity; the in-process lock bounds races for
        # single-writer deployments (silently degraded, but never corrupted).
        with self._lock:
            yield self


class SQLiteControlPlaneStore(_LegacyStoreMixin):
    """Durable single-process control plane backed by SQLite (WAL mode).

    A single generic ``control_records(collection, key, value_json)`` table is
    used so the document blobs stored by the repositories are backend-portable.
    WAL improves concurrent readers and ``transaction()`` gives atomic
    Promote / Rollback batches (Phase 5 requirement).
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._init()

    def _connect(self):
        import sqlite3
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS %s ("
                " collection TEXT NOT NULL,"
                " key TEXT NOT NULL,"
                " value_json TEXT NOT NULL,"
                " PRIMARY KEY(collection, key))" % _GENERIC_TABLE
            )

    def _roundtrip(self, value: Any) -> Any:
        # JSON round-trip produces plain dicts/lists so repositories observe the
        # same structures regardless of backend.
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))

    # ---- protocol ----
    def get(self, collection: str, key: str) -> Optional[Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM %s WHERE collection=? AND key=?" % _GENERIC_TABLE,
                (collection, key),
            ).fetchone()
        return json.loads(row["value_json"]) if row is not None else None

    def put(self, collection: str, key: str, value: Any) -> Any:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO %s(collection,key,value_json) VALUES (?,?,?) "
                "ON CONFLICT(collection,key) DO UPDATE SET value_json=excluded.value_json"
                % _GENERIC_TABLE,
                (collection, key, payload),
            )
        return value

    def delete(self, collection: str, key: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM %s WHERE collection=? AND key=?" % _GENERIC_TABLE,
                (collection, key),
            )
            return cursor.rowcount > 0

    def list(self, collection: str) -> List[Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT value_json FROM %s WHERE collection=? ORDER BY key" % _GENERIC_TABLE,
                (collection,),
            ).fetchall()
        return [json.loads(row["value_json"]) for row in rows]

    def list_all(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT collection,key,value_json FROM %s" % _GENERIC_TABLE
            ).fetchall()
        return [{"_collection": r["collection"], "_key": r["key"],
                 "value": json.loads(r["value_json"])} for r in rows]

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Yield a write proxy whose changes commit or roll back atomically."""
        conn = self._connect()
        beginning = False
        try:
            conn.isolation_level = None
            conn.execute("BEGIN")
            beginning = True

            class _Tx:
                def put(self, collection: str, key: str, value: Any) -> Any:
                    payload = json.dumps(value, ensure_ascii=False, default=str)
                    conn.execute(
                        "INSERT INTO %s(collection,key,value_json) VALUES (?,?,?) "
                        "ON CONFLICT(collection,key) DO UPDATE SET value_json=excluded.value_json"
                        % _GENERIC_TABLE,
                        (collection, key, payload),
                    )
                    return value

                def delete(self, collection: str, key: str) -> bool:
                    cursor = conn.execute(
                        "DELETE FROM %s WHERE collection=? AND key=?" % _GENERIC_TABLE,
                        (collection, key),
                    )
                    return cursor.rowcount > 0

            yield _Tx()
            conn.execute("COMMIT")
            beginning = False
        except BaseException:
            if beginning:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            raise
        finally:
            conn.close()

    # ---- legacy get_many override ----
    def get_many(self, table: str) -> Dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key,value_json FROM %s WHERE collection=?" % _GENERIC_TABLE,
                (table,),
            ).fetchall()
        return {r["key"]: json.loads(r["value_json"]) for r in rows}


class PostgresControlPlaneStore(_LegacyStoreMixin):
    """Recommended production control plane backed by PostgreSQL (JSONB).

    ``transaction()`` issues a real SQL transaction so Promote / Rollback are
    atomic and multi-instance readers share the same ACTIVE deployment.
    ``psycopg[binary]`` is an optional dependency (mirrors postgres_store).
    """

    def __init__(self, dsn: str):
        self._init_psycopg()
        self.dsn = dsn
        self._init_schema()

    def _init_psycopg(self) -> None:
        try:
            import psycopg
            from psycopg import sql
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL control plane requires: pip install psycopg[binary]"
            ) from exc
        self.psycopg = psycopg
        self.sql = sql
        self.dict_row = dict_row

    def _connect(self):
        return self.psycopg.connect(self.dsn, row_factory=self.dict_row)

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS %s ("
                " collection TEXT NOT NULL, key TEXT NOT NULL,"
                " value_json JSONB NOT NULL,"
                " PRIMARY KEY(collection, key))" % _GENERIC_TABLE
            )

    def _serialize(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    def _deserialize(self, payload: Any) -> Any:
        return json.loads(payload) if isinstance(payload, str) else payload

    # ---- protocol ----
    def get(self, collection: str, key: str) -> Optional[Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT value_json FROM %s WHERE collection=%s AND key=%s"
                % (_GENERIC_TABLE, "%s", "%s"),
                (collection, key),
            ).fetchone()
        return self._deserialize(row["value_json"]) if row else None

    def put(self, collection: str, key: str, value: Any) -> Any:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO %s(collection,key,value_json) VALUES (%s,%s,%s::jsonb) "
                "ON CONFLICT(collection,key) DO UPDATE SET value_json=excluded.value_json"
                % (_GENERIC_TABLE, "%s", "%s", "%s"),
                (collection, key, self._serialize(value)),
            )
        return value

    def delete(self, collection: str, key: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM %s WHERE collection=%s AND key=%s"
                % (_GENERIC_TABLE, "%s", "%s"),
                (collection, key),
            )
            return cursor.rowcount > 0

    def list(self, collection: str) -> List[Any]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT value_json FROM %s WHERE collection=%s ORDER BY key"
                % (_GENERIC_TABLE, "%s"),
                (collection,),
            ).fetchall()
        return [self._deserialize(r["value_json"]) for r in rows]

    def get_many(self, table: str) -> Dict[str, Any]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT key,value_json FROM %s WHERE collection=%s" % (_GENERIC_TABLE, "%s"),
                (table,),
            ).fetchall()
        return {r["key"]: self._deserialize(r["value_json"]) for r in rows}

    def list_all(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT collection,key,value_json FROM %s" % _GENERIC_TABLE
            ).fetchall()
        return [{"_collection": r["collection"], "_key": r["key"],
                 "value": self._deserialize(r["value_json"])} for r in rows]

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        tx = self._connect()

        class _Tx:
            def put(self, collection: str, key: str, value: Any) -> Any:
                tx.execute(
                    "INSERT INTO %s(collection,key,value_json) VALUES (%s,%s,%s::jsonb) "
                    "ON CONFLICT(collection,key) DO UPDATE SET value_json=excluded.value_json"
                    % (_GENERIC_TABLE, "%s", "%s", "%s"),
                    (collection, key, json.dumps(value, ensure_ascii=False, default=str)),
                )
                return value

            def delete(self, collection: str, key: str) -> bool:
                cursor = tx.execute(
                    "DELETE FROM %s WHERE collection=%s AND key=%s"
                    % (_GENERIC_TABLE, "%s", "%s"),
                    (collection, key),
                )
                return cursor.rowcount > 0

        try:
            yield _Tx()
            tx.commit()
        except BaseException:
            tx.rollback()
            raise
        finally:
            tx.close()


def create_control_plane_store(settings) -> ControlPlaneStore:
    """Select a durable control-plane backend from Settings.

    Backend is chosen by ``CONTROL_PLANE_BACKEND``:
      - ``sqlite`` (default): single-process development / deployment backend
      - ``postgres``: recommended production backend (uses database_url)
      - ``json``: smoke / debug / test fallback
    """
    backend = getattr(settings, "control_plane_backend", "sqlite") or "sqlite"
    backend = backend.strip().lower()
    if backend == "postgres":
        dsn = getattr(settings, "database_url", "") or ""
        if not dsn:
            raise ValueError(
                "CONTROL_PLANE_BACKEND=postgres requires EVOAGENT_DATABASE_URL"
            )
        return PostgresControlPlaneStore(dsn)
    if backend == "json":
        path = getattr(settings, "control_plane_path", None) or (
            "%s.control.json" % getattr(settings, "db_path", "evoagent")
        )
        return JSONControlPlaneStore(path)
    # default: sqlite
    path = getattr(settings, "control_plane_path", None) or (
        "%s.control.sqlite" % getattr(settings, "db_path", "evoagent")
    )
    return SQLiteControlPlaneStore(path)


__all__ = [
    "ControlPlaneStore",
    "JSONControlPlaneStore",
    "SQLiteControlPlaneStore",
    "PostgresControlPlaneStore",
    "create_control_plane_store",
]