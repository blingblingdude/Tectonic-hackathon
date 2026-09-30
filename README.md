# KBC Whisper: proof of concept

A clickable demo of **Whisper**: right after a customer makes a substantial purchase (a flight, a car, a new rental) or receives a bigger payout (a bonus via SD Worx), the bank recognises what happened and suggests fitting insurance or a smart financial move.

The frontend is the phone demo. The Python backend makes every decision: it classifies the transaction, checks consent, enriches with partner data, decides whether to show a whisper, prices the options and books the payment.

> Concept prototype, not an official KBC product. Auto Verhaegen and HuurHub are fictional. Prices, tax figures and partner data are illustrative.

## Run it

You need Python 3.9 or newer.

```bash
cd kbc-whisper-poc
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

The demo opens at **http://localhost:8000**. The interactive API docs are at **http://localhost:8000/docs**.

- `python run.py --port 8080` runs it on another port.
- `python run.py --no-browser` stops it from opening a browser tab.
- To start over, click **Reset demo data** in the page, or delete `whisper.db`.

## What happens when you click "Simulate the payment"

```
 Phone UI ──POST /api/customers/{id}/transactions──▶ FastAPI
                                                      │
          1. book the transaction (balance check, integer cents, one atomic DB transaction)
          2. classify it from raw bank data only     → detection.py
               • merchant category 4511 / 3000–3299  → flight
               • counterparty registered NACE 45.11  → car purchase
               • message "huurwaarborg" + address    → new rental
               • payroll provider, >25% above usual  → bonus / bigger payout
                 salary (median of earlier payslips) or "EINDEJAARSPREMIE"
          3. if the customer connected that partner   → partners.py (mock APIs)
               enrich: booking dates, dealer order, lease, payslip
          4. relevance: muted?  already covered?      → service.py
          5. build the whisper + pre-filled questions → whispers.py
                                                      │
 Phone UI ◀────── signals + whisper (or reason for staying silent)
 Phone UI ──POST /api/whispers/{id}/quotes {answers}──▶ server-side pricing (products.py)
 Phone UI ──POST /api/quotes/{id}/accept {item_ids}───▶ debit account, create contracts
```

The **Behind the scenes** panel shows the signals exactly as the backend returned them. **Live API calls** lists every request and response; click a row to see the JSON.

## Things to try in the demo

| Try this | What the backend does |
|---|---|
| Run a scenario with **Automatic bank recognition** | Classifies from bank data alone and asks for what it can't know |
| Switch to **Connected app** and run again | Adds a partner-enrichment step; the questions come pre-filled (green badges) |
| Buy **Student Travel Year**, then book another flight | No whisper, because the customer is already covered |
| Tap **Don't send me suggestions like this again**, run again | Suppressed as muted. The **Turn … back on** link unmutes |
| Buy the car twice | The second payment is declined for insufficient funds (409) |
| Accept pension savings for Elise, run the payout again | Pension is pre-filled as "Already maxed" and no longer suggested |
| In `/docs`, POST a payment to `EASYJET AIRLINE CO` with MCC `4511` | Recognised as a flight without any partner at all |

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status |
| GET | `/api/scenarios` | Demo scenarios and the raw transaction each one posts |
| GET | `/api/partners` | Partner apps a customer can connect |
| GET | `/api/customers/{id}` | Balance, latest transactions, contracts |
| GET / PUT | `/api/customers/{id}/consents[/{partner}]` | Data-sharing consents |
| GET / DELETE | `/api/customers/{id}/mutes[/{category}]` | Muted whisper categories |
| POST | `/api/customers/{id}/transactions` | Book a transaction → analysis + whisper |
| POST | `/api/whispers/{id}/feedback` | `opened`, `dismissed` or `muted` |
| POST | `/api/whispers/{id}/quotes` | Price the options for the given answers |
| POST | `/api/quotes/{id}/accept` | Accept a plan or actions: debit and create contracts |
| GET | `/api/events` | Audit log of backend decisions |
| POST | `/api/demo/reset` | Re-seed all demo data |

Errors return `{"detail": "..."}` with these codes: 404 (unknown), 409 (insufficient funds, already accepted, muted), 410 (quote expired after 15 minutes) and 422 (invalid input or answers).

## Design choices worth mentioning

- **Money is stored as integer cents.** Premiums are calculated with `Decimal` and rounded half-up exactly once, so there is no floating-point drift.
- **Prices never come from the browser.** The client sends answers, gets a priced quote with an id, and accepts by id. A quote can only be accepted once.
- **Writes are atomic and serialised.** Each write runs in one SQLite `BEGIN IMMEDIATE` transaction, so concurrent payments can't overdraw an account. This was tested with 20 parallel payments.
- **Consent-first.** Partner adapters are only called when the customer connected that partner, and every whisper states which data it used.
- **Input is validated** with pydantic (unknown fields are rejected), and the frontend HTML-escapes everything it renders.

## Project layout

```
run.py              start the server (and open the browser)
app/main.py         HTTP routes, error mapping, serves static/index.html
app/service.py      business operations: post transaction, analyse, quote, accept
app/detection.py    rules engine: what is this transaction?
app/partners.py     mock partner APIs (Ryanair, SD Worx, dealer, HuurHub)
app/whispers.py     whisper copy, pre-filled questions, teaser price
app/products.py     questions per category + illustrative pricing
app/catalog.py      demo personas, scenarios, partner records, company registry
app/db.py           SQLite schema, seeding, audit log
app/money.py        cents and euro formatting helpers
static/index.html   the phone demo (plain HTML/CSS/JS, no build step)
tests/test_api.py   32 API and unit tests
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## From proof of concept to production

This is where a real build would go next: real transaction feeds from core banking, the KBO/BCE company registry and real partner APIs (OAuth consent, a PSD2-style consent register), authentication, a proper database, an ML classifier alongside the rules, frequency capping and A/B testing of whispers, and compliance review of the advice texts (insurance distribution and tax wording).
