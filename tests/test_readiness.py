"""Fail-closed operational health vs readiness checks."""
from sqlalchemy import create_engine, text

from im19_mcp.readiness import database_ready


def test_uninitialized_database_is_not_ready(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "uninitialized.sqlite3"))
    assert database_ready(engine) is False


def test_schema_revision_must_be_exact(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "revisions.sqlite3"))
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0003_refresh_leases')"))
    assert database_ready(engine) is False
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num='0005_distributed_quotas'"))
    assert database_ready(engine) is True
    assert database_ready(engine, "unexpected-version") is False
