from app.risk.context import ServiceContext
from app.risk.fmt import duration, eur
from app.risk.registry import NO_SIGNAL, FeatureResult, feature
from app.risk.terms import CHURNED_STATUSES


@feature(
    "prolong_wait",
    title="Ожидает оплаты",
    default_weight=45,
    description="Срок услуги истёк, она в статусе prolong_wait и без оплаты будет удалена после льготного периода.",
)
def prolong_wait(ctx: ServiceContext) -> FeatureResult:
    if ctx.service.status != "prolong_wait":
        return NO_SIGNAL
    left = ctx.settings.prolong_wait_grace_days + ctx.days_to_expiry
    if left > 0:
        return FeatureResult(1.0, f"Ожидает оплаты: без неё будет удалена через {duration(left)}")
    return FeatureResult(1.0, "Ожидает оплаты дольше льготного периода: удаление в любой момент")


@feature(
    "expiring_no_autoprolong",
    title="Скоро истекает без автопродления",
    default_weight=30,
    description="Автопродление выключено, а срок истекает в пределах горизонта прогноза. Чем ближе дата, тем сильнее сигнал.",
)
def expiring_no_autoprolong(ctx: ServiceContext) -> FeatureResult:
    if ctx.service.auto_prolong or ctx.service.status not in ("active", "suspended"):
        return NO_SIGNAL
    if ctx.days_to_expiry <= 0:
        return FeatureResult(1.0, "Срок истёк, автопродление выключено")
    horizon = ctx.settings.horizon_days
    if ctx.days_to_expiry > horizon:
        return NO_SIGNAL
    return FeatureResult(
        1 - ctx.days_to_expiry / horizon,
        f"Истекает через {duration(ctx.days_to_expiry)}, автопродление выключено",
    )


@feature(
    "insufficient_funds",
    title="Не хватает средств на продление",
    default_weight=25,
    description="Основного и доступного бонусного баланса не хватит на продления в горизонте прогноза. "
    "Средства распределяются по услугам в порядке ближайшего списания.",
)
def insufficient_funds(ctx: ServiceContext) -> FeatureResult:
    if ctx.horizon_cost == 0:
        return NO_SIGNAL
    shortage = 1 - ctx.funded_share
    if shortage <= 0:
        return NO_SIGNAL
    return FeatureResult(
        shortage,
        f"На балансе {eur(ctx.available_funds)}, а продления за {ctx.settings.horizon_days} дн. "
        f"стоят {eur(ctx.account_horizon_cost)}",
    )


@feature(
    "suspended",
    title="Сервер выключен",
    default_weight=10,
    description="Пользователь сам выключил сервер (suspended): услугой не пользуются, хотя деньги списываются.",
)
def suspended(ctx: ServiceContext) -> FeatureResult:
    if ctx.service.status != "suspended":
        return NO_SIGNAL
    return FeatureResult(1.0, "Сервер выключен пользователем: услугой не пользуются")


@feature(
    "churn_history",
    title="Уже уходил",
    default_weight=15,
    description="Доля услуг клиента, которые уже удалены или заблокированы.",
)
def churn_history(ctx: ServiceContext) -> FeatureResult:
    total = len(ctx.account_services)
    churned = sum(s.status in CHURNED_STATUSES for s in ctx.account_services)
    if churned == 0:
        return NO_SIGNAL
    return FeatureResult(churned / total, f"Удалено или заблокировано {churned} из {total} услуг клиента")


@feature(
    "unpaid_transactions",
    title="Неоплаченные платежи",
    default_weight=10,
    description="Доля непроведённых транзакций (not_performed) по услуге: созданные, но не оплаченные заказы и продления.",
)
def unpaid_transactions(ctx: ServiceContext) -> FeatureResult:
    if ctx.tx.not_performed == 0:
        return NO_SIGNAL
    return FeatureResult(
        ctx.tx.not_performed / ctx.tx.total,
        f"Не проведено {ctx.tx.not_performed} из {ctx.tx.total} платежей по услуге",
    )


@feature(
    "new_service",
    title="Новая услуга",
    default_weight=5,
    description="Услуга подключена недавно: в первые недели клиенты отказываются чаще. Сигнал затухает к концу периода.",
)
def new_service(ctx: ServiceContext) -> FeatureResult:
    window = ctx.settings.new_service_days
    if ctx.age_days >= window:
        return NO_SIGNAL
    return FeatureResult(1 - max(ctx.age_days, 0) / window, f"Подключена {duration(ctx.age_days)} назад")
