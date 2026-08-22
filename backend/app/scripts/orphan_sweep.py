"""Manual CLI variant of the Phase 10.5 orphan sweep (app/worker/tasks/
integrity.py), sharing the exact same collect_known_keys/check_db_to_storage/
check_storage_to_db core logic — one real implementation, not two that could
silently drift apart.

Without --delete, only reports findings (identical to what the Celery task
already does nightly) — this script exists for on-demand runs and for the
one capability the report-only task deliberately never has: deleting a
storage-to-DB orphan an admin has reviewed and decided is safe to remove.
DB-to-storage findings (a DB row pointing at a missing object) are never
auto-deleted by this script either — that's a data-loss risk needing a
human decision about the affected row, not a bulk action.

Usage:
    python -m app.scripts.orphan_sweep                  # report only
    python -m app.scripts.orphan_sweep --delete <key> [<key> ...]
        Deletes the exact storage_to_db-direction key(s) given (must
        currently appear as a storage_to_db finding for the backend
        implied by --backend) — never a bulk "delete everything found"
        action, so a stale finding from a moment ago can't wipe out an
        object that became legitimately referenced in the meantime.
"""

import argparse

from app.services.storage.registry import get_storage_backend
from app.worker.tasks.integrity import run_sweep


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--delete",
        nargs="+",
        metavar="KEY",
        help="Delete these exact storage-to-DB orphan keys (must currently be reported as findings)",
    )
    parser.add_argument("--backend", default="vps_minio", help="Backend the --delete keys belong to (default: vps_minio)")
    args = parser.parse_args()

    result = run_sweep()

    print(f"known_key_count: {result.known_key_count}")
    print(f"listed_object_count: {result.listed_object_count}")
    print(f"regenerated_prefix_object_count: {result.regenerated_prefix_object_count}")
    print(f"finding_count: {len(result.findings)}")
    for f in result.findings:
        print(f"  [{f.direction}] {f.backend}/{f.bucket}/{f.key} — {f.detail}")

    if not args.delete:
        return

    deletable = {
        f.key: f
        for f in result.findings
        if f.direction == "storage_to_db" and f.backend == args.backend
    }
    storage = get_storage_backend(args.backend)
    for key in args.delete:
        finding = deletable.get(key)
        if finding is None:
            print(f"SKIPPED (not a current storage_to_db finding for backend={args.backend}): {key}")
            continue
        storage.delete(finding.bucket, key)
        print(f"DELETED: {finding.bucket}/{key}")


if __name__ == "__main__":
    main()
