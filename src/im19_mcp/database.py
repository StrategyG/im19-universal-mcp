"""Consistent, bounded database admission for remote workers and diagnostics."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


def database_engine(url: str, **kwargs):
    options = {"pool_pre_ping": True, "pool_recycle": 300, **kwargs}
    if make_url(url).drivername.startswith("postgresql"):
        options.setdefault("connect_args", {
            "connect_timeout": 5,
            "options": "-c statement_timeout=10000 -c lock_timeout=3000",
        })
        if options.get("poolclass") is not NullPool:
            options.setdefault("pool_timeout", 5)
    return create_engine(url, **options)
