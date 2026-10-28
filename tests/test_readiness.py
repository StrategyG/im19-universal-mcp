"""Fail-closed operational health vs readiness checks."""
from sqlalchemy import create_engine, text

from im19_mcp.readiness import database_ready
from im19_mcp.storage import Base


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
        conn.execute(text("UPDATE alembic_version SET version_num='0007_browser_inspection_jobs'"))
    assert database_ready(engine) is False  # a marker without tables is insufficient
    Base.metadata.create_all(engine)
    assert database_ready(engine) is True
    assert database_ready(engine, "unexpected-version") is False
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE oauth_credentials RENAME COLUMN refresh_owner TO missing_owner"))
    assert database_ready(engine) is False

