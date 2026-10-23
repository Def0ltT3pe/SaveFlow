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
    funded_share: float  # доля horizon_cost, которую покрывают available_funds (0..1)
