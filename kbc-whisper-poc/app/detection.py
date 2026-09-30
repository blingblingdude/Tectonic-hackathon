"""Rules engine: what is this transaction?

It only looks at the raw transaction fields a bank always has (direction,
amount, counterparty, merchant category code, free-text message) plus the
customer's own history. No partner connection is needed for any rule here;
partner data is only used later, to pre-fill details.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import catalog
from .money import fmt_eur

MIN_FLIGHT_CENTS = 100_00          # ignore tiny airline charges (seat, bag)
MIN_VEHICLE_CENTS = 2_500_00       # ignore servicing / parts at a dealer
PAYOUT_RATIO_TRIGGER = 1.25        # 25% above the usual salary counts as "bigger"

RENTAL_KEYWORDS = ("HUURWAARBORG", "HUURGARANTIE", "GARANTIE LOCATIVE", "RENTAL DEPOSIT")
# Checked in order: the specific year-end terms before the generic ones.
YEAR_END_KEYWORDS = ("EINDEJAARSPREMIE", "13DE MAAND", "13E MAAND", "PRIME DE FIN D'ANNEE")
GENERIC_BONUS_KEYWORDS = ("BONUS", "PREMIE", "PRIME")

CATEGORY_LABELS = {
    "flight": "travel insurance",
    "vehicle": "car insurance",
    "rental": "home insurance",
    "payroll": "salary and bonus",
}


@dataclass
class Classification:
    category: str                      # flight | vehicle | rental | payroll
    evidence: str                      # one line for the "Classified" signal
    confidence: float
    partner_id: Optional[str] = None   # which partner could enrich this, if any
    facts: dict = field(default_factory=dict)


def normalise(text: str) -> str:
    """Upper-case and strip accents so 'Prime de fin d'année' matches 'PRIME DE FIN D'ANNEE'."""
    text = (text or "").replace("’", "'").replace("‘", "'")   # before the ASCII step, which would drop them
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).upper().strip()


def is_airline_mcc(mcc: Optional[str]) -> bool:
    if not mcc or not mcc.isdigit():
        return False
    code = int(mcc)
    return code == 4511 or 3000 <= code <= 3299   # 3000-3299: individual airlines


def payroll_provider(counterparty: str) -> Optional[Tuple[str, Optional[str]]]:
    name = normalise(counterparty)
    for provider, partner_id in catalog.PAYROLL_PROVIDERS.items():
        if provider in name:
            return provider, partner_id
    return None


def bonus_keyword(message: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (keyword, kind) for a payroll message, kind being 'year_end_bonus' or 'bonus'."""
    msg = normalise(message)
    keyword = next((k for k in YEAR_END_KEYWORDS if k in msg), None)
    if keyword:
        return keyword, "year_end_bonus"
    keyword = next((k for k in GENERIC_BONUS_KEYWORDS if re.search(rf"\b{k}\b", msg)), None)
    return (keyword, "bonus") if keyword else (None, None)


def usual_salary(previous_credits: Sequence[int]) -> Optional[int]:
    """Median of the last six regular payroll credits (the caller leaves out bonus payouts)."""
    recent = list(previous_credits)[-6:]
    return int(statistics.median(recent)) if recent else None


def _extract_address(message: str, keyword: str) -> Optional[str]:
    match = re.search(re.escape(keyword), message, flags=re.IGNORECASE)
    if not match:
        return None
    rest = message[match.end():].strip(" :-/,")
    address = re.match(r"[A-Za-zÀ-ÿ'. -]+?\s\d+[A-Za-z]?", rest)
    return address.group(0).strip() if address else None


def classify(tx: dict, previous_payroll_credits: Sequence[int] = ()) -> Tuple[Optional[Classification], str]:
    """Return (classification, explanation). Classification is None when no rule fires."""
    direction = tx["direction"]
    amount = tx["amount_cents"]            # always positive here
    counterparty = tx["counterparty"]
    message = tx.get("message") or ""
    msg = normalise(message)

    if direction == "out":
        # 1. Flights: the merchant category code says "airline".
        if is_airline_mcc(tx.get("mcc")):
            if amount < MIN_FLIGHT_CENTS:
                return None, f"Airline charge below {fmt_eur(MIN_FLIGHT_CENTS)}: probably a seat or bag fee"
            partner = "ryanair" if "RYANAIR" in normalise(counterparty) else None
            return Classification("flight", f"Merchant category {tx['mcc']} · Airlines", 0.97, partner,
                                  {"merchant": counterparty}), ""

        # 2. Vehicles: the counterparty is registered as a car dealer.
        company = catalog.COMPANY_REGISTRY.get(normalise(counterparty))
        if company and company["nace"] == catalog.CAR_DEALER_NACE:
            if amount < MIN_VEHICLE_CENTS:
                return None, "Payment to a car dealer, but too small for a vehicle purchase (servicing or parts)"
            return Classification(
                "vehicle",
                f"Recipient registered as car dealer (NACE {company['nace']}) · large one-off amount",
                0.9, company.get("partner_id"), {"dealer": counterparty},
            ), ""

        # 3. Rentals: the transfer message mentions a rental deposit.
        for keyword in RENTAL_KEYWORDS:
            if keyword in msg:
                address = _extract_address(message, keyword)
                shown = next((w for w in message.split() if normalise(w) == keyword), keyword.lower())
                evidence = f'Message keyword "{shown}" (rental deposit)'
                if address:
                    evidence += f" · address {address}"
                return Classification("rental", evidence, 0.9, "huurhub",
                                      {"keyword": shown, "address": address, "landlord": counterparty}), ""

        return None, "No rule matched: an everyday payment"

    # Incoming money: only payroll is interesting for now.
    provider = payroll_provider(counterparty)
    if not provider:
        return None, "Incoming payment that is not from a payroll provider"
    _, partner_id = provider
    usual = usual_salary(previous_payroll_credits)
    keyword, kind = bonus_keyword(message)
    ratio = (amount / usual) if usual else None
    if not kind and ratio is not None and ratio >= PAYOUT_RATIO_TRIGGER:
        kind = "higher_payout"
    if not kind:
        return None, f"Regular salary from {counterparty}, in line with the usual pay"

    parts: List[str] = [f"{counterparty} is the usual payroll provider"]
    pct_above = round((ratio - 1) * 100) if ratio is not None else None
    if pct_above is not None and pct_above > 0:
        parts.append(f"{pct_above}% above the usual salary of {fmt_eur(usual)}")
    if keyword:
        parts.append(f'message "{keyword}"')
    return Classification(
        "payroll", " · ".join(parts), 0.95 if keyword else 0.75, partner_id,
        {"provider": counterparty, "payout_kind": kind, "usual_cents": usual,
         "delta_cents": (amount - usual) if usual else None, "keyword": keyword},
    ), ""
