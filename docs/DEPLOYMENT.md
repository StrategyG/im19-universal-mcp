# Deployment (staging first)

## Local development
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
im19-mcp
```

Without IM19_DEV_SUBJECT, provider tools correctly return AUTH_REQUIRED.

## Database and secrets

Use environment variables from .env.example; generate a Fernet key securely,
and set a separately random audit salt. Do not commit .env.

```bash
alembic upgrade head
```

The im19-init-db command invokes metadata.create_all for local development
only; for staging and production, use Alembic migrations.

## Docker

```bash
export POSTGRES_PASSWORD='choose-a-long-secret'
docker compose build
docker compose up -d postgres
# Apply migrations from a controlled environment with the production DB URL.
docker compose up -d gateway
```

Terminate TLS at a trusted ingress, configure an explicit hostname allowlist
and test token introspection, request Origin rules, and health checks before
exposing the server to the internet. Docker Compose alone is not a finished
production deployment.

## Release gates

- Python matrix tests and Docker image build must pass.
- No production credentials in untrusted pull requests or CI logs.
- OAuth callback, refresh, revocation, cross-user isolation and 429 behavior
  exercised in staging.
- Backup/restore validated before handling real customer data.
- Scope least privilege and app verification reviewed.
