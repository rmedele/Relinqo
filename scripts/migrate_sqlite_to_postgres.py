"""Copy Relinqo data from SQLite into an empty PostgreSQL database.

Usage:
    python scripts/migrate_sqlite_to_postgres.py \
      --sqlite-url sqlite:///./data/leadrelay.db \
      --postgres-url postgresql://user:pass@host:5432/leadrelay

Run `alembic upgrade head` against the Postgres database before this script.
"""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine

from app.config import normalized_database_url
from app.database import Base
from app import models  # noqa: F401 - register all tables


def _engine(url: str) -> Engine:
    normalized = normalized_database_url(url)
    connect_args = {"check_same_thread": False} if normalized.startswith("sqlite") else {}
    return create_engine(normalized, connect_args=connect_args, pool_pre_ping=True)


def _table_has_rows(engine: Engine, table_name: str) -> bool:
    with engine.connect() as conn:
        count = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
    return count > 0


def _reset_sequence(pg: Engine, table_name: str, pk_name: str) -> None:
    with pg.begin() as conn:
        conn.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence(:table_name, :pk_name),
                    COALESCE((SELECT MAX(id) FROM public.""" + table_name + """), 1),
                    true
                )
                """
            ),
            {"table_name": table_name, "pk_name": pk_name},
        )


def migrate(sqlite_url: str, postgres_url: str, truncate: bool) -> None:
    sqlite = _engine(sqlite_url)
    pg = _engine(postgres_url)
    source_inspector = inspect(sqlite)
    source_tables = set(source_inspector.get_table_names())

    if truncate:
        table_names = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
        if table_names:
            with pg.begin() as conn:
                conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))

    for table in Base.metadata.sorted_tables:
        if table.name not in source_tables:
            print(f"{table.name}: source table missing, skipped")
            continue

        if _table_has_rows(pg, table.name):
            raise RuntimeError(
                f"Postgres table {table.name!r} already has rows. "
                "Use --truncate if you intentionally want to replace target data."
            )

        source_columns = {col["name"] for col in source_inspector.get_columns(table.name)}
        copy_columns = [col for col in table.columns if col.name in source_columns]
        if not copy_columns:
            print(f"{table.name}: no matching source columns, skipped")
            continue

        with sqlite.connect() as source:
            rows = [dict(row._mapping) for row in source.execute(select(*copy_columns)).all()]

        if not rows:
            print(f"{table.name}: 0 rows")
            continue

        with pg.begin() as target:
            target.execute(table.insert(), rows)
        print(f"{table.name}: copied {len(rows)} rows")

        pk_cols = list(table.primary_key.columns)
        if len(pk_cols) == 1 and pk_cols[0].name == "id":
            _reset_sequence(pg, table.name, "id")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-url", default="sqlite:///./data/leadrelay.db")
    parser.add_argument("--postgres-url", required=True)
    parser.add_argument("--truncate", action="store_true")
    args = parser.parse_args()
    migrate(args.sqlite_url, args.postgres_url, args.truncate)


if __name__ == "__main__":
    main()
