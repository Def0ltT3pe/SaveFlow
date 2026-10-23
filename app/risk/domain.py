from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AccountData:
    id: int
    email: str
    balance: int
    withdraw_balance: int
    total_replenished: int
    bonus_balance: int
    bonus_state: str

    @property
    def usable_bonus(self) -> int:
        """Бонусы, которыми автопродление может оплатить услугу."""
        return self.bonus_balance if self.bonus_state == "unlocked" else 0


@dataclass(frozen=True)
class ServiceData:
    id: int
    name: str
    owner_id: int
    payment_term: str
    auto_prolong: bool
    created_at: datetime
    expires_at: datetime
    status: str
    price: int


@dataclass(frozen=True)
class TxStats:
    total: int = 0
    performed: int = 0
    not_performed: int = 0
    last_performed_at: datetime | None = None
