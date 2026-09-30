"""Products: the questions we ask per category and the (illustrative) pricing.

Prices are computed only here, on the server. The frontend never sends a
price: it sends answers, gets a priced quote back, and accepts a quote by id.
"""
from __future__ import annotations

import itertools
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from .money import D, euros_to_cents, fmt_eur

PENSION_SAVINGS_CAP_CENTS = 1_050_00   # illustrative yearly amount for the 30% tax reduction
PENSION_TAX_RATE = D("0.30")
QUOTE_TTL_MINUTES = 15
# Products a customer only needs once: never offer them again when already held.
SINGLE_POLICY_PRODUCTS = {"car", "home", "bike", "device", "income"}

# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------
CAR_ZIPS = ["1000 Brussels", "2000 Antwerp", "3000 Leuven", "9000 Ghent", "8400 Ostend"]
HOME_ZIPS = ["3000 Leuven", "9000 Ghent", "2000 Antwerp", "8620 Nieuwpoort", "4000 Liège"]

QUESTIONS: Dict[str, List[dict]] = {
    "flight": [
        {"id": "dest", "label": "Where are you going?", "options": ["Europe", "Outside Europe"]},
        {"id": "len", "label": "How long is the trip?",
         "options": ["Weekend (≤ 3 days)", "Up to a week", "Up to a month"]},
        {"id": "freq", "label": "How often do you fly per year?",
         "options": ["Just this once", "2–3 times", "More often"],
         "note": "Helps us check if yearly cover is cheaper for you."},
    ],
    "vehicle": [
        {"id": "cond", "label": "Is the car new or second-hand?", "options": ["New", "Second-hand"]},
        {"id": "fuel", "label": "What does it run on?", "options": ["Petrol", "Diesel", "Hybrid", "Electric"]},
        {"id": "zip", "label": "Where is it parked at night?", "options": CAR_ZIPS,
         "option_notes": {
             "1000 Brussels": {"text": "Dense city traffic and more theft claims: slightly higher premium.", "level": "warn"},
             "2000 Antwerp": {"text": "Busy urban area: slightly higher premium.", "level": "warn"},
             "3000 Leuven": {"text": "Average claims in this area.", "level": "info"},
             "9000 Ghent": {"text": "Average claims in this area.", "level": "info"},
             "8400 Ostend": {"text": "Lower claim frequency in this area.", "level": "info"},
         }},
    ],
    "rental": [
        {"id": "sit", "label": "Are you renting or buying?", "options": ["Renting", "Buying"]},
        {"id": "zip", "label": "Postcode of the new home", "options": HOME_ZIPS,
         "option_notes": {
             "3000 Leuven": {"text": "Low flood risk in most of Leuven.", "level": "info"},
             "9000 Ghent": {"text": "Some streets near the Leie and Scheldt have moderate flood risk. Water damage cover advised.", "level": "warn"},
             "2000 Antwerp": {"text": "Moderate storm and water risk in parts of the city. Water damage cover advised.", "level": "warn"},
             "8620 Nieuwpoort": {"text": "Coastal area with elevated flood and storm risk. Water and storm cover strongly advised.", "level": "warn"},
             "4000 Liège": {"text": "Region with a history of river flooding. Water damage cover strongly advised.", "level": "warn"},
         }},
        {"id": "type", "label": "What kind of home?", "options": ["Apartment", "House"]},
        {"id": "size", "label": "How many bedrooms?", "options": ["1", "2", "3", "4+"]},
    ],
    "payroll": [
        {"id": "event", "label": "What was this payment?",
         "options": ["13th month / year-end bonus", "Salary increase", "Promotion", "One-off bonus"]},
        {"id": "cafe", "label": "What did you pick in your cafeteria plan?",
         "options": ["E-bike lease", "Extra holidays", "Pension top-up", "Laptop / smartphone", "Nothing yet"]},
        {"id": "pens", "label": "Pension savings this year?", "options": ["Not yet", "Partly", "Already maxed"]},
        {"id": "dep", "label": "Does anyone rely on your income?", "options": ["Just me", "My partner", "Partner & kids"]},
    ],
}


class AnswerError(ValueError):
    """Raised when answers are missing, unknown or not one of the options."""


def validate_answers(category: str, answers: Dict[str, str]) -> Dict[str, str]:
    questions = QUESTIONS[category]
    known = {q["id"] for q in questions}
    unknown = set(answers) - known
    if unknown:
        raise AnswerError(f"Unknown question(s): {', '.join(sorted(unknown))}")
    clean = {}
    for q in questions:
        value = answers.get(q["id"])
        if value is None or value == "":
            raise AnswerError(f"Please answer: {q['label']}")
        if value not in q["options"]:
            raise AnswerError(f"'{value}' is not a valid answer to: {q['label']}")
        clean[q["id"]] = value
    return clean


