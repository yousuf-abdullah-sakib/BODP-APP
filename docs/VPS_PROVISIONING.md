# VPS Provisioning Checklist

Reference: Master Plan §4 (sizing) and §3 Phase 1 task 8. Follow in order —
each step depends on the one before it.

Related docs: `docs/ENVIRONMENT_VARIABLES.md` (every setting referenced
below, in full), `docs/DISASTER_RECOVERY.md` (what to do if this VPS, its
database, or its object storage is lost), `docs/LOAD_TESTING.md` (re-run
after any scaling change), `docs/SECURITY_AUDIT.md`, `docs/TESTING.md`.

## 1. Provision the VPS

Minimum spec (Master Plan §4):
- **CPU:** 8 vCPU
- **RAM:** 32GB (64GB preferred once kriging/large-grid interpolation is active)
- **Disk:** 500GB+ NVMe SSD, separate volume for Postgres data if the provider supports it
- **Network:** generous/unmetered bandwidth (downloads + cloud-tier proxying are bandwidth-heavy)
- **OS:** Ubuntu 22.04 LTS or 24.04 LTS

## 2. Base OS hardening

- [ ] Create a non-root sudo user; disable root SSH login
- [ ] SSH key-only auth; disable password auth in `/etc/ssh/sshd_config`
- [ ] `ufw` firewall: allow only 22 (SSH, ideally from a restricted IP range), 80, 443 — everything else (5432, 6379, 9000/9001, 8000) stays internal-only, reachable purely via the Docker network
- [ ] Unattended security updates (`unattended-upgrades`)
- [ ] `fail2ban` for SSH brute-force protection

## 3. Domain & DNS

- [ ] Register/point the domain's `A` record (and `AAAA` if IPv6) at the VPS's public IP
- [ ] Confirm propagation (`dig +short yourdomain.org`) before requesting a certificate

## 4. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# log out/in for group membership to apply
docker compose version
```

## 5. Clone the repo and configure secrets

```bash
git clone <repo-url> bodp && cd bodp
cp .env.prod.example .env.prod
# Fill in every REPLACE_ME value — see .env.prod.example for the full list.
# Generate strong secrets, e.g.:
openssl rand -hex 32   # for JWT_SECRET_KEY
openssl rand -hex 24   # for POSTGRES_PASSWORD / MINIO_ROOT_PASSWORD
```

Set the real domain in `nginx/conf.d/bodp.conf` (replace `bodp.example.org`
in both `server_name` directives and the `ssl_certificate*` paths).

### Rotating secrets

A manual process — no automation is provided, since a secret rotation is
infrequent enough that automating it would add more risk (a broken
script mid-rotation) than it removes.

- **`JWT_SECRET_KEY`**: update `.env.prod`, restart the `backend` and
  `celery-worker*` services. Every currently active session is
  invalidated immediately — every logged-in user is signed out and must
  log in again. This is an expected, acceptable consequence of rotating
  this specific secret, not a bug to work around.
- **`POSTGRES_PASSWORD`** / **`MINIO_ROOT_PASSWORD`**: update `.env.prod`,
  then restart in this order — the credential-owning service first, then
  every service that connects to it: `postgres` (or `minio`) →
  `backend`, `celery-worker`, `celery-worker-ingestion`, `celery-beat`.
  Restarting a dependent service before the credential-owning one is
  updated will fail its next connection attempt — order matters here.
- **SMTP credentials**: update `.env.prod`, restart `backend` and the
  Celery workers (email sending happens from both). No downtime — the
  next send simply uses the new credentials; nothing holds a persistent
  SMTP connection between sends.

## 6. First-run TLS certificate (chicken-and-egg with Nginx)

Nginx's prod config expects certificates to already exist at
`/etc/letsencrypt/live/<domain>/`. Bootstrap them once before the first full
`docker compose up`:

```bash
# Bring up nginx alone first, serving only the ACME challenge path over HTTP
# (comment out the HTTPS server block temporarily, or use a minimal bootstrap
# config), then:
docker compose -f docker-compose.prod.yml run --rm certbot certonly \
  --webroot -w /var/www/certbot -d bodp.example.org \
  --email you@example.org --agree-tos --no-eff-email
```

After the certificate exists, restore the full `bodp.conf` (HTTPS block
included) and proceed to step 7. The `certbot` service in
`docker-compose.prod.yml` handles renewal automatically every 12h from then on.

## 7. Bring up the stack

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
docker compose -f docker-compose.prod.yml ps   # confirm all services healthy
```

The backend's start command runs `alembic upgrade head` automatically before
starting Gunicorn, so migrations apply on every deploy without a manual step.

## 8. Verify

- [ ] `curl https://yourdomain.org/api/health` returns `{"status":"ok",...}`
- [ ] `docker compose -f docker-compose.prod.yml logs backend` shows no errors
- [ ] HTTPS certificate is valid (browser padlock / `curl -vI`)
- [ ] Port scan confirms 5432/6379/9000/9001/8000 are NOT reachable from outside the VPS

## 9. Backups

- [ ] Confirm `bodp_pgdata` and `bodp_minio_data` volumes are on durable storage
- Automated nightly `pg_dump` backups (3 AM UTC), retention, and a real restore-verification path are wired up as of Phase 10.3 — see the admin "Backups & Recovery" section, `app/worker/tasks/backups.py`, and `app/scripts/test_backup_restore.py`. A nightly orphan sweep (Phase 10.5, 4 AM UTC) additionally checks for storage/DB drift across every storage-key-bearing table, backups included.
- Object storage itself has no off-VPS destination yet (MinIO's volume is the only copy unless the optional cloud storage tier is configured — provider selection deferred per Master Plan §5) — see `docs/DISASTER_RECOVERY.md` for what this does and doesn't cover.

## 10. Ongoing operational notes

- Redeploy: `git pull && docker compose -f docker-compose.prod.yml up -d --build`
- Logs: `docker compose -f docker-compose.prod.yml logs -f <service>`
- Scale Celery workers if background jobs queue up under load: `docker compose -f docker-compose.prod.yml up -d --scale celery-worker=3`
- If Celery workers become the bottleneck under Phase 10 load testing, prefer a second dedicated worker VPS over vertically scaling this one (Master Plan §4 bump path)
- Load testing: `backend/scripts/load_test.py` (Phase 10.6) — re-run after any scaling change to confirm the new configuration actually sustains real traffic; see `docs/LOAD_TESTING.md` for usage and the last captured pre-launch results.
