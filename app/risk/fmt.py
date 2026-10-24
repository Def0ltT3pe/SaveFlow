NBSP = "\u00a0"


def eur(cents: float) -> str:
    """«1 081,11 €» — как суммы на панели (ru-RU); пробелы неразрывные, чтобы сумму не разрывало переносом строки."""
    return f"{cents / 100:,.2f}".replace(",", NBSP).replace(".", ",") + NBSP + "€"


def duration(days: float) -> str:
    hours = days * 24
    if hours < 1:
        return "менее часа"
    if days < 1:
        return f"{round(hours)} ч"
    return f"{round(days)} дн."


def in_time(days: float) -> str:
    """«менее чем через час», «через 5 ч», «через 3 дн.» — для «Истекает …», «будет удалена …»."""
    return "менее чем через час" if days * 24 < 1 else f"через {duration(days)}"


def ago(days: float) -> str:
    """«менее часа назад», «5 ч назад», «3 дн. назад»."""
    return f"{duration(days)} назад"
