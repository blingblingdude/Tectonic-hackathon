"""Turns a classified transaction into the whisper the customer sees:
copy, the "why am I seeing this" reasons, and the form with pre-filled answers.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import catalog
from .detection import Classification
from .money import fmt_eur
from .partners import date_range, nights
from .products import CAR_ZIPS, QUESTIONS, PENSION_SAVINGS_CAP_CENTS, cheapest

PILLS = {
    "flight": "A thought about your trip",
    "vehicle": "A thought about your new car",
    "rental": "A thought about your new home",
    "payroll": "A thought about your payout",
}

FORMS = {
    "flight": ("Travel insurance", "A few quick questions. We filled in what we could guess.",
               "We pre-filled this from your booking. Just check it's right.", "See my options"),
    "vehicle": ("Car insurance", "A few quick questions and we'll show you your options.",
                "Pre-filled from your dealer order. Just confirm where the car sleeps.", "See my options"),
    "rental": ("Home & fire insurance",
               "Tell us a bit about the home. The postcode helps us check local risks like flooding.",
               "Pre-filled from your lease. Check and continue.", "See my options"),
    "payroll": ("Smart moves for your payout", "Tell us a bit about this payment and your payroll choices.",
                "Pre-filled from your SD Worx payslip and your KBC accounts.", "See my smart moves"),
}

CAFETERIA_PHRASES = {"E-bike lease": "an e-bike lease", "Extra holidays": "extra holidays",
                     "Pension top-up": "a pension top-up", "Laptop / smartphone": "a laptop or smartphone"}


def _pre(value: str, label: str, kind: str) -> dict:
    """kind: partner (from a connected app) | kbc (KBC's own data) | guess (inferred from the transaction)."""
    return {"value": value, "label": label, "kind": kind}


def pension_status(ytd_cents: int) -> str:
    if ytd_cents <= 0:
        return "Not yet"
    return "Already maxed" if ytd_cents >= PENSION_SAVINGS_CAP_CENTS else "Partly"


def prefills(cls: Classification, record: Optional[dict], customer: dict) -> Dict[str, dict]:
    p: Dict[str, dict] = {}
    c = cls.category
    if c == "flight":
        if record:
            p["dest"] = _pre(record["region"], "From booking", "partner")
            n = nights(record)
            length = "Weekend (≤ 3 days)" if n <= 3 else "Up to a week" if n <= 7 else "Up to a month"
            p["len"] = _pre(length, "From booking", "partner")
        elif cls.partner_id == "ryanair":
            p["dest"] = _pre("Europe", "Our guess", "guess")
    elif c == "vehicle":
        if record:
            p["cond"] = _pre(record["condition"], "From dealer", "partner")
            p["fuel"] = _pre(record["fuel"], "From dealer", "partner")
        if customer["postcode"] in CAR_ZIPS:
            p["zip"] = _pre(customer["postcode"], "Your address", "kbc")
    elif c == "rental":
        if record:
            p["sit"] = _pre("Renting", "From lease", "partner")
            p["zip"] = _pre(record["postcode"], "From lease", "partner")
            p["type"] = _pre(record["home_type"], "From lease", "partner")
            p["size"] = _pre(record["bedrooms"], "From lease", "partner")
        else:
            p["sit"] = _pre("Renting", "Our guess", "guess")
    elif c == "payroll":
        if record:
            p["event"] = _pre(record["payout_type"], "From SD Worx", "partner")
            p["cafe"] = _pre(record["cafeteria_choice"], "From SD Worx", "partner")
        elif cls.facts.get("payout_kind") == "year_end_bonus":
            p["event"] = _pre("13th month / year-end bonus", "Our guess", "guess")
        elif cls.facts.get("payout_kind") == "bonus":
            p["event"] = _pre("One-off bonus", "Our guess", "guess")
        p["pens"] = _pre(pension_status(customer["pension_ytd_cents"]), "KBC data", "kbc")
    # Only keep prefills that are valid options (partner data can be messy).
    options = {q["id"]: q["options"] for q in QUESTIONS[c]}
    return {k: v for k, v in p.items() if v["value"] in options.get(k, [])}


def _notes(cls: Classification, record: Optional[dict], filled: Dict[str, dict]) -> Dict[str, str]:
    notes: Dict[str, str] = {}
    if cls.category == "flight" and filled.get("dest", {}).get("kind") == "guess":
        notes["dest"] = "Ryanair flies almost only within Europe, so we pre-selected it."
    if cls.category == "rental" and filled.get("sit", {}).get("kind") == "guess":
        notes["sit"] = f'The word "{cls.facts.get("keyword", "huurwaarborg")}" in your transfer suggests a rental.'
    if cls.category == "payroll":
        if filled.get("event", {}).get("kind") == "guess":
            notes["event"] = f'The message "{cls.facts.get("keyword")}" suggests this.'
        elif "event" not in filled:
            notes["event"] = "We can't tell a bonus from a raise on your statement, so we ask."
        if "cafe" not in filled:
            notes["cafe"] = "We can't see this on your statement, so we ask."
        notes["pens"] = "We know this from your own KBC accounts."
    return notes


def build_form(cls: Classification, record: Optional[dict], customer: dict, data_source: str) -> dict:
    filled = prefills(cls, record, customer)
    notes = _notes(cls, record, filled)
    title, intro_rec, intro_partner, submit = FORMS[cls.category]
    questions = []
    for q in QUESTIONS[cls.category]:
        questions.append({
            "id": q["id"], "label": q["label"], "options": q["options"],
            "prefill": filled.get(q["id"]),
            "note": notes.get(q["id"], q.get("note")),
            "option_notes": q.get("option_notes", {}),
        })
    return {"title": title, "intro": intro_partner if data_source == "partner" else intro_rec,
            "submit_label": submit, "questions": questions}


def _join(words: List[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def copy(cls: Classification, record: Optional[dict], tx: dict, customer: dict) -> dict:
    """Title, body and context line of the whisper card."""
    amount = fmt_eur(tx["amount_cents"])
    cp = tx["counterparty"]
    c = cls.category
    if c == "flight":
        if record:
            dates = date_range(record["depart"], record["return"])
            pax = record["passengers"]
            return {"title": f"Your {record['destination']} trip isn't insured yet",
                    "body": f"{dates}, {pax} traveller{'s' if pax != 1 else ''}. "
                            "Add cancellation, luggage and medical cover in one tap.",
                    "context": {"icon": "✈️", "title": f"{record['route']} · {dates}", "subtitle": "Ryanair booking"}}
        return {"title": "Flying somewhere soon?",
                "body": "Looks like you just booked a flight. Cover the trip against cancellation, "
                        "lost luggage and medical costs abroad.",
                "context": {"icon": "✈️", "title": f"{cp} · {amount}", "subtitle": "Just now"}}
    if c == "vehicle":
        if record:
            new = "new " if record["condition"] == "New" else ""
            return {"title": f"Insure your {new}{record['fuel'].lower()} car",
                    "body": "We have the details from your dealer. Confirm where it's parked and you're ready to drive.",
                    "context": {"icon": "🚗",
                                "title": f"{record['condition']} {record['fuel'].lower()} · "
                                         f"{fmt_eur(record['catalogue_value_cents'])}",
                                "subtitle": "Dealer order"}}
        return {"title": "New wheels? 🚗",
                "body": "You just paid a car dealer. Liability cover is required before you drive off. "
                        "Want a quote in 30 seconds?",
                "context": {"icon": "🚗", "title": f"{cp} · {amount}", "subtitle": "Just now"}}
    if c == "rental":
        if record:
            city = record["postcode"].split(" ", 1)[-1]
            return {"title": f"Protect your new {record['home_type'].lower()} in {city}",
                    "body": "We have your lease details. Fire, water damage and contents, tailored to your neighbourhood.",
                    "context": {"icon": "🏠", "title": f"{record['address']}, {record['postcode']}",
                                "subtitle": "Lease details"}}
        return {"title": "Moving into a new place? 🏠",
                "body": "Congrats! Most landlords ask for tenant fire insurance. Tell us a bit about the home "
                        "and we'll tailor it to your area.",
                "context": {"icon": "🏠", "title": f"Rental deposit · {amount}",
                            "subtitle": cls.facts.get("address") or cp}}
    # payroll
    if record:
        payout = record["payout_type"]
        title = "Your 13th month has landed" if payout.startswith("13th") else f"Your {payout.lower()} has landed"
        owned = set(customer.get("owned_products") or [])
        moves = []
        if customer["pension_ytd_cents"] < PENSION_SAVINGS_CAP_CENTS:
            moves.append("pension savings")
        if record["cafeteria_choice"] == "E-bike lease" and "bike" not in owned:
            moves.append("bike cover")
        if record["cafeteria_choice"] == "Laptop / smartphone" and "device" not in owned:
            moves.append("device cover")
        if "income" not in owned:
            moves.append("income protection")
        moves.append("a savings buffer")
        phrase = CAFETERIA_PHRASES.get(record["cafeteria_choice"])
        lead = f"And you picked {phrase} in your cafeteria plan. " if phrase else ""
        return {"title": title, "body": f"{lead}We lined up a few smart moves: {_join(moves)}.",
                "context": {"icon": "💼", "title": f"{payout} · + {amount}", "subtitle": "From SD Worx payslip"}}
    kind = cls.facts.get("payout_kind")
    label = {"year_end_bonus": "year-end bonus", "bonus": "bonus"}.get(kind, "bigger payout")
    delta = cls.facts.get("delta_cents")
    if delta and delta > 0:
        title = "A bigger payout than usual 🎉"
        body = (f"This payment is {fmt_eur(delta)} higher than your usual salary. Looks like a {label}. "
                "Want a few smart ideas so less of it goes to tax?")
    else:
        title = {"year_end_bonus": "Your year-end bonus has landed 🎉", "bonus": "A bonus has landed 🎉"}.get(
            kind, "A bigger payout than usual 🎉")
        body = f"Looks like a {label}. Want a few smart ideas so less of it goes to tax?"
    return {"title": title, "body": body,
            "context": {"icon": "💼", "title": f"{cp} · + {amount}", "subtitle": "Just now"}}


def teaser(cls: Classification, form: dict, ctx: dict, data_source: str, segment: str) -> str:
    filled = {q["id"]: q["prefill"]["value"] for q in form["questions"] if q["prefill"]}
    if cls.category == "payroll":
        open_n = len(form["questions"]) - len(filled)
        if open_n == 0:
            return "Ready to review"
        plural = "s" if open_n != 1 else ""
        return f"{open_n} question{plural} left" if data_source == "partner" else f"{open_n} quick question{plural}"
    best = cheapest(cls.category, filled, ctx)
    unit = "" if best["price_unit"] in ("this trip", "per year") else f" {best['price_unit']}"
    text = f"from {fmt_eur(best['price_cents'])}{unit}"
    if cls.category == "flight" and segment == "student":
        text += " · student rate"
    return text


def privacy_note(cls: Classification, data_source: str) -> str:
    partner = catalog.PARTNERS_BY_ID.get(cls.partner_id or "")
    if data_source == "partner" and partner:
        return f"{partner['name']} data was used because you connected it. Manage this under Data sharing."
    who = partner["name"] if partner else "the merchant"
    return f"We only used your own transaction data. Nothing was shared with {who}."
