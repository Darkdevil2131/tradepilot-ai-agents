# Production Runbook

## Persistence

Set `TRADEPILOT_DATABASE_URL` to a PostgreSQL connection string for shipment,
commercial-workflow, job and audit persistence. If it is absent, the service
uses SQLite and reports `sqlite_ephemeral_or_local`. A PostgreSQL label means
the backend is PostgreSQL; durability still depends on the provider's backup
and retention configuration.

## Rate limits and jobs

Set `REDIS_URL` for a shared multi-replica rate limit. Without it the health
response reports `process_local`. Document jobs persist their status and result,
but execution currently uses the API worker's background-task facility. A
worker crash can leave a job queued; an external worker/queue should reclaim
stale jobs before operating multiple replicas.

## Backup and recovery

SQLite administrators can call `POST /api/v1/admin/backups`; copy the returned
artifact to storage outside the container. PostgreSQL deployments must use
provider-managed backups or scheduled `pg_dump`, encrypt backups, restrict
access, and perform quarterly restore tests. A backup is not considered valid
until a restore test can read shipment revisions and audit events.

Recovery order:

1. Stop writes or put the API in maintenance mode.
2. Restore the database into a separate instance.
3. Run `/api/v1/ready` and retrieve a known shipment and audit event.
4. Compare record counts and the latest revision timestamps.
5. Switch traffic, monitor errors, and retain the previous instance for rollback.

## Monitoring

Scrape authenticated `GET /api/v1/metrics`. Example alert rules are in
`monitoring/alerts.yml`. Monitor readiness, HTTP 5xx rate, latency, storage
capacity, queued jobs, database connections and backup freshness.

## External actions

RFQ delivery uses an internal outbox. SMTP is disabled by default. Enable it
only with `TRADEPILOT_SMTP_ENABLED=true` plus host, sender and credentials.
Booking and purchase-order objects are approval-gated records; the repository
contains no external carrier booking or ERP posting adapter.
