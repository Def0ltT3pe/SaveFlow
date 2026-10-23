# Длина оплачиваемого периода в днях. Для месяца и длиннее: средняя календарная длина.
PERIOD_DAYS: dict[str, float] = {
    "hour": 1 / 24,
    "week": 7.0,
    "month": 30.4375,
    "quarter_year": 91.3125,
    "half_year": 182.625,
    "year": 365.25,
}
DAYS_PER_MONTH = 30.4375

# Услуги, которые ещё приносят или могут принести деньги: только их оцениваем.
LIVE_STATUSES = frozenset({"active", "suspended", "prolong_wait"})
# Уже потерянные: deleted (удалена) и blocked (продлить невозможно).
CHURNED_STATUSES = frozenset({"deleted", "blocked"})


def monthly_price(price: int, payment_term: str) -> float:
    """Цена услуги, приведённая к месяцу (price считается ценой одного периода paymentTerm)."""
    return price * DAYS_PER_MONTH / PERIOD_DAYS[payment_term]
