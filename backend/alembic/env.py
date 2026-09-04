"""Alembic environment.

The database URL comes from settings rather than alembic.ini, so migrations and
the application can never disagree about which database they are talking to.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.base import ANALYTICS_SCHEMA, IDENTITY_SCHEMA, Base  # noqa: E402
from app.models import *  # noqa: E402, F401, F403  -- registers every table

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

MANAGED_SCHEMAS = {IDENTITY_SCHEMA, ANALYTICS_SCHEMA, None}


def include_object(obj, name, type_, reflected, compare_to):
    """Keep PostGIS's own tables out of autogenerate.

    Installing PostGIS creates `spatial_ref_sys` and friends in the public
    schema. Without this filter, autogenerate proposes dropping them on every
    run.
    """
    if type_ == "table":
        if name in {"spatial_ref_sys", "geography_columns", "geometry_columns"}:
            return False
        schema = getattr(obj, "schema", None)
        return schema in MANAGED_SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=None,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
