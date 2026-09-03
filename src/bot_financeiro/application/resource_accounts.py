from typing import Protocol
from uuid import UUID

from bot_financeiro.domain.resource_account import ResourceAccount


class ResourceAccountRepository(Protocol):
    def add(self, account: ResourceAccount) -> None: ...

    def get(self, account_id: UUID) -> ResourceAccount | None: ...

    def save(self, account: ResourceAccount) -> None: ...