# --------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------
def _item(id_, name, price: Decimal, unit, features, recommended, product_type, kind="policy",
          charge_now: Optional[Decimal] = None) -> dict:
    price_cents = euros_to_cents(price)
    return {
        "id": id_, "name": name, "price_cents": price_cents, "price_unit": unit,
        "charge_now_cents": price_cents if charge_now is None else euros_to_cents(charge_now),
        "features": features, "recommended": recommended, "product_type": product_type, "kind": kind,
    }


def _flight(a: Dict[str, str], ctx: dict) -> dict:
    trip = {"Weekend (≤ 3 days)": D("4.90"), "Up to a week": D("7.90"), "Up to a month": D("17.90")}[a["len"]]
    dest = D("2.1") if a["dest"] == "Outside Europe" else D("1")
    student = ctx.get("segment") == "student"
    discount = D("0.85") if student else D("1")
    ticket = D(ctx["amount_cents"]) / 100
    rate = D("0.70")
    basic = trip * dest * rate * discount
    plus = (trip * dest + ticket * D("0.06")) * rate * discount
    annual = (D("99") if a["dest"] == "Outside Europe" else D("69")) * discount
    yearly_fits = a["freq"] != "Just this once"
    year_name = "Student Travel Year" if student else "Travel Year"
    factors = [f"Destination: {a['dest']}", f"Trip length: {a['len']}",
               f"Ticket value {fmt_eur(ctx['amount_cents'])} sets the cancellation limit"]
    if student:
        factors.insert(0, "Student rate −15%")
    return {
        "selection": "single",
        "items": [
            _item("trip_basic", "Trip Basic", basic, "this trip",
                  ["Medical costs abroad", "Repatriation", "Lost or delayed luggage"], False, "travel"),
            _item("trip_plus", "Trip Plus", plus, "this trip",
                  ["Everything in Basic", f"Cancellation up to {fmt_eur(ctx['amount_cents'])} (ticket value)",
                   "24/7 assistance"], not yearly_fits, "travel"),
            _item("travel_year", year_name, annual, "per year",
                  ["Every trip for 12 months", "Cancellation included", "Great if you fly 2+ times a year"],
                  yearly_fits, "travel"),
        ],
        "factors": factors,
    }


def _vehicle(a: Dict[str, str], ctx: dict) -> dict:
    region = {"1000 Brussels": D("1.30"), "2000 Antwerp": D("1.15"), "3000 Leuven": D("1.00"),
              "9000 Ghent": D("1.05"), "8400 Ostend": D("0.93")}[a["zip"]]
    fuel = {"Petrol": D("1.00"), "Diesel": D("1.05"), "Hybrid": D("0.95"), "Electric": D("0.90")}[a["fuel"]]
    value_cents = ctx.get("vehicle_value_cents") or ctx["amount_cents"]
    value = D(value_cents) / 100
    liability = D("34") * region * fuel
    mini = liability + value * D("0.017") / 12
    full = liability + value * D("0.033") / 12
    new = a["cond"] == "New"
    return {
        "selection": "single",
        "items": [
            _item("car_liability", "Liability only", liability, "/ month",
                  ["Legally required cover", "Damage you cause to others"], False, "car"),
            _item("car_mini", "Mini-omnium", mini, "/ month",
                  ["Liability", "Theft, fire, glass, storm", "Collision with animals"], not new, "car"),
            _item("car_full", "Full omnium", full, "/ month",
                  ["Everything in Mini-omnium", "Own damage, even if it's your fault", "Replacement vehicle"],
                  new, "car"),
        ],
        "factors": [f"Parked in {a['zip']} (regional factor ×{region})", f"{a['fuel']} engine",
                    f"{a['cond']} car · value {fmt_eur(value_cents)}",
                    "Full omnium recommended for new cars" if new else "Mini-omnium recommended for second-hand cars"],
    }


