"""SQLite persistence: schema, connections, seeding and a small audit log.

Every write request runs inside one `BEGIN IMMEDIATE` transaction, so a
balance check and the debit that follows it can never interleave with another
request. Reads run in autocommit mode.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from . import catalog

DEFAULT_DB = Path(__file__).resolve().parent.parent / "whisper.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id                TEXT PRIMARY KEY,
    first_name        TEXT NOT NULL,
    last_name         TEXT NOT NULL,
    age               INTEGER NOT NULL,
    segment           TEXT NOT NULL,
    iban              TEXT NOT NULL,
    postcode          TEXT NOT NULL,
    balance_cents     INTEGER NOT NULL,
    pension_ytd_cents INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  TEXT NOT NULL REFERENCES customers(id),
    booked_at    TEXT NOT NULL,
    direction    TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    counterparty TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,            -- signed: negative = money out
    mcc          TEXT,
    message      TEXT NOT NULL DEFAULT '',
    channel      TEXT NOT NULL DEFAULT 'transfer',
    category     TEXT NOT NULL DEFAULT '',
    icon         TEXT NOT NULL DEFAULT '💳'
);
CREATE INDEX IF NOT EXISTS ix_tx_customer ON transactions(customer_id, booked_at);
CREATE TABLE IF NOT EXISTS consents (
    customer_id TEXT NOT NULL REFERENCES customers(id),
    partner_id  TEXT NOT NULL,
    connected   INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (customer_id, partner_id)
);
CREATE TABLE IF NOT EXISTS mutes (
    customer_id TEXT NOT NULL REFERENCES customers(id),
    category    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (customer_id, category)
);
CREATE TABLE IF NOT EXISTS whispers (
    id             TEXT PRIMARY KEY,
    customer_id    TEXT NOT NULL REFERENCES customers(id),
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    category       TEXT NOT NULL,
    status         TEXT NOT NULL,             -- shown | opened | dismissed | muted | quoted | accepted
    context        TEXT NOT NULL,             -- JSON: facts the quote engine needs
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    id          TEXT PRIMARY KEY,
    whisper_id  TEXT NOT NULL REFERENCES whispers(id),
    customer_id TEXT NOT NULL REFERENCES customers(id),
    body        TEXT NOT NULL,                -- JSON: the priced items, exactly as shown
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    accepted_at TEXT
);
CREATE TABLE IF NOT EXISTS contracts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  TEXT NOT NULL REFERENCES customers(id),
    quote_id     TEXT NOT NULL REFERENCES quotes(id),
    item_id      TEXT NOT NULL,
    product_type TEXT NOT NULL,
    kind         TEXT NOT NULL,               -- policy | transfer
    name         TEXT NOT NULL,
    price_cents  INTEGER NOT NULL,
    price_unit   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    customer_id TEXT,
    type        TEXT NOT NULL,
    detail      TEXT NOT NULL
);
"""

TABLES_IN_DROP_ORDER = ["events", "contracts", "quotes", "whispers", "mutes", "consents",
                        "transactions", "customers"]


def db_path() -> Path:
    return Path(os.environ.get("WHISPER_DB", str(DEFAULT_DB)))


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    conn = _open()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """One atomic, serialised write transaction."""
    conn = _open()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def log_event(conn: sqlite3.Connection, type_: str, customer_id: Optional[str], **detail) -> None:
    conn.execute(
        "INSERT INTO events (at, customer_id, type, detail) VALUES (?, ?, ?, ?)",
        (iso(now()), customer_id, type_, json.dumps(detail, ensure_ascii=False)),
    )


def _seed(conn: sqlite3.Connection) -> None:
    ts = now()
    for c in catalog.CUSTOMERS:
        conn.execute(
            "INSERT INTO customers (id, first_name, last_name, age, segment, iban, postcode, balance_cents,"
            " pension_ytd_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (c["id"], c["first_name"], c["last_name"], c["age"], c["segment"], c["iban"], c["postcode"],
             c["balance_cents"], c["pension_ytd_cents"]),
        )
        # Oldest first so ids increase with time.
        for days_ago, direction, cp, amount, category, icon, message in sorted(c["history"], key=lambda h: -h[0]):
            signed = amount if direction == "in" else -amount
            conn.execute(
                "INSERT INTO transactions (customer_id, booked_at, direction, counterparty, amount_cents,"
                " message, category, icon) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (c["id"], iso(ts - timedelta(days=days_ago)), direction, cp, signed, message, category, icon),
            )
        for p in catalog.PARTNERS:
            conn.execute(
                "INSERT INTO consents (customer_id, partner_id, connected, updated_at) VALUES (?, ?, 0, ?)",
                (c["id"], p["id"], iso(ts)),
            )
    log_event(conn, "demo_seeded", None, customers=len(catalog.CUSTOMERS))


def init_db(reset: bool = False) -> None:
    """Create the schema and seed demo data (again, if `reset`)."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if reset:
            for table in TABLES_IN_DROP_ORDER:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        # Run statement by statement (executescript would commit our transaction).
        for statement in SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        empty = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0
        if empty:
            _seed(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
