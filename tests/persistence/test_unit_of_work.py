import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork


def test_unit_of_work_commits_success_and_rolls_back_an_entire_failed_operation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    audit_probe = Table(
        "audit_probe",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("description", String, nullable=False),
    )
    metadata.create_all(engine)
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=engine))

    with unit_of_work:
        unit_of_work.session.execute(
            insert(audit_probe).values(id=1, description="registro confirmado")
        )
        unit_of_work.commit()

    with pytest.raises(IntegrityError):
        with unit_of_work:
            unit_of_work.session.execute(
                insert(audit_probe).values(id=2, description="deve ser revertido")
            )
            unit_of_work.session.execute(
                insert(audit_probe).values(id=1, description="identidade duplicada")
            )
            unit_of_work.commit()

    with unit_of_work:
        descriptions = tuple(
            unit_of_work.session.scalars(
                select(audit_probe.c.description).order_by(audit_probe.c.id)
            )
        )

    assert descriptions == ("registro confirmado",)
