"""Alembic upgrade smoke test against a fresh durable SQLite database."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_full_upgrade_including_confirmations_and_refresh_leases(tmp_path, monkeypatch):
    database_url = "sqlite:///" + str(tmp_path / "migrations.sqlite3")
    monkeypatch.setenv("IM19_DATABASE_URL", database_url)
    project_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    command.upgrade(cfg, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert {
        "oauth_states", "oauth_credentials", "oauth_pending_links",
        "audit_events", "alembic_version",
    }.issubset(set(schema.get_table_names()))
    columns = {col["name"] for col in schema.get_columns("oauth_credentials")}
    assert {"refresh_owner", "refresh_until"}.issubset(columns)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0003_refresh_leases"
        )
    engine.dispose()
