from alembic import command
from alembic.config import Config

from bot_financeiro.persistence.database import create_sqlite_engine


def test_migration_0008_adds_and_reverses_financial_change_storage() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0007")
        for account_id, name in (("source", "Origem"), ("destination", "Destino")):
            connection.exec_driver_sql(
                "INSERT INTO resource_accounts "
                "(id, name, account_type, currency, balance_minor_units, status, "
                "allows_negative_balance) VALUES (?, ?, 'checking', 'BRL', 10000, "
                "'active', 1)",
                (account_id, name),
            )
        connection.exec_driver_sql(
            "INSERT INTO financial_movements "
            "(id, kind, amount_minor_units, currency, account_id, source_account_id, "
            "destination_account_id, allocation_id, related_transfer_id, payable_id, "
            "card_id, invoice_closing_date, applied_amount_minor_units, effective_date, "
            "recorded_at, origin) VALUES "
            "('movement', 'transfer', 1000, 'BRL', NULL, 'source', 'destination', "
            "NULL, NULL, NULL, NULL, NULL, NULL, '2026-09-01', "
            "'2026-09-01T12:00:00+00:00', 'manual')"
        )
        command.upgrade(config, "0008")
        movement_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(financial_movements)"
            )
        }
        change_table = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'financial_fact_changes'"
        ).scalar_one()
        triggers = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = 'financial_fact_changes'"
            )
        }
        preserved_movement = connection.exec_driver_sql(
            "SELECT kind, amount_minor_units FROM financial_movements WHERE id = 'movement'"
        ).one()

        assert "original_expense_id" in movement_columns
        assert change_table == "financial_fact_changes"
        assert triggers == {
            "financial_fact_changes_no_update",
            "financial_fact_changes_no_delete",
        }
        assert preserved_movement == ("transfer", 1000)

        command.downgrade(config, "0007")
        restored_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(financial_movements)"
            )
        }
        removed_table = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'financial_fact_changes'"
        ).one_or_none()
        movement_after_downgrade = connection.exec_driver_sql(
            "SELECT kind, amount_minor_units FROM financial_movements WHERE id = 'movement'"
        ).one()

    assert "original_expense_id" not in restored_columns
    assert removed_table is None
    assert movement_after_downgrade == ("transfer", 1000)
