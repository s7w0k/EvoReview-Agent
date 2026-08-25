"""Import labelled public GitHub PR diffs into the Evaluation Harness JSONL format
or directly into the EvoAgent store as `source="github-real"` evaluation cases.

The manifest must contain repository, pull_request, split, expected_findings and
repair_validation. Public PR content alone is not ground truth, so unlabelled
records are intentionally rejected.

Manifest lines may carry optional `category` (clean/refactor/large/multi-language
or any other bucket label) and `language`; both are stored for later bucketing.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evoagent.diff_parser import parse_unified_diff  # noqa: E402
from evoagent.evaluation_harness import validate_case  # noqa: E402

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def validate_source(record: dict) -> None:
    """Reject records without an immutable production source."""
    repository = str(record.get("repository", "")).strip()
    try:
        pull_request = int(record.get("pull_request", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("pull_request must be an integer") from exc
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("repository must be 'owner/name': %r" % repository)
    if pull_request <= 0:
        raise ValueError("pull_request must be positive")
    source_uri = str(record.get("source_uri", "")).strip()
    if not source_uri.startswith("https://"):
        raise ValueError("a production source URI is required")


def check_label_locations(record: dict, parsed) -> None:
    """Ensure every human label points to a real added line."""
    valid = {(line.path, line.line) for line in parsed.added_lines}
    for finding in record["expected_findings"]:
        location = (str(finding["path"]), int(finding["start_line"]))
        if location not in valid:
            raise ValueError("label %s:%s does not point to an added line" % location)


def deduplicate_by_diff(records: list) -> list:
    """Drop records whose diff content was already seen (keeps first)."""
    seen = set()
    unique = []
    for record in records:
        digest = hashlib.sha256(record["diff"].encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        unique.append(record)
    return unique


def dataset_version_fingerprint(records: list) -> str:
    """Stable version fingerprint over the imported records' immutable content."""
    canonical = [
        {
            "repository": record.get("repository"),
            "pull_request": record.get("pull_request"),
            "split": record.get("split"),
            "diff": record.get("diff"),
            "expected_findings": record.get("expected_findings"),
        }
        for record in sorted(records, key=lambda r: (r.get("repository", ""), r.get("pull_request", 0)))
    ]
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_no_repo_leakage(records: list) -> None:
    """Forbid the same repository from appearing in validation and holdout."""
    by_repo = {}
    for record in records:
        by_repo.setdefault(record.get("repository"), set()).add(record.get("split"))
    leaks = sorted(
        repo for repo, splits in by_repo.items()
        if {"validation", "holdout"} <= splits
    )
    if leaks:
        raise ValueError(
            "repository leakage across validation/holdout: %s" % ", ".join(leaks)
        )


def fetch_diff(repository, pull_request, token=""):
    url = "https://api.github.com/repos/%s/pulls/%d" % (repository, pull_request)
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "evoagent-evaluation-importer",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace"), url
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            "GitHub returned HTTP %d for %s#%d"
            % (exc.code, repository, pull_request)
        ) from exc


def to_store_expected(record: dict) -> list:
    """Convert harness expected_findings into the store's evaluation schema.

    The store replays with {path, line, min_severity, rule_id}; the harness
    manifest uses {path, start_line, end_line, cwe, severity}.  rule_id is
    optional: empty when the human label did not pin a specific rule.
    """
    expected = []
    for finding in record["expected_findings"]:
        expected.append({
            "path": str(finding["path"]),
            "line": int(finding["start_line"]),
            "min_severity": str(finding["severity"]),
            "rule_id": str(finding.get("rule_id", "")),
        })
    return expected


def derive_category(record: dict) -> str:
    """Category bucket label: manifest-provided label wins, else clean/risk."""
    explicit = str(record.get("category", "")).strip()
    if explicit:
        return explicit
    return "clean" if not record["expected_findings"] else "risk"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", help="Labelled JSONL manifest")
    parser.add_argument("output", help="Evaluation JSONL output")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--db", default="", metavar="URL",
        help=(
            "Optional store target: a SQLite file path or a postgres:// URL. "
            "When set, each labelled record is also written into evaluation_cases "
            "with source='github-real' and its category label."
        ),
    )
    parser.add_argument("--suite-id", default="real-validation", help="Dataset suite_id")
    parser.add_argument("--dataset-version", default="", help="Dataset version (default: content fingerprint)")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    records = []
    with open(args.manifest, "r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            item = json.loads(raw)
            if "expected_findings" not in item:
                raise ValueError(
                    "manifest line %d has no human-reviewed expected_findings"
                    % line_number
                )
            diff, _api_url = fetch_diff(
                str(item["repository"]), int(item["pull_request"]), token
            )
            parsed = parse_unified_diff(diff)
            public_url = "https://github.com/%s/pull/%d" % (
                item["repository"], int(item["pull_request"])
            )
            record = {
                "schema_version": 1,
                "id": item.get(
                    "id", "%s#%s" % (item["repository"], item["pull_request"])
                ),
                "repository": item["repository"],
                "pull_request": int(item["pull_request"]),
                "split": item["split"],
                "source": {"kind": "github-real", "public_url": public_url},
                "source_uri": public_url,
                "suite_id": args.suite_id,
                "labeler_ids": list(item.get("labeler_ids", [])),
                "label_schema_version": str(item.get("label_schema_version", "")),
                "diff": diff,
                "after_files": item.get("after_files", {}),
                "expected_findings": item["expected_findings"],
                "repair_validation": item.get("repair_validation", {}),
                # Work Package 8: bucket labels for per-repository/clean/refactor/
                # large/multi-language statistics.
                "category": str(item.get("category", "")).strip() or derive_category(item),
                "language": str(item.get("language", "")).strip(),
            }
            validate_source(record)
            validate_case(record)
            if not parsed.added_lines:
                raise ValueError("PR %s has no added lines" % record["id"])
            check_label_locations(record, parsed)
            records.append(record)
            if len(records) >= args.limit:
                break
    records = deduplicate_by_diff(records)
    assert_no_repo_leakage(records)
    dataset_version = args.dataset_version or dataset_version_fingerprint(records)[:16]
    if len(records) < args.limit:
        raise ValueError(
            "manifest produced %d records; %d required" % (len(records), args.limit)
        )
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    print("wrote %d labelled public PRs to %s" % (len(records), args.output))
    if args.db:
        from evoagent.postgres_store import create_store
        store = create_store(args.db, args.db if not args.db.startswith("postgres") else "")
        written = skipped = 0
        for record in records:
            try:
                store.save_evaluation_case(
                    record["id"], record["split"], record["diff"],
                    to_store_expected(record), "github-real", True,
                    record["category"],
                    suite_id=record.get("suite_id", ""),
                    dataset_version=dataset_version,
                    repository=record.get("repository", ""),
                    language=record.get("language", ""),
                    source_uri=record.get("source_uri", ""),
                    labeler_ids=record.get("labeler_ids", []),
                    label_schema_version=record.get("label_schema_version", ""),
                )
                written += 1
            except ValueError:
                # Immutable-name collision with identical content is a no-op.
                skipped += 1
        print(
            "wrote %d / %d evaluation cases into %s (source=github-real; %d unchanged)"
            % (written, len(records), args.db, skipped)
        )


if __name__ == "__main__":
    main()
