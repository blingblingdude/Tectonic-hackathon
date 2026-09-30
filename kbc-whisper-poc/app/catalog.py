"""Static reference data for the demo: partners, personas, demo scenarios and
the tiny "company registry" the detection rules look things up in.

In a real bank these would come from core banking, the KBO/BCE company
registry and a partner-management system. Here they are plain Python data so
the whole proof of concept runs from one folder.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# Partner apps a customer can connect (with consent)
# --------------------------------------------------------------------------
PARTNERS = [
    {"id": "ryanair", "name": "Ryanair", "icon": "✈️",
     "scopes": "Flight bookings: destination, dates, passengers"},
    {"id": "sdworx", "name": "SD Worx", "icon": "💼",
     "scopes": "Payslips, bonuses, salary changes, cafeteria plan choices"},
    {"id": "dealer", "name": "Auto Verhaegen", "icon": "🚗",
     "scopes": "Vehicle orders: new/used, fuel type, value"},
    {"id": "huurhub", "name": "HuurHub", "icon": "🏠",
     "scopes": "Lease details: address, type, size"},
]
PARTNERS_BY_ID = {p["id"]: p for p in PARTNERS}

# --------------------------------------------------------------------------
# Mock company registry (think KBO/BCE): counterparty name -> activity code.
# Auto Verhaegen is a fictional dealer used for the demo.
# --------------------------------------------------------------------------
COMPANY_REGISTRY = {
    "AUTO VERHAEGEN NV": {"nace": "45.11", "activity": "Sale of cars and light motor vehicles",
                          "partner_id": "dealer"},
}
CAR_DEALER_NACE = "45.11"

# Belgian social secretariats / payroll providers recognised on statements.
PAYROLL_PROVIDERS = {
    "SD WORX": "sdworx",
    "SECUREX": None,
    "PARTENA": None,
    "ACERTA": None,
    "LIANTIS": None,
    "GROUP S": None,
}

# --------------------------------------------------------------------------
# Demo personas. `history` is seeded as already-booked transactions
# (days_ago, direction, counterparty, amount_cents, category, icon, message).
# --------------------------------------------------------------------------
CUSTOMERS = [
    {
        "id": "lotte", "first_name": "Lotte", "last_name": "Janssens", "age": 21,
        "segment": "student", "iban": "BE68 7340 1234 4512", "postcode": "3000 Leuven",
        "balance_cents": 124055, "pension_ytd_cents": 0,
        "history": [
            (1, "out", "Colruyt Leuven", 3840, "Groceries", "🛒", ""),
            (3, "out", "De Lijn", 250, "Transport", "🚌", ""),
            (9, "in", "Student job · Café Commerce", 62000, "Income", "💶", "Loon augustus"),
            (12, "out", "Café Commerce", 680, "Food & drink", "☕", ""),
        ],
    },
    {
        "id": "pieter", "first_name": "Pieter", "last_name": "Claes", "age": 38,
        "segment": "adult", "iban": "BE71 7350 2233 4512", "postcode": "3000 Leuven",
        "balance_cents": 2431020, "pension_ytd_cents": 0,
        "history": [
            (1, "out", "Delhaize Kessel-Lo", 8415, "Groceries", "🛒", ""),
            (4, "out", "Fluvius", 14200, "Utilities", "💡", "Voorschot energie"),
            (6, "in", "Salary · Brightline NV", 345000, "Income", "💶", "Loon september"),
            (11, "out", "Total Energies Leuven", 7230, "Fuel", "⛽", ""),
        ],
    },
    {
        "id": "sarah", "first_name": "Sarah", "last_name": "Wouters", "age": 31,
        "segment": "adult", "iban": "BE09 7360 5566 4512", "postcode": "3000 Leuven",
        "balance_cents": 612075, "pension_ytd_cents": 0,
        "history": [
            (1, "out", "Albert Heijn Leuven", 5290, "Groceries", "🛒", ""),
            (2, "out", "Verhuisfirma Dewitte", 45000, "Moving", "📦", "Voorschot verhuis"),
            (6, "in", "Salary · Novacare VZW", 298000, "Income", "💶", "Loon september"),
            (10, "out", "IKEA Zaventem", 31990, "Home", "🛋️", ""),
        ],
    },
    {
        "id": "elise", "first_name": "Elise", "last_name": "Maes", "age": 36,
        "segment": "adult", "iban": "BE44 7370 8899 4512", "postcode": "9000 Ghent",
        "balance_cents": 341230, "pension_ytd_cents": 0,
        "history": [
            (2, "out", "Delhaize Gent", 6420, "Groceries", "🛒", ""),
            (5, "in", "SD WORX NV", 312000, "Income · Payroll", "💼", "LOON SEPTEMBER 2026"),
            (35, "in", "SD WORX NV", 312000, "Income · Payroll", "💼", "LOON AUGUSTUS 2026"),
            (66, "in", "SD WORX NV", 312000, "Income · Payroll", "💼", "LOON JULI 2026"),
        ],
    },
]
CUSTOMERS_BY_ID = {c["id"]: c for c in CUSTOMERS}

# --------------------------------------------------------------------------
# Demo scenarios: which persona does what. The transaction below is sent to
# the API exactly like a core-banking system would post it. The backend then
# classifies it from these raw fields only.
# --------------------------------------------------------------------------
GROUPS = [
    {"id": "flight", "icon": "✈️", "label": "Student books a flight",
     "subtitle": "Lotte, 21, pays Ryanair"},
    {"id": "adult", "icon": "🚗 🏠", "label": "Adult buys a car or rents a home",
     "subtitle": "Pieter pays a dealer · Sarah pays a deposit"},
    {"id": "payroll", "icon": "💼", "label": "Salary or bonus via SD Worx",
     "subtitle": "Elise, 36, receives her 13th month"},
]

SCENARIOS = [
    {
        "id": "flight", "group": "flight", "label": "✈️ Books a flight",
        "customer_id": "lotte", "partner_id": "ryanair", "icon": "✈️",
        "transaction": {"direction": "out", "counterparty": "RYANAIR DAC", "amount_cents": 18420,
                        "mcc": "4511", "message": "Online card payment", "channel": "card"},
        "explainer": {
            "recognition": "The bank sees a card payment to RYANAIR DAC with merchant category 4511 (airlines). "
                           "No partnership with Ryanair is needed: a flight is a flight. "
                           "Details it can't know, it simply asks.",
            "partner": "With the customer's consent, booking details (destination, dates, passengers) flow in "
                       "from the airline. The app pre-fills everything and quotes instantly.",
        },
    },
    {
        "id": "car", "group": "adult", "label": "🚗 Buys a car",
        "customer_id": "pieter", "partner_id": "dealer", "icon": "🚗",
        "transaction": {"direction": "out", "counterparty": "AUTO VERHAEGEN NV", "amount_cents": 1845000,
                        "mcc": None, "message": "Invoice 2026-0914 order 5531", "channel": "transfer"},
        "explainer": {
            "recognition": "The bank sees a large one-off transfer to a company registered as a car dealer "
                           "(NACE 45.11). It can't know the model, so it asks a few quick questions.",
            "partner": "The dealer shares the order form (with consent): new or used, fuel type, value. "
                       "The app only needs to confirm where the car will be parked.",
        },
    },
    {
        "id": "house", "group": "adult", "label": "🏠 Rents a home",
        "customer_id": "sarah", "partner_id": "huurhub", "icon": "🏠",
        "transaction": {"direction": "out", "counterparty": "J. PEETERS", "amount_cents": 285000,
                        "mcc": None, "message": "huurwaarborg Kerkstraat 12", "channel": "transfer"},
        "explainer": {
            "recognition": "The transfer message contains \"huurwaarborg\" (rental deposit) and a street address. "
                           "That's enough to know this is a new rental. The app asks for the postcode to assess "
                           "regional risks like flooding.",
            "partner": "The lease details arrive via a connected rental platform (with consent): address, type, "
                       "size. The app prices fire insurance straight away using the region's risk profile.",
        },
    },
    {
        "id": "payroll", "group": "payroll", "label": "💼 Bonus via SD Worx",
        "customer_id": "elise", "partner_id": "sdworx", "icon": "💼",
        "transaction": {"direction": "in", "counterparty": "SD WORX NV", "amount_cents": 486000,
                        "mcc": None, "message": "EINDEJAARSPREMIE 2026 / 13DE MAAND", "channel": "transfer"},
        "explainer": {
            "recognition": "The bank sees an incoming credit from SD WORX NV, the usual payroll provider, well above "
                           "the usual salary and with \"EINDEJAARSPREMIE\" (year-end bonus) in the message. "
                           "No SD Worx connection needed: it simply asks about the cafeteria plan.",
            "partner": "With consent, the SD Worx app shares payslip data: bonus type, salary changes and cafeteria "
                       "plan choices (e.g. an e-bike lease). KBC suggests the right actions without asking much.",
        },
    },
]
SCENARIOS_BY_ID = {s["id"]: s for s in SCENARIOS}

# --------------------------------------------------------------------------
# What each partner "knows" about our personas (mock partner APIs).
# --------------------------------------------------------------------------
PARTNER_RECORDS = {
    ("ryanair", "lotte"): {
        "booking_ref": "RYR8Q2KX", "route": "BRU → BCN", "destination": "Barcelona", "region": "Europe",
        "depart": "2026-11-14", "return": "2026-11-18", "passengers": 1, "fare_cents": 18420,
    },
    ("dealer", "pieter"): {
        "order_ref": "5531", "condition": "New", "fuel": "Electric", "catalogue_value_cents": 1845000,
    },
    ("huurhub", "sarah"): {
        "lease_ref": "HH-20931", "deposit_cents": 285000, "address": "Kerkstraat 12", "postcode": "9000 Ghent",
        "home_type": "Apartment", "bedrooms": "2",
    },
    ("sdworx", "elise"): {
        "payslip_ref": "SDW-2026-13M", "net_cents": 486000, "payout_type": "13th month / year-end bonus",
        "cafeteria_choice": "E-bike lease", "gross_salary_change_pct": 6, "salary_change_since": "January",
    },
}
