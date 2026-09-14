from alembic import context
from sqlalchemy import engine_from_config, pool

import bot_financeiro.persistence.categories  # noqa: F401
import bot_financeiro.persistence.credit_cards  # noqa: F401
import bot_financeiro.persistence.financial_operations  # noqa: F401
import bot_financeiro.persistence.payables  # noqa: F401
import bot_financeiro.persistence.planning  # noqa: F401
import bot_financeiro.persistence.resource_accounts  # noqa: F401
from bot_financeiro.persistence.schema import metadata

config = context.config
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        supplied_connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        context.configure(connection=supplied_connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
