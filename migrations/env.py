from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from tradehub_data.core.config import get_settings
from tradehub_data.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DATABASE_URL_ATTRIBUTE = "tradehub_data_database_url"


def get_url() -> str:
    explicit_url = config.attributes.get(DATABASE_URL_ATTRIBUTE)
    if explicit_url is not None:
        if not isinstance(explicit_url, str) or not explicit_url.strip():
            raise RuntimeError(f"Alembic attribute {DATABASE_URL_ATTRIBUTE!r} must be a non-empty string")
        return explicit_url
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
