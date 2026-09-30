"""Money helpers.

All amounts are stored and exchanged as integer euro cents, so no floating
point rounding errors can creep into balances. Calculations that need
fractions (premiums, tax benefits) use Decimal and are rounded half-up to
whole cents exactly once, at the end.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Union

Number = Union[int, str, Decimal]


def D(value: Number) -> Decimal:
    """Build a Decimal from an int, a string or a Decimal (never from a float)."""
    if isinstance(value, float):  # pragma: no cover - guard against misuse
        raise TypeError("Use strings or ints for money values, not floats")
    return Decimal(value)


def euros_to_cents(amount: Decimal) -> int:
    """Round a euro amount half-up to whole cents."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_euros(cents: int) -> Decimal:
    return Decimal(cents) / 100


def fmt_eur(cents: int, whole: bool = False) -> str:
    """Format cents the Belgian way: 18450000 -> '€ 184.500,00'."""
    sign = "− " if cents < 0 else ""
    cents = abs(cents)
    if whole:
        euros = int((Decimal(cents) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return f"{sign}€ {euros:,}".replace(",", ".")
    euros, rest = divmod(cents, 100)
    return f"{sign}€ {euros:,}".replace(",", ".") + f",{rest:02d}"
