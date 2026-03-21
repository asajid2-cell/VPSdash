from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import PlatformConfig


def create_platform_engine(config: PlatformConfig) -> Engine:
    connect_args: dict[str, object] = {}
    if config.database_url.startswith("sqlite:///"):
        db_path = Path(config.database_url.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False
    return create_engine(config.database_url, future=True, connect_args=connect_args)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def apply_schema_migrations(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    statements: list[str] = []

    if "harminoplets" in tables and "doplets" not in tables:
        statements.append("ALTER TABLE harminoplets RENAME TO doplets")
        tables.discard("harminoplets")
        tables.add("doplets")

    if "backup_records" in tables:
        backup_columns = {column["name"] for column in inspector.get_columns("backup_records")}
        if "harminoplet_id" in backup_columns and "doplet_id" not in backup_columns:
            statements.append("ALTER TABLE backup_records RENAME COLUMN harminoplet_id TO doplet_id")

    if "snapshot_records" in tables:
        snapshot_columns = {column["name"] for column in inspector.get_columns("snapshot_records")}
        if "harminoplet_id" in snapshot_columns and "doplet_id" not in snapshot_columns:
            statements.append("ALTER TABLE snapshot_records RENAME COLUMN harminoplet_id TO doplet_id")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

        connection.execute(text("UPDATE tasks SET target_type = 'doplet' WHERE target_type = 'harminoplet'"))
        connection.execute(text("UPDATE tasks SET task_type = REPLACE(task_type, 'harminoplet', 'doplet') WHERE instr(task_type, 'harminoplet') > 0"))
        connection.execute(text("UPDATE audit_events SET target_type = 'doplet' WHERE target_type = 'harminoplet'"))
        connection.execute(text("UPDATE audit_events SET action = REPLACE(action, 'harminoplet', 'doplet') WHERE instr(action, 'harminoplet') > 0"))


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
