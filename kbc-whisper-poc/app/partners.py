"""Mock partner adapters.

Each adapter pretends to call a partner API (Ryanair bookings, SD Worx
payslips, ...). It is only ever called when the customer has connected that
partner, and it only returns a record that matches the transaction at hand.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from . import catalog
from .detection import Classification, normalise
from .money import fmt_eur


def date_range(start_iso: str, end_iso: str) -> str:
    """'2026-11-14', '2026-11-18' -> '14–18 Nov' (or '28 Nov – 2 Dec')."""
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    if start.month == end.month and start.year == end.year:
        return f"{start.day}–{end.day} {end.strftime('%b')}"
    return f"{start.day} {start.strftime('%b')} – {end.day} {end.strftime('%b')}"


def nights(record: dict) -> int:
    return (date.fromisoformat(record["return"]) - date.fromisoformat(record["depart"])).days


def fetch(partner_id: str, customer_id: str, tx: dict, cls: Classification) -> Optional[dict]:
    record = catalog.PARTNER_RECORDS.get((partner_id, customer_id))
    if not record:
        return None
    if partner_id == "ryanair":
        # Match the booking to this exact card payment.
        return record if record["fare_cents"] == tx["amount_cents"] else None
    if partner_id == "dealer":
        # Match the dealer order by its reference in the transfer message.
        ref = re.search(rf"\b{re.escape(record['order_ref'])}\b", tx.get("message") or "")
        return record if cls.category == "vehicle" and ref else None
    if partner_id == "huurhub":
        # Match the lease by the deposit amount.
        return record if cls.category == "rental" and record["deposit_cents"] == tx["amount_cents"] else None
    if partner_id == "sdworx":
        # Match the payslip by the net amount paid out.
        return record if ("SD WORX" in normalise(tx["counterparty"])
                          and record["net_cents"] == tx["amount_cents"]) else None
    return None


def summary(partner_id: str, record: dict) -> str:
    if partner_id == "ryanair":
        pax = record["passengers"]
        return (f"Booking shared with consent: {record['route']} · "
                f"{date_range(record['depart'], record['return'])} · {pax} passenger{'s' if pax != 1 else ''}")
    if partner_id == "dealer":
        return (f"Dealer order shared with consent: {record['condition'].lower()} · {record['fuel'].lower()} · "
                f"value {fmt_eur(record['catalogue_value_cents'])}")
    if partner_id == "huurhub":
        return (f"Lease shared with consent: {record['home_type'].lower()} · {record['bedrooms']} bedrooms · "
                f"{record['postcode']}")
    if partner_id == "sdworx":
        return (f"Payslip shared with consent: {record['payout_type']} · cafeteria plan: "
                f"{record['cafeteria_choice'].lower()} · gross salary +{record['gross_salary_change_pct']}% "
                f"since {record['salary_change_since']}")
    return "Partner data received"