def _rental(a: Dict[str, str], ctx: dict) -> dict:
    risk = {"3000 Leuven": D("1.00"), "9000 Ghent": D("1.08"), "2000 Antwerp": D("1.10"),
            "8620 Nieuwpoort": D("1.35"), "4000 Liège": D("1.28")}[a["zip"]]
    size = {"1": D("0.85"), "2": D("1.00"), "3": D("1.20"), "4+": D("1.45")}[a["size"]]
    home = D("1.4") if a["type"] == "House" else D("1")
    buying = a["sit"] == "Buying"
    own = D("2.3") if buying else D("1")
    base = D("6.5") * risk * size * home * own
    water_risk = risk >= D("1.08")
    bedrooms = "1 bedroom" if a["size"] == "1" else f"{a['size']} bedrooms"
    return {
        "selection": "single",
        "items": [
            _item("home_base", "Fire (building)" if buying else "Tenant fire liability", base, "/ month",
                  ["Fire, explosion, smoke",
                   "Rebuild value of the home" if buying else "Damage to the landlord's property",
                   "Usually required by the lender" if buying else "Usually required by the lease"],
                  not water_risk, "home"),
            _item("home_comfort", "Comfort", base * D("1.5"), "/ month",
                  ["Everything in the base cover", "Water damage & flooding", "Storm, hail and your contents"],
                  water_risk, "home"),
            _item("home_complete", "Complete", base * D("1.9"), "/ month",
                  ["Everything in Comfort", "Theft & vandalism", "Legal assistance"], False, "home"),
        ],
        "factors": [f"Regional risk {a['zip']}: ×{risk}", f"{a['type']}, {bedrooms}", a["sit"],
                    "Comfort recommended because of local water risk" if water_risk
                    else "Base cover fits the low local risk"],
    }


def pension_room(answer: str, ytd_cents: int) -> int:
    """How much to suggest for pension savings, in cents (0 = nothing)."""
    remaining = max(PENSION_SAVINGS_CAP_CENTS - ytd_cents, 0)
    if answer == "Already maxed":
        return 0
    if answer == "Partly" and ytd_cents == 0:
        # Saving elsewhere: suggest topping up half the yearly amount.
        return PENSION_SAVINGS_CAP_CENTS // 2
    return remaining


def _payroll(a: Dict[str, str], ctx: dict) -> dict:
    ytd = ctx.get("pension_ytd_cents", 0)
    owned = set(ctx.get("owned_products") or [])
    raise_ = a["event"] in ("Salary increase", "Promotion")
    alone = a["dep"] == "Just me"
    items = []
    room = pension_room(a["pens"], ytd)
    if room:
        tax_back = int((D(room) * PENSION_TAX_RATE).quantize(D("1"), rounding=ROUND_HALF_UP))
        items.append(_item(
            "pension", "KBC pension savings", D(room) / 100, "once",
            [f"Belgian income tax runs up to 50%. A deposit of {fmt_eur(room)} earns about "
             f"{fmt_eur(tax_back)} back as a 30% tax reduction",
             "Deposit now, the benefit comes with next year's tax return",
             "Choose a low- or medium-risk fund"],
            True, "pension", kind="transfer"))
    if a["cafe"] == "E-bike lease" and "bike" not in owned:
        items.append(_item("bike", "E-bike theft & damage cover", D("7.90"), "/ month",
                           ["Theft, vandalism and accident damage", "Pick-up assistance if you break down",
                            "Check your lease: if theft isn't included, add it here"], True, "bike"))
    if a["cafe"] == "Laptop / smartphone" and "device" not in owned:
        items.append(_item("device", "Device cover", D("4.50"), "/ month",
                           ["Theft and accidental damage", "Covers the device from your cafeteria plan"],
                           True, "device"))
    if "income" not in owned:
        items.append(_item(
            "income", "Guaranteed income insurance", D("16") if alone else D("22"), "/ month",
            ["Your salary went up. Keep up to 80% of your new income if you can't work" if raise_
             else "Keep up to 80% of your income if illness keeps you from working",
             "Tops up the legal sickness benefit" if alone else "Protects the people who rely on you"],
            raise_ or not alone, "income"))
    items.append(_item("buffer", "Emergency buffer", D("1000"), "to savings",
                       ["Move part of the payout to a KBC savings account",
                        "Around three months of fixed costs is a healthy target"],
                       not raise_, "savings", kind="transfer"))
    return {
        "selection": "multiple",
        "items": items,
        "factors": [f"Payout {fmt_eur(ctx['amount_cents'])} · {a['event']}",
                    f"Pension savings so far this year: {fmt_eur(ytd)}",
                    f"Cafeteria plan: {a['cafe']}", f"People relying on your income: {a['dep']}"],
    }


PRICERS = {"flight": _flight, "vehicle": _vehicle, "rental": _rental, "payroll": _payroll}


def price(category: str, answers: Dict[str, str], ctx: dict) -> dict:
    return PRICERS[category](validate_answers(category, answers), ctx)


def cheapest(category: str, prefilled: Dict[str, str], ctx: dict) -> dict:
    """The cheapest item over every combination of the questions not yet answered."""
    questions = QUESTIONS[category]
    open_q = [q for q in questions if q["id"] not in prefilled]
    best = None
    for combo in itertools.product(*[q["options"] for q in open_q]):
        answers = dict(prefilled, **{q["id"]: v for q, v in zip(open_q, combo)})
        for item in price(category, answers, ctx)["items"]:
            if best is None or item["price_cents"] < best["price_cents"]:
                best = item
    return best
