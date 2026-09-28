from decimal import Decimal, InvalidOperation


def parse_sek(text: str) -> int:

    try:
        value = Decimal(text.strip())

    except InvalidOperation:
        raise ValueError(f"not a number: {text!r}")

    if not value.is_finite() or value <= 0:
        raise ValueError("amount must be a positive number")

    if value != value.quantize(Decimal("0.01")):
        raise ValueError("at most two decimals")

    return int(value * 100)


def format_sek(minor: int) -> str:
    """4550 -> '45.50'."""
    return f"{minor // 100}.{minor % 100:02d}"
