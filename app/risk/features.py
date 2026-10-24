from app.risk.context import ServiceContext
from app.risk.fmt import ago, eur, in_time
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
    # Срок в будущем при статусе prolong_wait — несогласованные данные: считаем, что он истёк сейчас.
    left = ctx.settings.prolong_wait_grace_days + min(ctx.days_to_expiry, 0.0)
    if left > 0:
        return FeatureResult(1.0, f"Ожидает оплаты: без неё будет удалена {in_time(left)}")
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
        f"Истекает {in_time(ctx.days_to_expiry)}, автопродление выключено",
    )


@feature(
    "insufficient_funds",
    title="Не хватает средств на продление",
    default_weight=35,
    description="Деньги на продления кончатся в пределах горизонта прогноза. Чем раньше наступит продление, "
    "которое уже нечем оплатить, тем сильнее сигнал: от 1 (платить нужно сейчас) до 0.5 (в конце горизонта). "
    "Учитываются основной баланс и доступные бонусы, продления оплачиваются по порядку наступления.",
)
def insufficient_funds(ctx: ServiceContext) -> FeatureResult:
    if ctx.unfunded_in_days is None:
        return NO_SIGNAL
    horizon = ctx.settings.horizon_days
    money = _funds_text(ctx)
    next_due = 0.0 if ctx.service.status == "prolong_wait" else max(ctx.days_to_expiry, 0.0)
    if ctx.unfunded_in_days == 0:
        text = f"Денег не хватает на продление, которое нужно оплатить сейчас: {money}"
    elif abs(ctx.unfunded_in_days - next_due) < 1e-6:
        text = f"Денег не хватит на ближайшее продление {in_time(ctx.unfunded_in_days)}: {money}"
    else:
        text = f"Деньги на продления кончатся {in_time(ctx.runway_days)}: {money}"
    # Нечем оплатить продление в горизонте — сильный сигнал при любом сроке, поэтому не ниже половины веса.
    return FeatureResult(1 - 0.5 * ctx.unfunded_in_days / horizon,
                         f"{text}, а продления за {horizon} дн. стоят {eur(ctx.account_horizon_cost)}")


def _funds_text(ctx: ServiceContext) -> str:
    """«на балансе €2.00 и бонусах €30.00» — без смешивания реальных денег и бонусов в одну цифру."""
    a = ctx.account
    text = f"на балансе {eur(a.balance)}"
    if a.usable_bonus:
        text += f" и бонусах {eur(a.usable_bonus)}"
    elif a.bonus_balance:
        text += f" (бонусы {eur(a.bonus_balance)} заблокированы)"
    return text


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
    return FeatureResult(1 - max(ctx.age_days, 0) / window, f"Подключена {ago(ctx.age_days)}")
