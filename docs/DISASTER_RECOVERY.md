# Disaster Recovery

Concrete recovery procedures for the three loss scenarios worth planning
for: full database loss, object storage loss/corruption, and full VPS
loss. Each states what's actually recoverable today, plainly, not hedged
— stated recovery time expectations reflect the current setup (no live
standby, single VPS), not an aspirational target.

## 1. Full database loss

**What's recoverable**: everything, up to the most recent nightly backup
(3 AM UTC) or a manual on-demand backup, via `pg_restore` from a real
`pg_dump` artifact stored in object storage. **What's lost**: any write
made between the last backup and the moment of loss (up to ~24 hours in
the worst case, on the default nightly-only schedule).

### Procedure

This is the same mechanism `app/scripts/test_backup_restore.py` already
runs routinely to verify every backup is genuinely restorable — pointed
at the real production database instead of a throwaway one. Steps below
were executed and confirmed working end-to-end against a real backup
during this phase's own testing.

1. **Identify the backup to restore.** Via the admin "Backups &
   Recovery" section, or directly:
   ```sql
   SELECT id, status, storage_key, size_bytes, started_at
   FROM backups WHERE status = 'success' ORDER BY started_at DESC LIMIT 5;
   ```
2. **Download the dump** from object storage using that row's
   `storage_key` (via the admin UI's download button, or `storage.get()`
   directly with the storage backend's own credentials — see
   `app/worker/tasks/backups.py` for the exact key layout,
   `backups/{backup_id}/pg_dump.dump`).
3. **Provision or reach the replacement Postgres instance** — same major
   version (`postgis/postgis:17-3.5`), since the dump is in `pg_dump`
   custom format (`-Fc`), not a plain-SQL dump.
4. **Restore**:
   ```bash
   pg_restore --host <new-db-host> --port 5432 --username bodp \
       --dbname bodp --no-owner --no-privileges /path/to/pg_dump.dump
   ```
   `--no-owner --no-privileges` matches exactly what
   `test_backup_restore.py` uses — the restored roles/grants come from
   the target database's own setup (via the same Alembic
   migrations/seed process), not baked into the dump.
5. **Verify row counts** against the backup's own `row_counts` column
   (captured at dump time, not a live query — comparing against a live
   query is inherently racy against any write between dump and
   verification, a false-positive mismatch this project hit once during
   its own testing before switching to this comparison). A handful of
   core tables (`users`, `datasets`, `dataset_files`) is the standing
   sanity check; a full application smoke test after restore is still
   the real confirmation.
6. **Point the application at the restored database** (`DATABASE_URL`/
   `DATABASE_URL_SYNC`) and restart every service that holds a
   connection pool (backend, celery-worker, celery-worker-ingestion,
   celery-beat).

No manual intervention beyond these steps is required — the procedure
above is exactly what was run to confirm this document, not a simplified
summary of a more complicated real process.

## 2. Object storage loss or corruption

**What's recoverable today: nothing**, if the MinIO data volume
(`bodp_minio_data`) is lost and no cloud storage tier is configured — the
VPS-local MinIO instance is the only copy of every raw dataset upload,
processed Parquet/Zarr artifact, extraction output, avatar, media asset,
and report. This is stated plainly because Master Plan §5 deliberately
leaves cloud-tier provider selection open — there is currently no
off-VPS replication of object storage, only of the database (via nightly
backups above).

**What a future cloud-storage tier would add**: `app/services/storage/
registry.py` already supports a second backend (`STORAGE_CLOUD_*`
settings) — once a provider is selected and configured, replicating
critical prefixes (`raw/`, `processed/`) to that tier would close this
gap. Until then, the only mitigation is what already exists:

- The database backup preserves every `storage_key` reference and
  dataset metadata — after a database restore, storage objects the
  database still references but that no longer exist would show up as
  real, findable `db_to_storage` orphan-sweep findings (Phase 10.5,
  `app/worker/tasks/integrity.py`) rather than a silent broken link — the
  gap is detectable, even though the underlying files are not
  recoverable without a separate copy.
- `DatasetFile.checksum` (captured at upload time) means a partially
  corrupted MinIO volume can at least be distinguished from a genuinely
  missing one — checksum-on-read (Phase 10.5) fails cleanly with a clear
  error rather than silently serving corrupted data.

## 3. Full VPS loss

**What's recoverable**: the application and database, from scratch, in
roughly the time it takes to re-provision + restore. **What's lost**:
the same object storage gap as scenario 2 above, plus any database
writes since the last backup (scenario 1).

### Procedure

1. Re-provision a new VPS following `docs/VPS_PROVISIONING.md` sections
   1–7 (base OS hardening, Docker install, repo clone, secrets, TLS,
   bring up the stack) — this produces a fresh, empty application with
   no data.
2. Restore the database following section 1 above, using the most
   recent backup (which must have been stored somewhere other than the
   lost VPS itself — an off-VPS destination for backup dumps, e.g.
   downloaded periodically to a separate location, is a real operational
   gap worth closing before this scenario is fully covered; the backup
   *mechanism* itself is real and tested, but nothing today copies the
   dump anywhere outside the VPS it runs on beyond the MinIO volume it's
   uploaded to).
3. Object storage is unrecoverable per scenario 2, unless a cloud tier
   was configured and separately backed up.

**Recovery time expectation**: no live standby exists — this is a cold
rebuild, not a failover. Re-provisioning (steps 1–7 of
`docs/VPS_PROVISIONING.md`) plus a database restore is realistically an
hours-scale operation, not minutes, given DNS propagation, TLS
certificate issuance, and manual verification steps in the provisioning
runbook. This is the honest current number, not a target to defend —
closing it further would mean a warm/hot standby, which is out of scope
for the current infrastructure.
