from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from app.risk.domain import AccountData, ServiceData, TxStats

if TYPE_CHECKING:
    from app.risk.schemas import ModelSettings


@dataclass(frozen=True)
class ServiceContext:
    service: ServiceData
    account: AccountData
    account_services: tuple[ServiceData, ...]
    tx: TxStats
    as_of: datetime
    settings: "ModelSettings"
    age_days: float
    days_to_expiry: float  # < 0: срок уже истёк
    renewals_in_horizon: int
    horizon_cost: int  # сколько стоят продления этой услуги в горизонте, евроценты
    account_horizon_cost: int  # то же по всем действующим услугам клиента
    available_funds: int  # balance + доступный бонус
    # Через сколько дней кончатся деньги на продления всех услуг клиента (None — хватит на весь горизонт)
    runway_days: float | None
    # Через сколько дней у этой услуги будет первое продление, на которое денег уже не хватит (None — хватит)
    unfunded_in_days: float | None
