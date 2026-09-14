from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
)
from sqlalchemy.orm import Session, sessionmaker

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.planning import (
    ForecastDirection,
    OccurrenceConfirmed,
    OccurrenceRevised,
    PlannedOccurrence,
    Recurrence,
    RecurrenceCreated,
    RecurrenceFrequency,
    RecurrenceRevisedFrom,
    SafetyMargin,
    SafetyMarginKind,
)
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialMovementRepository,
)
from bot_financeiro.persistence.money import money_from_minor_units, money_to_minor_units
from bot_financeiro.persistence.resource_accounts import (
    SqlAlchemyResourceAccountRepository,
    resource_accounts,
)
from bot_financeiro.persistence.schema import metadata
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork

planned_occurrences = Table(
    "planned_occurrences",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("account_id", String(36), ForeignKey(resource_accounts.c.id), nullable=False),
    Column("direction", String(16), nullable=False),
    Column("amount_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("planned_date", Date, nullable=False),
    Column("margin_kind", String(16), nullable=True),
    Column("margin_value", String, nullable=True),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
)

planning_recurrences = Table(
    "planning_recurrences",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("account_id", String(36), ForeignKey(resource_accounts.c.id), nullable=False),
    Column("direction", String(16), nullable=False),
    Column("amount_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("starts_on", Date, nullable=False),
    Column("ends_on", Date, nullable=True),
    Column("frequency", String(16), nullable=False),
    Column("interval", Integer, nullable=False),
    Column("margin_kind", String(16), nullable=True),
    Column("margin_value", String, nullable=True),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
)

planning_recurrence_revisions = Table(
    "planning_recurrence_revisions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "recurrence_id",
        String(36),
        ForeignKey(planning_recurrences.c.id),
        nullable=False,
    ),
    Column("sequence", Integer, nullable=False),
    Column("from_date", Date, nullable=False),
    Column("amount_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("margin_kind", String(16), nullable=True),
    Column("margin_value", String, nullable=True),
    Column("frequency", String(16), nullable=False),
    Column("interval", Integer, nullable=False),
    Column("reason", String, nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
    UniqueConstraint("recurrence_id", "sequence"),
)

planning_occurrence_events = Table(
    "planning_occurrence_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("occurrence_id", String(36), nullable=False),
    Column("recurrence_id", String(36), ForeignKey(planning_recurrences.c.id), nullable=True),
    Column("source_planned_date", Date, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("kind", String(16), nullable=False),
    Column("expected_minor_units", Integer, nullable=False),
    Column("actual_minor_units", Integer, nullable=True),
    Column("currency", String(3), nullable=False),
    Column("planned_date", Date, nullable=True),
    Column("margin_kind", String(16), nullable=True),
    Column("margin_value", String, nullable=True),
    Column("movement_id", String(36), ForeignKey("financial_movements.id"), unique=True),
    Column("reason", String, nullable=True),
    Column("original_expected_minor_units", Integer, nullable=True),
    Column("original_planned_date", Date, nullable=True),
    Column("original_margin_kind", String(16), nullable=True),
    Column("original_margin_value", String, nullable=True),
    Column("original_effective_date", Date, nullable=True),
    Column("original_recorded_at", String, nullable=True),
    Column("original_origin", String(16), nullable=True),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
    UniqueConstraint("occurrence_id", "sequence"),
)


class SqlAlchemyPlanningRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_occurrence(self, occurrence: PlannedOccurrence) -> None:
        if occurrence.recurrence_id is not None:
            raise ValueError("generated occurrences are owned by their recurrence")
        planned = occurrence.history[0]
        margin_kind, margin_value = _margin_values(occurrence.margin)
        self._session.execute(
            insert(planned_occurrences).values(
                id=str(occurrence.occurrence_id),
                account_id=str(occurrence.account_id),
                direction=occurrence.direction.value,
                amount_minor_units=money_to_minor_units(occurrence.expected_amount),
                currency=occurrence.expected_amount.currency,
                planned_date=occurrence.planned_date,
                margin_kind=margin_kind,
                margin_value=margin_value,
                effective_date=planned.metadata.effective_date,
                recorded_at=planned.metadata.recorded_at.isoformat(),
                origin=planned.metadata.origin.value,
            )
        )

    def get_occurrence(self, occurrence_id: UUID) -> PlannedOccurrence | None:
        row = (
            self._session.execute(
                select(planned_occurrences).where(
                    planned_occurrences.c.id == str(occurrence_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self.apply_history(self._occurrence(row))

    def list_standalone_occurrences(self) -> tuple[PlannedOccurrence, ...]:
        rows = self._session.execute(
            select(planned_occurrences).order_by(
                planned_occurrences.c.planned_date, planned_occurrences.c.id
            )
        ).mappings()
        return tuple(self.apply_history(self._occurrence(row)) for row in rows)

    def save_occurrence(self, occurrence: PlannedOccurrence) -> None:
        persisted = self._persisted_occurrence(occurrence)
        if persisted is None:
            raise LookupError(f"planned occurrence {occurrence.occurrence_id} does not exist")
        if (
            len(occurrence.history) < len(persisted.history)
            or occurrence.history[: len(persisted.history)] != persisted.history
        ):
            raise ValueError("persisted planning history is immutable and can only be appended")
        start_at = len(persisted.history)
        for sequence, event in enumerate(occurrence.history[start_at:], start=start_at):
            self._insert_occurrence_event(occurrence, sequence, event)

    def apply_history(self, occurrence: PlannedOccurrence) -> PlannedOccurrence:
        rows = tuple(
            self._session.execute(
            select(planning_occurrence_events)
            .where(
                planning_occurrence_events.c.occurrence_id
                == str(occurrence.occurrence_id)
            )
            .order_by(planning_occurrence_events.c.sequence)
            ).mappings()
        )
        confirmation_row = next(
            (row for row in rows if row["kind"] == "confirmed"), None
        )
        current = occurrence
        if confirmation_row is not None:
            original_metadata = RecordMetadata(
                effective_date=confirmation_row["original_effective_date"],
                recorded_at=datetime.fromisoformat(
                    confirmation_row["original_recorded_at"]
                ),
                origin=RecordOrigin(confirmation_row["original_origin"]),
                record_id=occurrence.occurrence_id,
            )
            original_amount = money_from_minor_units(
                confirmation_row["original_expected_minor_units"],
                confirmation_row["currency"],
            )
            current = PlannedOccurrence.plan(
                account_id=occurrence.account_id,
                direction=occurrence.direction,
                expected_amount=original_amount,
                planned_date=confirmation_row["original_planned_date"],
                margin=_margin(
                    confirmation_row["original_margin_kind"],
                    confirmation_row["original_margin_value"],
                    confirmation_row["currency"],
                ),
                metadata=original_metadata,
                recurrence_id=occurrence.recurrence_id,
                occurrence_id=occurrence.occurrence_id,
            )
        for row in rows:
            event_metadata = _metadata(row)
            if row["kind"] == "revised":
                event = OccurrenceRevised(
                    expected_amount=money_from_minor_units(
                        row["expected_minor_units"], row["currency"]
                    ),
                    planned_date=row["planned_date"],
                    margin=_margin(
                        row["margin_kind"], row["margin_value"], row["currency"]
                    ),
                    reason=row["reason"],
                    metadata=event_metadata,
                )
                planned = current.history[0]
                if event.metadata.recorded_at < planned.metadata.recorded_at:
                    current = PlannedOccurrence(
                        occurrence_id=current.occurrence_id,
                        account_id=current.account_id,
                        direction=current.direction,
                        expected_amount=current.expected_amount,
                        planned_date=current.planned_date,
                        margin=current.margin,
                        recurrence_id=current.recurrence_id,
                        history=(*current.history, event),
                    )
                else:
                    current = current.revise(
                        expected_amount=event.expected_amount,
                        planned_date=event.planned_date,
                        margin=event.margin,
                        reason=event.reason,
                        metadata=event.metadata,
                    )
            elif row["kind"] == "confirmed":
                expected = money_from_minor_units(
                    row["expected_minor_units"], row["currency"]
                )
                if current.expected_amount != expected:
                    current = PlannedOccurrence(
                        occurrence_id=current.occurrence_id,
                        account_id=current.account_id,
                        direction=current.direction,
                        expected_amount=expected,
                        planned_date=current.planned_date,
                        margin=current.margin,
                        recurrence_id=current.recurrence_id,
                        history=current.history,
                    )
                current = current.confirm(
                    movement_id=UUID(row["movement_id"]),
                    actual_amount=money_from_minor_units(
                        row["actual_minor_units"], row["currency"]
                    ),
                    metadata=event_metadata,
                )
            else:
                raise ValueError(f"unknown planning occurrence event: {row['kind']}")
        return current

    def add_recurrence(self, recurrence: Recurrence) -> None:
        created = recurrence.history[0]
        assert isinstance(created, RecurrenceCreated)
        margin_kind, margin_value = _margin_values(recurrence.margin)
        self._session.execute(
            insert(planning_recurrences).values(
                id=str(recurrence.recurrence_id),
                account_id=str(recurrence.account_id),
                direction=recurrence.direction.value,
                amount_minor_units=money_to_minor_units(recurrence.expected_amount),
                currency=recurrence.expected_amount.currency,
                starts_on=recurrence.starts_on,
                ends_on=recurrence.ends_on,
                frequency=recurrence.frequency.value,
                interval=recurrence.interval,
                margin_kind=margin_kind,
                margin_value=margin_value,
                effective_date=created.metadata.effective_date,
                recorded_at=created.metadata.recorded_at.isoformat(),
                origin=created.metadata.origin.value,
            )
        )

    def get_recurrence(self, recurrence_id: UUID) -> Recurrence | None:
        row = (
            self._session.execute(
                select(planning_recurrences).where(
                    planning_recurrences.c.id == str(recurrence_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._recurrence(row)

    def list_recurrences(self) -> tuple[Recurrence, ...]:
        rows = self._session.execute(
            select(planning_recurrences).order_by(
                planning_recurrences.c.starts_on, planning_recurrences.c.id
            )
        ).mappings()
        return tuple(self._recurrence(row) for row in rows)

    def save_recurrence(self, recurrence: Recurrence) -> None:
        persisted = self.get_recurrence(recurrence.recurrence_id)
        if persisted is None:
            raise LookupError(f"recurrence {recurrence.recurrence_id} does not exist")
        if (
            len(recurrence.history) < len(persisted.history)
            or recurrence.history[: len(persisted.history)] != persisted.history
        ):
            raise ValueError("persisted recurrence history is immutable and can only be appended")
        start_at = len(persisted.history)
        for sequence, event in enumerate(recurrence.history[start_at:], start=start_at):
            if not isinstance(event, RecurrenceRevisedFrom):
                raise TypeError("unsupported recurrence history event")
            margin_kind, margin_value = _margin_values(event.margin)
            self._session.execute(
                insert(planning_recurrence_revisions).values(
                    id=str(event.metadata.record_id),
                    recurrence_id=str(recurrence.recurrence_id),
                    sequence=sequence,
                    from_date=event.from_date,
                    amount_minor_units=money_to_minor_units(event.expected_amount),
                    currency=event.expected_amount.currency,
                    margin_kind=margin_kind,
                    margin_value=margin_value,
                    frequency=event.frequency.value,
                    interval=event.interval,
                    reason=event.reason,
                    effective_date=event.metadata.effective_date,
                    recorded_at=event.metadata.recorded_at.isoformat(),
                    origin=event.metadata.origin.value,
                )
            )

    def movement_is_linked(self, movement_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(planning_occurrence_events.c.id).where(
                    planning_occurrence_events.c.movement_id == str(movement_id)
                )
            )
            is not None
        )

    def occurrence_event_dates(self, recurrence_id: UUID) -> tuple[date, ...]:
        rows = self._session.execute(
            select(
                planning_occurrence_events.c.source_planned_date,
                planning_occurrence_events.c.planned_date,
            )
            .where(
                planning_occurrence_events.c.recurrence_id == str(recurrence_id)
            )
        )
        return tuple(
            sorted(
                {
                    event_date
                    for row in rows
                    for event_date in row
                    if event_date is not None
                }
            )
        )

    def occurrence_source_dates(self, recurrence_id: UUID) -> tuple[date, ...]:
        rows = self._session.scalars(
            select(planning_occurrence_events.c.source_planned_date)
            .where(
                planning_occurrence_events.c.recurrence_id == str(recurrence_id)
            )
            .order_by(planning_occurrence_events.c.source_planned_date)
        )
        return tuple(rows)

    def _persisted_occurrence(
        self, occurrence: PlannedOccurrence
    ) -> PlannedOccurrence | None:
        if occurrence.recurrence_id is None:
            return self.get_occurrence(occurrence.occurrence_id)
        recurrence = self.get_recurrence(occurrence.recurrence_id)
        if recurrence is None:
            return None
        original_date = occurrence.history[0].planned_date
        generated = next(
            (
                item
                for item in recurrence.occurrences_through(original_date)
                if item.occurrence_id == occurrence.occurrence_id
            ),
            None,
        )
        return None if generated is None else self.apply_history(generated)

    def _occurrence(self, row: object) -> PlannedOccurrence:
        amount = money_from_minor_units(row["amount_minor_units"], row["currency"])
        return PlannedOccurrence.plan(
            account_id=UUID(row["account_id"]),
            direction=ForecastDirection(row["direction"]),
            expected_amount=amount,
            planned_date=row["planned_date"],
            margin=_margin(row["margin_kind"], row["margin_value"], row["currency"]),
            metadata=_metadata(row),
            occurrence_id=UUID(row["id"]),
        )

    def _recurrence(self, row: object) -> Recurrence:
        amount = money_from_minor_units(row["amount_minor_units"], row["currency"])
        recurrence = Recurrence.create(
            account_id=UUID(row["account_id"]),
            direction=ForecastDirection(row["direction"]),
            expected_amount=amount,
            starts_on=row["starts_on"],
            ends_on=row["ends_on"],
            frequency=RecurrenceFrequency(row["frequency"]),
            interval=row["interval"],
            margin=_margin(row["margin_kind"], row["margin_value"], row["currency"]),
            metadata=_metadata(row),
        )
        revisions = self._session.execute(
            select(planning_recurrence_revisions)
            .where(
                planning_recurrence_revisions.c.recurrence_id == row["id"]
            )
            .order_by(planning_recurrence_revisions.c.sequence)
        ).mappings()
        for revision in revisions:
            recurrence = recurrence.revise_from(
                from_date=revision["from_date"],
                expected_amount=money_from_minor_units(
                    revision["amount_minor_units"], revision["currency"]
                ),
                margin=_margin(
                    revision["margin_kind"],
                    revision["margin_value"],
                    revision["currency"],
                ),
                frequency=RecurrenceFrequency(revision["frequency"]),
                interval=revision["interval"],
                reason=revision["reason"],
                metadata=_metadata(revision),
            )
        return recurrence

    def _insert_occurrence_event(
        self,
        occurrence: PlannedOccurrence,
        sequence: int,
        event: object,
    ) -> None:
        if isinstance(event, OccurrenceRevised):
            kind = "revised"
            actual_minor_units = None
            planned_date = event.planned_date
            margin_kind, margin_value = _margin_values(event.margin)
            movement_id = None
            reason = event.reason
            original_values = {
                "original_expected_minor_units": None,
                "original_planned_date": None,
                "original_margin_kind": None,
                "original_margin_value": None,
                "original_effective_date": None,
                "original_recorded_at": None,
                "original_origin": None,
            }
        elif isinstance(event, OccurrenceConfirmed):
            kind = "confirmed"
            actual_minor_units = money_to_minor_units(event.actual_amount)
            planned_date = occurrence.planned_date
            margin_kind = None
            margin_value = None
            movement_id = str(event.movement_id)
            reason = None
            original = occurrence.history[0]
            original_margin_kind, original_margin_value = _margin_values(
                original.margin
            )
            original_values = {
                "original_expected_minor_units": money_to_minor_units(
                    original.expected_amount
                ),
                "original_planned_date": original.planned_date,
                "original_margin_kind": original_margin_kind,
                "original_margin_value": original_margin_value,
                "original_effective_date": original.metadata.effective_date,
                "original_recorded_at": original.metadata.recorded_at.isoformat(),
                "original_origin": original.metadata.origin.value,
            }
        else:
            raise TypeError("unsupported occurrence history event")
        self._session.execute(
            insert(planning_occurrence_events).values(
                id=str(event.metadata.record_id),
                occurrence_id=str(occurrence.occurrence_id),
                recurrence_id=(
                    None if occurrence.recurrence_id is None else str(occurrence.recurrence_id)
                ),
                source_planned_date=occurrence.history[0].planned_date,
                sequence=sequence,
                kind=kind,
                expected_minor_units=money_to_minor_units(event.expected_amount),
                actual_minor_units=actual_minor_units,
                currency=event.expected_amount.currency,
                planned_date=planned_date,
                margin_kind=margin_kind,
                margin_value=margin_value,
                movement_id=movement_id,
                reason=reason,
                effective_date=event.metadata.effective_date,
                recorded_at=event.metadata.recorded_at.isoformat(),
                origin=event.metadata.origin.value,
                **original_values,
            )
        )


class SqlAlchemyPlanningUnitOfWork(SqlAlchemyUnitOfWork):
    accounts: SqlAlchemyResourceAccountRepository
    movements: SqlAlchemyFinancialMovementRepository
    planning: SqlAlchemyPlanningRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)

    def __enter__(self) -> SqlAlchemyPlanningUnitOfWork:
        super().__enter__()
        self.accounts = SqlAlchemyResourceAccountRepository(self.session)
        self.movements = SqlAlchemyFinancialMovementRepository(self.session)
        self.planning = SqlAlchemyPlanningRepository(self.session)
        return self


def _metadata(row: object) -> RecordMetadata:
    return RecordMetadata(
        effective_date=row["effective_date"],
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
        origin=RecordOrigin(row["origin"]),
        record_id=UUID(row["id"]),
    )


def _margin_values(margin: SafetyMargin | None) -> tuple[str | None, str | None]:
    if margin is None:
        return None, None
    if margin.kind is SafetyMarginKind.FIXED:
        assert margin.fixed_amount is not None
        return margin.kind.value, str(money_to_minor_units(margin.fixed_amount))
    assert margin.percentage_rate is not None
    return margin.kind.value, str(margin.percentage_rate)


def _margin(kind: str | None, value: str | None, currency: str) -> SafetyMargin | None:
    if kind is None:
        return None
    if value is None:
        raise ValueError("persisted safety margin has no value")
    if SafetyMarginKind(kind) is SafetyMarginKind.FIXED:
        return SafetyMargin.fixed(money_from_minor_units(int(value), currency))
    return SafetyMargin.percentage(Decimal(value))
