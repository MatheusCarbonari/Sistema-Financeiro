from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker


class SqlAlchemyUnitOfWork:
    """Delimita uma única transação lógica em uma Session curta."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        if self._session is not None:
            raise RuntimeError("unit of work is already active")
        self._session = self._session_factory()
        return self

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.rollback()
        finally:
            self.session.close()
            self._session = None
