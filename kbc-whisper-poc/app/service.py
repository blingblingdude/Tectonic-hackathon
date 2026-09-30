"""Business operations. Each function receives an open connection; the HTTP
layer decides whether that is a read or an atomic write transaction."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from . import catalog, partners, products, whispers
from .db import iso, log_event, now
from .detection import (CATEGORY_LABELS, MIN_FLIGHT_CENTS, Classification, bonus_keyword, classify,
                        payroll_provider)
from .money import fmt_eur


# --------------------------------------------------------------------------
# Errors (mapped to HTTP status codes in main.py)
# --------------------------------------------------------------------------
class DomainError(Exception):
    status = 400


class NotFound(DomainError):
    status = 404


class Conflict(DomainError):
    status = 409


class Gone(DomainError):
    status = 410


class Invalid(DomainError):
    status = 422


TX_STYLE = {  # how a classified transaction is labelled in the statement
    "flight": ("Travel", "✈️"), "vehicle": ("Vehicle", "🚗"),
    "rental": ("Housing", "🏠"), "payroll": ("Income · Payroll", "💼"),
}
PRODUCT_ICONS = {"travel": "☂️", "car": "🛡️", "home": "🛡️", "income": "🛡️", "bike": "🚲",
                 "device": "💻", "pension": "🏦", "savings": "🐷"}


# --------------------------------------------------------------------------
# Customers, consents, mutes
# --------------------------------------------------------------------------
def customer_row(conn: sqlite3.Connection, customer_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not row:
        raise NotFound(f"Unknown customer '{customer_id}'")
    return row


def tx_view(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in ("id", "booked_at", "direction", "counterparty", "amount_cents", "mcc",
                                "message", "channel", "category", "icon")}


def customer_view(conn: sqlite3.Connection, customer_id: str, limit: int = 8) -> dict:
    c = customer_row(conn, customer_id)
    txs = conn.execute(
        "SELECT * FROM transactions WHERE customer_id = ? ORDER BY booked_at DESC, id DESC LIMIT ?",
        (customer_id, limit)).fetchall()
    contracts = conn.execute(
        "SELECT item_id, product_type, kind, name, price_cents, price_unit, created_at FROM contracts "
        "WHERE customer_id = ? ORDER BY id DESC", (customer_id,)).fetchall()
    iban = c["iban"]
    return {
        "id": c["id"], "first_name": c["first_name"], "last_name": c["last_name"],
        "initials": (c["first_name"][:1] + c["last_name"][:1]).upper(), "age": c["age"],
        "segment": c["segment"], "postcode": c["postcode"],
        "iban_masked": f"{iban[:9]} •••• {iban[-4:]}",
        "balance_cents": c["balance_cents"], "pension_ytd_cents": c["pension_ytd_cents"],
        "transactions": [tx_view(t) for t in txs],
        "contracts": [dict(r) for r in contracts],
    }


def _partner(partner_id: str) -> dict:
    partner = catalog.PARTNERS_BY_ID.get(partner_id)
    if not partner:
        raise NotFound(f"Unknown partner '{partner_id}'")
    return partner


def consents_view(conn: sqlite3.Connection, customer_id: str) -> List[dict]:
    customer_row(conn, customer_id)
    rows = {r["partner_id"]: r for r in conn.execute(
        "SELECT partner_id, connected, updated_at FROM consents WHERE customer_id = ?", (customer_id,))}
    out = []
    for p in catalog.PARTNERS:
        r = rows.get(p["id"])
        out.append({**p, "connected": bool(r and r["connected"]), "updated_at": r["updated_at"] if r else None})
    return out


def set_consent(conn: sqlite3.Connection, customer_id: str, partner_id: str, connected: bool) -> dict:
    customer_row(conn, customer_id)
    partner = _partner(partner_id)
    ts = iso(now())
    conn.execute(
        "INSERT INTO consents (customer_id, partner_id, connected, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(customer_id, partner_id) DO UPDATE SET connected = excluded.connected, "
        "updated_at = excluded.updated_at", (customer_id, partner_id, int(connected), ts))
    log_event(conn, "consent_granted" if connected else "consent_revoked", customer_id, partner=partner_id)
    return {**partner, "connected": connected, "updated_at": ts}


def is_connected(conn: sqlite3.Connection, customer_id: str, partner_id: Optional[str]) -> bool:
    if not partner_id:
        return False
    row = conn.execute("SELECT connected FROM consents WHERE customer_id = ? AND partner_id = ?",
                       (customer_id, partner_id)).fetchone()
    return bool(row and row["connected"])


def mutes_view(conn: sqlite3.Connection, customer_id: str) -> List[dict]:
    customer_row(conn, customer_id)
    return [dict(r) for r in conn.execute(
        "SELECT category, created_at FROM mutes WHERE customer_id = ? ORDER BY created_at", (customer_id,))]


def unmute(conn: sqlite3.Connection, customer_id: str, category: str) -> dict:
    customer_row(conn, customer_id)
    if category not in CATEGORY_LABELS:
        raise NotFound(f"Unknown category '{category}'")
    removed = conn.execute("DELETE FROM mutes WHERE customer_id = ? AND category = ?",
                           (customer_id, category)).rowcount
    if removed:
        log_event(conn, "whispers_unmuted", customer_id, category=category)
    return {"category": category, "muted": False}


# --------------------------------------------------------------------------
# Transactions + analysis (the heart of the demo)
# --------------------------------------------------------------------------
def _signal(stage: str, title: str, detail: str, source: str, status: str = "ok") -> dict:
    """source: bank (transaction data) | partner (connected app) | kbc (KBC's own customer data)."""
    return {"stage": stage, "title": title, "detail": detail, "source": source, "status": status}


def _previous_payroll_credits(conn: sqlite3.Connection, customer_id: str, counterparty: str) -> List[int]:
    provider = payroll_provider(counterparty)
    rows = conn.execute(
        "SELECT counterparty, amount_cents, message FROM transactions WHERE customer_id = ? AND direction = 'in' "
        "ORDER BY booked_at, id", (customer_id,)).fetchall()
    # Regular salary only: earlier bonuses must not inflate the "usual salary" baseline.
    return [r["amount_cents"] for r in rows
            if provider and payroll_provider(r["counterparty"]) == provider and not bonus_keyword(r["message"])[0]]


def owned_products(conn: sqlite3.Connection, customer_id: str) -> List[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT product_type FROM contracts WHERE customer_id = ?",
                                       (customer_id,))]


