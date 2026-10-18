from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_connect_args = {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
# FastAPI runs each request on a thread pool (40 threads); with SQLite's default 5+10 connections, busy
# moments queued requests for up to 30 s. SQLite connections are cheap, so allow plenty.
_pool = {"pool_size": 20, "max_overflow": 40, "pool_timeout": 60} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, **_pool)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    add_missing_columns()


def add_missing_columns() -> list[str]:
    """Minimal forward migration: add columns that newer versions define but an older
    database lacks. New columns are always nullable, so this is safe on existing rows."""
    from sqlalchemy import inspect, text

    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'))
                added.append(f"{table.name}.{col.name}")
    return added


def get_session() -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session
