from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


engine = _make_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


SCHEMA_VERSION = "2"  # bump when models change; the PoC resets its database instead of migrating


def init_db() -> None:
    import logging
    import shutil

    from sqlalchemy import inspect, text

    from app import models  # noqa: F401  (register tables)

    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tables = inspect(engine).get_table_names()
    current = None
    if "eval_dg_meta" in tables:
        with engine.connect() as conn:
            current = conn.execute(text("SELECT value FROM eval_dg_meta WHERE key='schema'")).scalar()
    if tables and current != SCHEMA_VERSION:
        logging.getLogger(__name__).warning("Schema changed (%s -> %s): resetting the PoC database and uploads.",
                                            current, SCHEMA_VERSION)
        Base.metadata.drop_all(engine)
        with engine.begin() as conn:  # tables from older versions that are no longer mapped
            for t in inspect(engine).get_table_names():
                conn.execute(text(f'DROP TABLE IF EXISTS "{t}"'))
        shutil.rmtree(settings.UPLOAD_DIR / "tenders", ignore_errors=True)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT OR REPLACE INTO eval_dg_meta (key, value) VALUES ('schema', :v)"), {"v": SCHEMA_VERSION})


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
