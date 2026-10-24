# IM19 — Distributed Admission Control (Phase 7)

**Implementation status:** database-backed admission quotas for production
gateway socket-peer addresses and authenticated MCP tool subjects. Tested
against PostgreSQL 16, including concurrent transactions. Not a DDoS shield
or an independently validated production capacity rating.

## Trust boundaries

- Pre-auth HTTP requests: `scope["client"][0]` is the immediate network
  peer only. **Never trust X-Forwarded-For, Forwarded, or user-supplied
  headers**; Uvicorn is configured with `proxy_headers=False`.
- Behind a TLS reverse proxy, all requests can appear from the **same proxy
  IP**. The resulting quota is shared between clients. Configure real-client
  limits at the trusted HTTPS ingress (WAF/LB) with a verified proxy chain,
  then use IM19 as a separate protective quota layer.
- Tool-level quotas use only the verified MCP token's `sub`, never a user,
  Google account or owner ID provided by the model.
- HMAC-SHA256 keyed by IM19 audit salt pseudonymizes socket IPs and subjects
  in SQL; the database stores **no raw client IP, Authorization header or
  OAuth token**.
- Clock-aligned fixed windows allow bursts around minute boundaries. Use
  edge protection, budget limits, alerting and network controls for volumetric
  attacks. Synchronize gateway worker clocks using NTP.

## Limits

Production defaults:
- HTTP socket-peer: 120 requests per 60-second fixed window, shared across
  all replicas using PostgreSQL. Excludes only `/health` and `/ready`
  (load balancer probes); ingress must protect these paths too.
- Verified MCP tool principal: 60 read requests per minute, aggregated across
  YouTube/Drive. This is independent of the HTTP socket-peer budget.
- OAuth consent starts: max 5 per verified MCP subject per minute.
- OAuth code confirmations: max 10 per verified MCP subject per minute.
- MCP connection status: max 60 per verified subject per minute.

The gateway enforces these **without model-visible bypasses**. If quota
storage fails, HTTP returns **503 QUOTA_UNAVAILABLE**, not an unmetered
request. MCP tools return `SERVICE_UNAVAILABLE` on quota storage failure.
A 429 HTTP response includes a bounded `Retry-After`; a tool-limited
response uses `RATE_LIMITED` with a retry hint.

PostgreSQL executes a single conditional UPSERT with `RETURNING`; it
atomically increments the shared counter only while below its maximum.
No process-local buckets are used in production. SQLite support exists for
local development tests only, not for multi-instance production.

## Installation and operations

1. Upgrade database with `alembic upgrade head`. Required revision:
   `0005_distributed_quotas`.
2. Configure the existing independent MCP OAuth issuer, HTTPS proxy and
   secrets manager. Production config requires PostgreSQL.
3. Monitor HTTP 429s, quota DB 503s, DB connection pool saturation and
   per-user denial rates. Avoid logging raw subject IDs, client IPs or
   confirmation codes. No dashboard or alert system is deployed by this PR.
4. Schedule `im19-purge-expired` from a trusted backend job; it deletes
   expired shared rate-window rows alongside expired OAuth and approvals.
5. Stress-test beyond the small PostgreSQL CI worker concurrency tests,
   including distributed latency, database failover and clock skew.
6. Do not interpret successful unit/CI checks as Google/ChatGPT live
   integration or production acceptance.

## Scope

This is **shared application-layer throttling**, not a full zero-trust policy
engine, network DoS prevention, risk scoring or per-tenant billing.
No external provider mutations are enabled.