def _has_contract(conn, customer_id: str, product_type: str, item_id: Optional[str] = None,
                  since: Optional[datetime] = None) -> Optional[sqlite3.Row]:
    sql = "SELECT name FROM contracts WHERE customer_id = ? AND product_type = ?"
    args: list = [customer_id, product_type]
    if item_id:
        sql += " AND item_id = ?"
        args.append(item_id)
    if since:
        sql += " AND created_at >= ?"
        args.append(iso(since))
    return conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()


def _relevance(conn, cust: sqlite3.Row, cls: Classification, record: Optional[dict]):
    """Return (suppress_code or None, one-line explanation)."""
    cid, name = cust["id"], cust["first_name"]
    if conn.execute("SELECT 1 FROM mutes WHERE customer_id = ? AND category = ?", (cid, cls.category)).fetchone():
        return "muted", f"{name} turned off {CATEGORY_LABELS[cls.category]} suggestions"
    if cls.category == "flight":
        yearly = _has_contract(conn, cid, "travel", "travel_year", now() - timedelta(days=365))
        if yearly:
            return "covered", f"Already covered by {yearly['name']} (every trip for 12 months)"
        profile = "Student profile" if cust["segment"] == "student" else "Adult profile"
        return None, f"{profile} · no yearly travel insurance on file · amount above {fmt_eur(MIN_FLIGHT_CENTS, whole=True)}"
    if cls.category == "vehicle":
        car = _has_contract(conn, cid, "car")
        if car:
            return "covered", f"Already has KBC car insurance ({car['name']})"
        return None, "No car insurance at KBC · liability cover is mandatory before driving"
    if cls.category == "rental":
        home = _has_contract(conn, cid, "home")
        if home:
            return "covered", f"Already has KBC home insurance ({home['name']})"
        return None, "Most leases require tenant fire insurance · none found at KBC"
    # payroll
    ytd = cust["pension_ytd_cents"]
    parts = ["No pension savings deposit yet this year" if ytd == 0
             else "Pension savings already maxed this year" if ytd >= products.PENSION_SAVINGS_CAP_CENTS
             else f"Pension savings not maxed yet ({fmt_eur(ytd)} so far)",
             "extra income is taxed at the top rate (up to 50%)"]
    if record and record["cafeteria_choice"] == "E-bike lease" and not _has_contract(conn, cid, "bike"):
        parts.append("leased e-bike not insured at KBC")
    if record and record.get("gross_salary_change_pct") and not _has_contract(conn, cid, "income"):
        parts.append("higher salary not yet protected")
    return None, " · ".join(parts)


