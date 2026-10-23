def eur(cents: float) -> str:
    return f"€{cents / 100:,.2f}".replace(",", " ")


def duration(days: float) -> str:
    hours = days * 24
    if hours < 1:
        return "менее часа"
    if days < 1:
        return f"{round(hours)} ч"
    return f"{round(days)} дн."
