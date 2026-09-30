"""HTTP layer: routes, error mapping, static frontend.

Run with `python run.py` and open http://localhost:8000 (frontend) or
http://localhost:8000/docs (interactive API documentation).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import catalog, db, service
from .models import AcceptIn, ConsentIn, FeedbackIn, QuoteIn, TransactionIn

VERSION = "1.0.0"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="KBC Whisper · proof-of-concept API",
    version=VERSION,
    description=(
        "Backend for the Whisper concept: the bank recognises a substantial purchase or payout from the raw "
        "transaction, optionally enriches it with data from a connected partner app (only with consent), "
        "and suggests fitting insurance or financial actions. All amounts are integer euro cents. "
        "Prices are illustrative."
    ),
    lifespan=lifespan,
)
# The frontend is served from this same server; CORS is only open so the HTML
# file also works when opened straight from disk during a demo.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(service.DomainError)
async def domain_error(_: Request, exc: service.DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------
# Demo + reference data
# --------------------------------------------------------------------------
@app.get("/api/health", tags=["demo"], summary="Is the backend up?")
def health() -> dict:
    with db.read() as conn:
        customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    return {"status": "ok", "version": VERSION, "database": db.db_path().name, "customers": customers,
            "time": db.iso(db.now())}


@app.get("/api/scenarios", tags=["demo"], summary="Demo scenarios and the raw transaction each one posts")
def scenarios() -> dict:
    return {"groups": catalog.GROUPS, "scenarios": catalog.SCENARIOS}


@app.get("/api/partners", tags=["demo"], summary="Partner apps a customer can connect")
def partner_catalog() -> list:
    return catalog.PARTNERS


@app.post("/api/demo/reset", tags=["demo"], summary="Wipe and re-seed all demo data")
def reset() -> dict:
    db.init_db(reset=True)
    return {"status": "reset"}


@app.get("/api/events", tags=["demo"], summary="Audit log of what the backend decided")
def events(customer_id: Optional[str] = None, limit: int = Query(30, ge=1, le=200)) -> list:
    with db.read() as conn:
        return service.events_view(conn, customer_id, limit)


# --------------------------------------------------------------------------
# Customers, consents, mutes
# --------------------------------------------------------------------------
@app.get("/api/customers", tags=["customers"], summary="Demo customers")
def customers() -> list:
    with db.read() as conn:
        rows = conn.execute("SELECT id, first_name, last_name, age, segment FROM customers ORDER BY rowid")
        return [dict(r) for r in rows]


@app.get("/api/customers/{customer_id}", tags=["customers"], summary="Account, latest transactions, contracts")
def customer(customer_id: str) -> dict:
    with db.read() as conn:
        return service.customer_view(conn, customer_id)


@app.get("/api/customers/{customer_id}/consents", tags=["consents"], summary="Which partner apps share data")
def consents(customer_id: str) -> list:
    with db.read() as conn:
        return service.consents_view(conn, customer_id)


@app.put("/api/customers/{customer_id}/consents/{partner_id}", tags=["consents"],
         summary="Connect or disconnect a partner app")
def set_consent(customer_id: str, partner_id: str, body: ConsentIn) -> dict:
    with db.write() as conn:
        return service.set_consent(conn, customer_id, partner_id, body.connected)


@app.get("/api/customers/{customer_id}/mutes", tags=["whispers"], summary="Categories the customer turned off")
def mutes(customer_id: str) -> list:
    with db.read() as conn:
        return service.mutes_view(conn, customer_id)


@app.delete("/api/customers/{customer_id}/mutes/{category}", tags=["whispers"],
            summary="Turn suggestions for a category back on")
def unmute(customer_id: str, category: str) -> dict:
    with db.write() as conn:
        return service.unmute(conn, customer_id, category)


# --------------------------------------------------------------------------
# Transactions → analysis → whisper → quote → accept
# --------------------------------------------------------------------------
@app.post("/api/customers/{customer_id}/transactions", tags=["whispers"], status_code=201,
          summary="Post a transaction; the backend classifies it and decides on a whisper")
def post_transaction(customer_id: str, body: TransactionIn) -> dict:
    with db.write() as conn:
        return service.post_transaction(conn, customer_id, body.model_dump())


@app.post("/api/whispers/{whisper_id}/feedback", tags=["whispers"],
          summary="Record what the customer did with a whisper (opened, dismissed, muted)")
def feedback(whisper_id: str, body: FeedbackIn) -> dict:
    with db.write() as conn:
        return service.whisper_feedback(conn, whisper_id, body.action)


@app.post("/api/whispers/{whisper_id}/quotes", tags=["quotes"], status_code=201,
          summary="Price the options for the customer's answers (server-side)")
def quote(whisper_id: str, body: QuoteIn) -> dict:
    with db.write() as conn:
        return service.create_quote(conn, whisper_id, body.answers)


@app.post("/api/quotes/{quote_id}/accept", tags=["quotes"], status_code=201,
          summary="Accept one plan (or several actions): debits the account and creates contracts")
def accept(quote_id: str, body: AcceptIn) -> dict:
    with db.write() as conn:
        return service.accept_quote(conn, quote_id, body.item_ids)