def post_transaction(conn: sqlite3.Connection, customer_id: str, tx_in: dict) -> dict:
    cust = customer_row(conn, customer_id)
    amount = tx_in["amount_cents"]
    if tx_in["direction"] == "out" and amount > cust["balance_cents"]:
        raise Conflict(f"Payment declined: insufficient funds (balance {fmt_eur(cust['balance_cents'])}). "
                       "Use 'Reset demo data' to start over.")

    prev_payroll = (_previous_payroll_credits(conn, customer_id, tx_in["counterparty"])
                    if tx_in["direction"] == "in" else [])
    cls, why_not = classify(tx_in, prev_payroll)

    if cls:
        category, icon = TX_STYLE[cls.category]
    elif tx_in["direction"] == "in":
        category, icon = "Income", "💶"
    else:
        category, icon = "Payment", "💳"
    signed = amount if tx_in["direction"] == "in" else -amount
    cur = conn.execute(
        "INSERT INTO transactions (customer_id, booked_at, direction, counterparty, amount_cents, mcc, message,"
        " channel, category, icon) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (customer_id, iso(now()), tx_in["direction"], tx_in["counterparty"], signed, tx_in.get("mcc"),
         tx_in.get("message") or "", tx_in["channel"], category, icon))
    tx_id = cur.lastrowid
    conn.execute("UPDATE customers SET balance_cents = balance_cents + ? WHERE id = ?", (signed, customer_id))
    tx_row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    log_event(conn, "transaction_posted", customer_id, transaction_id=tx_id, amount_cents=signed,
              counterparty=tx_in["counterparty"])

    analysis = _analyse(conn, cust, tx_in, tx_id, cls, why_not)
    balance = conn.execute("SELECT balance_cents FROM customers WHERE id = ?", (customer_id,)).fetchone()[0]
    return {"transaction": tx_view(tx_row), "balance_cents": balance, "analysis": analysis}


