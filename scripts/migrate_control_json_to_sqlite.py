"""Migrate the control plane from the legacy JSON file backend to SQLite.

Hardening plan Phase 5: ``JSON -> validate -> insert -> count verify ->
backup original``.  Safely copies every ``collection`` / ``key`` / record from a
``*.control.json`` file into a new ``*.control.sqlite`` database.  The JSON file
is never deleted, only renamed to a ``.backup`` sibling when completion is
verified.

Usage:
    python -m scripts.migrate_control_json_to_sqlite \
        --source control.json --dest control.sqlite [--keep-original]
"""
import argparse
import json
import os
import shutil
import sys


def _iter_records(source_path: str):
    with open(source_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("control JSON must be an object of {collection: {key: record}}")
    for collection, bucket in data.items():
        if not isinstance(bucket, dict):
            continue
        for key, record in bucket.items():
            yield collection, str(key), record


def _validate_record(value) -> None:
    # Records must survive a JSON round-trip to be stored in a SQL column.
    json.dumps(value, ensure_ascii=False, default=str)


def migrate(source_path: str, dest_path: str, *, keep_original: bool = False) -> None:
    from evoagent.storage.control_plane import SQLiteControlPlaneStore

    if not os.path.exists(source_path):
        raise FileNotFoundError("source control file not found: %s" % source_path)
    if os.path.exists(dest_path):
        raise ValueError("destination already exists: %s" % dest_path)

    dest = SQLiteControlPlaneStore(dest_path)
    loaded = 0
    for collection, key, record in _iter_records(source_path):
        _validate_record(record)
        dest.put(collection, key, record)
        loaded += 1

    # count verify: every source row must be visible through the SQL backend.
    migrated = 0
    for collection, key, _record in _iter_records(source_path):
        if dest.get(collection, key) is None:
            raise RuntimeError(
                "verification failed for %s/%s; dest left incomplete" % (collection, key)
            )
        migrated += 1
    if loaded != migrated:
        raise RuntimeError("count mismatch: loaded=%d verified=%d" % (loaded, migrated))

    if not keep_original:
        backup = source_path + ".backup"
        counter = 1
        while os.path.exists(backup):
            backup = "%s.backup.%d" % (source_path, counter)
            counter += 1
        shutil.move(source_path, backup)
        print("original control file backed up to: %s" % backup)

    print("migrated %d records -> %s" % (migrated, dest_path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="path to *.control.json")
    parser.add_argument("--dest", required=True, help="path to *.control.sqlite")
    parser.add_argument(
        "--keep-original", action="store_true",
        help="do not move the source file to a .backup sibling",
    )
    args = parser.parse_args(argv)
    migrate(args.source, args.dest, keep_original=args.keep_original)
    return 0


if __name__ == "__main__":
    sys.exit(main())