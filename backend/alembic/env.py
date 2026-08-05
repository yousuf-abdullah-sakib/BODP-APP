from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.database import Base
from app.models import *  # noqa: F401,F403  (populate Base.metadata with every model)

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# PostGIS (and extensions like tiger geocoder, topology) create many of their
# own tables in the public schema. Without this filter, autogenerate would
# propose dropping all of them since they're not part of our declarative
# models. Only our own app tables should ever appear in a migration diff.
_OUR_TABLES = set(target_metadata.tables.keys())


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in _OUR_TABLES
    if type_ in ("column", "index", "unique_constraint", "foreign_key_constraint"):
        table_name = obj.table.name if hasattr(obj, "table") else None
        return table_name is None or table_name in _OUR_TABLES
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=False,
        include_object=include_object,
        compare_server_default=True,
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
            compare_type=True,
            include_object=include_object,
            # Without this, autogenerate silently ignores server_default
            # changes entirely — which is exactly how a bare-string
            # server_default="now()" bug (frozen literal timestamp instead
            # of a live function call) went undetected until a test caught
            # it at runtime. Comparing defaults explicitly surfaces this
            # class of bug at migration-generation time instead.
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