def _analyse(conn, cust: sqlite3.Row, tx: dict, tx_id: int, cls: Optional[Classification], why_not: str) -> dict:
    cid = cust["id"]
    amount = fmt_eur(tx["amount_cents"])
    if tx["direction"] == "in":
        received = _signal("received", "Payment received", f"Incoming {amount} from {tx['counterparty']}", "bank")
    elif tx["channel"] == "card":
        received = _signal("received", "Payment detected", f"Card payment {amount} to {tx['counterparty']}", "bank")
    else:
        received = _signal("received", "Payment detected", f"Transfer {amount} to {tx['counterparty']}", "bank")
    signals = [received]

    if not cls:
        signals.append(_signal("classified", "Not classified", why_not, "bank", "skipped"))
        signals.append(_signal("decision", "No whisper", "Nothing relevant to suggest for this transaction",
                               "bank", "skipped"))
        log_event(conn, "whisper_not_relevant", cid, transaction_id=tx_id, reason=why_not)
        return {"category": None, "category_label": None, "confidence": None, "data_source": "recognition",
                "partner_id": None,
                "signals": signals, "decision": {"shown": False, "code": "not_relevant", "reason": why_not},
                "whisper": None}

    partner = catalog.PARTNERS_BY_ID.get(cls.partner_id or "")
    connected = is_connected(conn, cid, cls.partner_id)
    evidence = cls.evidence
    if partner and not connected:
        evidence += f" · no {partner['name']} connection needed"
    signals.append(_signal("classified", "Classified by the bank", evidence, "bank"))

    record = None
    if partner and connected:
        record = partners.fetch(partner["id"], cid, tx, cls)
        if record:
            signals.append(_signal("enriched", f"Enriched via {partner['name']} link",
                                   partners.summary(partner["id"], record), "partner"))
        else:
            signals.append(_signal("enriched", f"{partner['name']} connected",
                                   "No matching record at the partner, so we use bank data only", "partner", "skipped"))
    data_source = "partner" if record else "recognition"

    code, relevance_text = _relevance(conn, cust, cls, record)
    if code:
        signals.append(_signal("relevance", "Relevance check", relevance_text, "kbc", "suppressed"))
        signals.append(_signal("decision", "Whisper suppressed", relevance_text, "kbc", "suppressed"))
        log_event(conn, "whisper_suppressed", cid, transaction_id=tx_id, category=cls.category, reason=code)
        return {"category": cls.category, "category_label": CATEGORY_LABELS[cls.category],
                "confidence": cls.confidence, "data_source": data_source,
                "partner_id": cls.partner_id, "signals": signals,
                "decision": {"shown": False, "code": code, "reason": relevance_text}, "whisper": None}
    signals.append(_signal("relevance", "Relevant for this customer", relevance_text, "kbc"))

    cust_dict = {**dict(cust), "owned_products": owned_products(conn, cid)}
    form = whispers.build_form(cls, record, cust_dict, data_source)
    open_n = sum(1 for q in form["questions"] if not q["prefill"])
    ctx = {"category": cls.category, "amount_cents": tx["amount_cents"], "segment": cust["segment"],
           "vehicle_value_cents": record.get("catalogue_value_cents") if record else None,
           "data_source": data_source, "facts": cls.facts}
    teaser = whispers.teaser(cls, form, {**ctx, "pension_ytd_cents": cust["pension_ytd_cents"]},
                             data_source, cust["segment"])
    questions_txt = f"{open_n} question{'s' if open_n != 1 else ''}"
    if record:
        decision_detail = (f"Pre-filled with {partner['name']} data · "
                           + (f"{questions_txt} left" if open_n else "nothing left to ask, just confirm"))
    else:
        decision_detail = f"One quiet suggestion, {questions_txt} to ask, easy to dismiss"
    signals.append(_signal("decision", "Whisper shown", decision_detail, "kbc"))

    whisper_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO whispers (id, customer_id, transaction_id, category, status, context, created_at) "
        "VALUES (?, ?, ?, ?, 'shown', ?, ?)",
        (whisper_id, cid, tx_id, cls.category, json.dumps(ctx, ensure_ascii=False), iso(now())))
    log_event(conn, "whisper_shown", cid, whisper_id=whisper_id, category=cls.category, data_source=data_source)

    text = whispers.copy(cls, record, tx, cust_dict)
    reasons = [evidence] + ([partners.summary(partner["id"], record)] if record else []) + [relevance_text]
    return {
        "category": cls.category, "category_label": CATEGORY_LABELS[cls.category],
        "confidence": cls.confidence, "data_source": data_source,
        "partner_id": cls.partner_id, "signals": signals,
        "decision": {"shown": True, "code": "shown", "reason": decision_detail},
        "whisper": {"id": whisper_id, "category": cls.category, "pill": whispers.PILLS[cls.category],
                    **text, "teaser": teaser, "reasons": reasons,
                    "privacy_note": whispers.privacy_note(cls, data_source), "form": form},
    }


# --------------------------------------------------------------------------
# Whisper feedback, quotes, acceptance
# --------------------------------------------------------------------------
def whisper_row(conn, whisper_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM whispers WHERE id = ?", (whisper_id,)).fetchone()
    if not row:
        raise NotFound("Unknown whisper")
    return row


def whisper_feedback(conn, whisper_id: str, action: str) -> dict:
    w = whisper_row(conn, whisper_id)
    if w["status"] == "accepted":
        raise Conflict("This suggestion was already accepted")
    if action == "opened" and w["status"] not in ("shown", "opened"):
        raise Conflict(f"Cannot open a whisper that is {w['status']}")
    conn.execute("UPDATE whispers SET status = ? WHERE id = ?", (action, whisper_id))
    if action == "muted":
        conn.execute("INSERT OR IGNORE INTO mutes (customer_id, category, created_at) VALUES (?, ?, ?)",
                     (w["customer_id"], w["category"], iso(now())))
    log_event(conn, f"whisper_{action}", w["customer_id"], whisper_id=whisper_id, category=w["category"])
    return {"whisper_id": whisper_id, "status": action, "category": w["category"],
            "muted": action == "muted"}


def create_quote(conn, whisper_id: str, answers: Dict[str, str]) -> dict:
    w = whisper_row(conn, whisper_id)
    if w["status"] in ("dismissed", "muted"):
        raise Conflict(f"This suggestion was {w['status']}")
    if w["status"] == "accepted":
        raise Conflict("This suggestion was already accepted")
    cust = customer_row(conn, w["customer_id"])
    ctx = {**json.loads(w["context"]), "pension_ytd_cents": cust["pension_ytd_cents"],
           "owned_products": owned_products(conn, cust["id"])}
    try:
        priced = products.price(w["category"], answers, ctx)
    except products.AnswerError as exc:
        raise Invalid(str(exc)) from exc
    if not priced["items"]:
        raise Conflict("Nothing left to suggest for these answers")

    title = whispers.FORMS[w["category"]][0]
    if w["category"] == "payroll":
        intro = (f"Based on your {answers['event'].lower()} of {fmt_eur(ctx['amount_cents'], whole=True)}, "
                 f"{cust['first_name']}, here's what we'd do. Tap to select.")
    else:
        intro = f"Your personal options, {cust['first_name']}:"
    created = now()
    quote = {"id": uuid.uuid4().hex, "whisper_id": whisper_id, "category": w["category"], "title": title,
             "intro": intro, "answers": answers, **priced,
             "created_at": iso(created), "expires_at": iso(created + timedelta(minutes=products.QUOTE_TTL_MINUTES))}
    conn.execute(
        "INSERT INTO quotes (id, whisper_id, customer_id, body, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (quote["id"], whisper_id, w["customer_id"], json.dumps(quote, ensure_ascii=False),
         quote["created_at"], quote["expires_at"]))
    conn.execute("UPDATE whispers SET status = 'quoted' WHERE id = ?", (whisper_id,))
    log_event(conn, "quote_created", w["customer_id"], quote_id=quote["id"], category=w["category"])
    return quote


def accept_quote(conn, quote_id: str, item_ids: List[str]) -> dict:
    q = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if not q:
        raise NotFound("Unknown quote")
    if q["accepted_at"]:
        raise Conflict("This quote was already accepted")
    if now() > datetime.fromisoformat(q["expires_at"]):
        raise Gone("This quote has expired. Please ask for a new one")
    w = whisper_row(conn, q["whisper_id"])
    if w["status"] == "accepted":
        raise Conflict("This suggestion was already accepted")
    body = json.loads(q["body"])
    by_id = {i["id"]: i for i in body["items"]}
    if len(set(item_ids)) != len(item_ids):
        raise Invalid("Each option can only be chosen once")
    unknown = [i for i in item_ids if i not in by_id]
    if unknown:
        raise Invalid(f"Not part of this quote: {', '.join(unknown)}")
    if body["selection"] == "single" and len(item_ids) != 1:
        raise Invalid("Choose exactly one plan")

    chosen = [by_id[i] for i in item_ids]
    total = sum(i["charge_now_cents"] for i in chosen)
    cust = customer_row(conn, q["customer_id"])
    if total > cust["balance_cents"]:
        raise Conflict(f"Insufficient funds: {fmt_eur(total)} needed, balance {fmt_eur(cust['balance_cents'])}")

    # Re-check against the current state: another quote may have been accepted in the meantime.
    owned = set(owned_products(conn, cust["id"]))
    for item in chosen:
        if item["product_type"] in products.SINGLE_POLICY_PRODUCTS and item["product_type"] in owned:
            raise Conflict(f"You already have {item['name']}. Please ask for a new quote")
        if (item["product_type"] == "pension"
                and cust["pension_ytd_cents"] + item["charge_now_cents"] > products.PENSION_SAVINGS_CAP_CENTS):
            raise Conflict("This deposit would go over this year's pension savings amount. Please ask for a new quote")

    ts = iso(now())
    new_tx = []
    for item in chosen:
        if item["product_type"] == "pension":
            cp, msg, cat = "KBC Pension Savings", f"Deposit {now().year}", "Pension savings"
        elif item["product_type"] == "savings":
            cp, msg, cat = "KBC Savings Account", "Emergency buffer", "Savings"
        else:
            cp, cat = f"KBC Insurance · {item['name']}", "Insurance"
            msg = "First monthly premium" if item["price_unit"] == "/ month" else "Premium"
        cur = conn.execute(
            "INSERT INTO transactions (customer_id, booked_at, direction, counterparty, amount_cents, message,"
            " channel, category, icon) VALUES (?, ?, 'out', ?, ?, ?, 'transfer', ?, ?)",
            (cust["id"], ts, cp, -item["charge_now_cents"], msg, cat, PRODUCT_ICONS.get(item["product_type"], "🛡️")))
        new_tx.append(cur.lastrowid)
        conn.execute(
            "INSERT INTO contracts (customer_id, quote_id, item_id, product_type, kind, name, price_cents,"
            " price_unit, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cust["id"], quote_id, item["id"], item["product_type"], item["kind"], item["name"],
             item["price_cents"], item["price_unit"], ts))
        if item["product_type"] == "pension":
            conn.execute("UPDATE customers SET pension_ytd_cents = pension_ytd_cents + ? WHERE id = ?",
                         (item["charge_now_cents"], cust["id"]))
    conn.execute("UPDATE customers SET balance_cents = balance_cents - ? WHERE id = ?", (total, cust["id"]))
    conn.execute("UPDATE quotes SET accepted_at = ? WHERE id = ?", (ts, quote_id))
    conn.execute("UPDATE whispers SET status = 'accepted' WHERE id = ?", (w["id"],))
    log_event(conn, "quote_accepted", cust["id"], quote_id=quote_id, items=item_ids, total_cents=total)

    balance = conn.execute("SELECT balance_cents FROM customers WHERE id = ?", (cust["id"],)).fetchone()[0]
    rows = conn.execute(f"SELECT * FROM transactions WHERE id IN ({','.join('?' * len(new_tx))}) ORDER BY id",
                        new_tx).fetchall()
    return {"quote_id": quote_id, "title": body["title"],
            "accepted": [{"id": i["id"], "name": i["name"], "price_cents": i["price_cents"],
                          "price_unit": i["price_unit"], "charged_cents": i["charge_now_cents"]} for i in chosen],
            "total_charged_cents": total, "balance_cents": balance,
            "transactions": [tx_view(r) for r in rows]}


def events_view(conn, customer_id: Optional[str], limit: int) -> List[dict]:
    if customer_id:
        rows = conn.execute("SELECT * FROM events WHERE customer_id = ? ORDER BY id DESC LIMIT ?",
                            (customer_id, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "at": r["at"], "customer_id": r["customer_id"], "type": r["type"],
             "detail": json.loads(r["detail"])} for r in rows]
