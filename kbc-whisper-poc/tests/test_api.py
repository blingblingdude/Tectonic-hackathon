"""End-to-end API tests. Run with:  python -m pytest -q"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import catalog
from app.detection import classify, usual_salary
from app.main import app
from app.money import fmt_eur


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WHISPER_DB", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def scenario(sid):
    return catalog.SCENARIOS_BY_ID[sid]


def post(client, sid, **override):
    s = scenario(sid)
    body = {**s["transaction"], **override}
    return client.post(f"/api/customers/{s['customer_id']}/transactions", json=body)


def connect(client, sid, on=True):
    s = scenario(sid)
    r = client.put(f"/api/customers/{s['customer_id']}/consents/{s['partner_id']}", json={"connected": on})
    assert r.status_code == 200 and r.json()["connected"] is on


def default_answers(whisper):
    return {q["id"]: (q["prefill"]["value"] if q["prefill"] else q["options"][0])
            for q in whisper["form"]["questions"]}


# --------------------------------------------------------------------------
def test_health_and_reference_data(client):
    assert client.get("/api/health").json()["status"] == "ok"
    data = client.get("/api/scenarios").json()
    assert {s["id"] for s in data["scenarios"]} == {"flight", "car", "house", "payroll"}
    assert len(client.get("/api/partners").json()) == 4
    assert client.get("/").status_code == 200


def test_unknown_things_are_404(client):
    assert client.get("/api/customers/nobody").status_code == 404
    assert client.put("/api/customers/lotte/consents/nope", json={"connected": True}).status_code == 404
    assert client.post("/api/whispers/nope/quotes", json={"answers": {}}).status_code == 404
    assert client.post("/api/quotes/nope/accept", json={"item_ids": ["x"]}).status_code == 404


def test_input_validation(client):
    bad = [
        {"direction": "sideways", "counterparty": "X", "amount_cents": 100},
        {"direction": "out", "counterparty": "X", "amount_cents": 0},
        {"direction": "out", "counterparty": "X", "amount_cents": 100, "mcc": "45A1"},
        {"direction": "out", "counterparty": "X", "amount_cents": 100, "surprise": 1},
    ]
    for body in bad:
        assert client.post("/api/customers/lotte/transactions", json=body).status_code == 422


@pytest.mark.parametrize("sid", ["flight", "car", "house", "payroll"])
@pytest.mark.parametrize("connected", [False, True])
def test_full_flow_every_scenario(client, sid, connected):
    s = scenario(sid)
    cid = s["customer_id"]
    if connected:
        connect(client, sid)
    start = client.get(f"/api/customers/{cid}").json()["balance_cents"]

    r = post(client, sid)
    assert r.status_code == 201
    body = r.json()
    a = body["analysis"]
    signed = s["transaction"]["amount_cents"] * (1 if s["transaction"]["direction"] == "in" else -1)
    assert body["balance_cents"] == start + signed
    assert a["data_source"] == ("partner" if connected else "recognition")
    stages = [x["stage"] for x in a["signals"]]
    assert stages[0] == "received" and stages[-1] == "decision"
    assert ("enriched" in stages) is connected
    w = a["whisper"]
    assert w and a["decision"]["shown"]

    kinds = {q["prefill"]["kind"] for q in w["form"]["questions"] if q["prefill"]}
    assert ("partner" in kinds) is connected

    q = client.post(f"/api/whispers/{w['id']}/quotes", json={"answers": default_answers(w)})
    assert q.status_code == 201
    quote = q.json()
    ids = [i["id"] for i in quote["items"] if i["recommended"]]
    ids = ids[:1] if quote["selection"] == "single" else ids
    total = sum(i["charge_now_cents"] for i in quote["items"] if i["id"] in ids)

    acc = client.post(f"/api/quotes/{quote['id']}/accept", json={"item_ids": ids})
    assert acc.status_code == 201
    assert acc.json()["total_charged_cents"] == total
    assert acc.json()["balance_cents"] == start + signed - total
    after = client.get(f"/api/customers/{cid}").json()
    assert after["balance_cents"] == start + signed - total
    assert sum(t["amount_cents"] for t in after["transactions"][: len(ids) + 1]) == signed - total

    # Idempotency: the same quote cannot be accepted twice.
    assert client.post(f"/api/quotes/{quote['id']}/accept", json={"item_ids": ids}).status_code == 409


def test_recognition_needs_no_partner(client):
    """Any airline is recognised from the merchant code alone."""
    r = client.post("/api/customers/lotte/transactions", json={
        "direction": "out", "counterparty": "EASYJET AIRLINE CO", "amount_cents": 21999,
        "mcc": "4511", "message": "", "channel": "card"}).json()
    assert r["analysis"]["category"] == "flight"
    assert r["analysis"]["partner_id"] is None
    assert r["analysis"]["whisper"]["form"]["questions"][0]["prefill"] is None  # no Ryanair "Europe" guess


def test_everyday_payment_gets_no_whisper(client):
    r = client.post("/api/customers/lotte/transactions", json={
        "direction": "out", "counterparty": "Colruyt", "amount_cents": 4210, "channel": "card"}).json()
    assert r["analysis"]["whisper"] is None and r["analysis"]["decision"]["code"] == "not_relevant"


def test_regular_salary_gets_no_whisper(client):
    r = post(client, "payroll", amount_cents=312000, message="LOON OKTOBER 2026").json()
    assert r["analysis"]["whisper"] is None


def test_quote_validation(client):
    w = post(client, "flight").json()["analysis"]["whisper"]
    url = f"/api/whispers/{w['id']}/quotes"
    assert client.post(url, json={"answers": {"dest": "Europe"}}).status_code == 422          # missing
    assert client.post(url, json={"answers": {**default_answers(w), "dest": "Mars"}}).status_code == 422
    assert client.post(url, json={"answers": {**default_answers(w), "x": "y"}}).status_code == 422
    quote = client.post(url, json={"answers": default_answers(w)}).json()
    accept = f"/api/quotes/{quote['id']}/accept"
    assert client.post(accept, json={"item_ids": ["trip_basic", "trip_plus"]}).status_code == 422  # single choice
    assert client.post(accept, json={"item_ids": ["free_money"]}).status_code == 422
    assert client.post(accept, json={"item_ids": []}).status_code == 422


def test_prices_are_server_side_and_exact(client):
    connect(client, "flight")
    w = post(client, "flight").json()["analysis"]["whisper"]
    answers = {**default_answers(w), "freq": "Just this once"}
    items = {i["id"]: i for i in client.post(f"/api/whispers/{w['id']}/quotes", json={"answers": answers}).json()["items"]}
    # Up to a week, Europe, student: 7.90 × 0.70 × 0.85 = 4.7005 → € 4,70
    assert items["trip_basic"]["price_cents"] == 470
    # (7.90 + 184.20 × 0.06) × 0.70 × 0.85 = 11.2764 → € 11,28
    assert items["trip_plus"]["price_cents"] == 1128
    assert items["trip_plus"]["recommended"]


def test_yearly_travel_cover_suppresses_next_flight(client):
    w = post(client, "flight").json()["analysis"]["whisper"]
    answers = {**default_answers(w), "freq": "2–3 times"}
    quote = client.post(f"/api/whispers/{w['id']}/quotes", json={"answers": answers}).json()
    assert client.post(f"/api/quotes/{quote['id']}/accept", json={"item_ids": ["travel_year"]}).status_code == 201
    again = post(client, "flight").json()["analysis"]
    assert again["whisper"] is None and again["decision"]["code"] == "covered"


def test_mute_and_unmute(client):
    w = post(client, "flight").json()["analysis"]["whisper"]
    assert client.post(f"/api/whispers/{w['id']}/feedback", json={"action": "muted"}).json()["muted"]
    assert client.get("/api/customers/lotte/mutes").json()[0]["category"] == "flight"
    muted = post(client, "flight").json()["analysis"]
    assert muted["whisper"] is None and muted["decision"]["code"] == "muted"
    # A muted whisper cannot be quoted any more.
    assert client.post(f"/api/whispers/{w['id']}/quotes", json={"answers": default_answers(w)}).status_code == 409
    client.delete("/api/customers/lotte/mutes/flight")
    assert post(client, "flight").json()["analysis"]["whisper"] is not None


def test_insufficient_funds(client):
    assert post(client, "car").status_code == 201
    declined = post(client, "car")               # a second car does not fit the balance
    assert declined.status_code == 409 and "insufficient funds" in declined.json()["detail"]
    balance = client.get("/api/customers/pieter").json()["balance_cents"]
    assert balance == 2431020 - 1845000           # nothing was booked for the declined payment


def test_payroll_bonus_pension_and_follow_up(client):
    connect(client, "payroll")
    a = post(client, "payroll").json()["analysis"]
    classified = next(s for s in a["signals"] if s["stage"] == "classified")["detail"]
    assert "56% above the usual salary of € 3.120,00" in classified
    w = a["whisper"]
    q = client.post(f"/api/whispers/{w['id']}/quotes", json={"answers": default_answers(w)}).json()
    items = {i["id"]: i for i in q["items"]}
    assert items["pension"]["price_cents"] == 105000
    assert "€ 315,00 back" in items["pension"]["features"][0]
    assert "bike" in items                        # e-bike lease from the SD Worx payslip
    client.post(f"/api/quotes/{q['id']}/accept", json={"item_ids": ["pension", "bike"]})
    assert client.get("/api/customers/elise").json()["pension_ytd_cents"] == 105000

    # Next payout: KBC now knows pension savings are maxed, so no pension suggestion.
    w2 = post(client, "payroll").json()["analysis"]["whisper"]
    pens = next(q for q in w2["form"]["questions"] if q["id"] == "pens")
    assert pens["prefill"] == {"value": "Already maxed", "label": "KBC data", "kind": "kbc"}
    q2 = client.post(f"/api/whispers/{w2['id']}/quotes", json={"answers": default_answers(w2)}).json()
    assert "pension" not in {i["id"] for i in q2["items"]}


def test_reset_restores_seed(client):
    post(client, "flight")
    client.put("/api/customers/lotte/consents/ryanair", json={"connected": True})
    client.post("/api/demo/reset")
    lotte = client.get("/api/customers/lotte").json()
    assert lotte["balance_cents"] == 124055
    assert not any(c["connected"] for c in client.get("/api/customers/lotte/consents").json())


def test_events_are_logged(client):
    post(client, "flight")
    types = [e["type"] for e in client.get("/api/events?customer_id=lotte").json()]
    assert "transaction_posted" in types and "whisper_shown" in types


# --------------------------------------------------------------------------
# Pure unit tests
# --------------------------------------------------------------------------
def test_money_format():
    assert fmt_eur(18420) == "€ 184,20"
    assert fmt_eur(1845000) == "€ 18.450,00"
    assert fmt_eur(-5) == "− € 0,05"
    assert fmt_eur(31550, whole=True) == "€ 316"


def test_usual_salary_ignores_outliers():
    assert usual_salary([312000, 312000, 486000, 312000]) == 312000


def test_rental_keyword_and_address():
    cls, _ = classify({"direction": "out", "counterparty": "J. PEETERS", "amount_cents": 150000,
                       "message": "Huurwaarborg Veldstraat 7b", "mcc": None})
    assert cls.category == "rental" and cls.facts["address"] == "Veldstraat 7b"


def test_small_airline_charge_is_ignored():
    cls, why = classify({"direction": "out", "counterparty": "RYANAIR DAC", "amount_cents": 2500,
                         "message": "", "mcc": "4511"})
    assert cls is None and "seat or bag" in why


# --------------------------------------------------------------------------
# Regression tests from the code review
# --------------------------------------------------------------------------
def test_small_bonus_is_not_called_above_usual(client):
    a = post(client, "payroll", amount_cents=50000, message="BONUS").json()["analysis"]
    classified = next(s for s in a["signals"] if s["stage"] == "classified")["detail"]
    assert "above" not in classified and "-" not in classified.split("·")[1]
    assert a["whisper"]["title"] == "A bonus has landed 🎉"


def test_curly_apostrophe_year_end_bonus():
    cls, _ = classify({"direction": "in", "counterparty": "SD WORX NV", "amount_cents": 400000,
                       "message": "Prime de fin d’année 2026", "mcc": None}, [312000])
    assert cls.facts["payout_kind"] == "year_end_bonus"


def test_repeated_bonuses_do_not_move_the_usual_salary(client):
    for _ in range(5):
        a = post(client, "payroll").json()["analysis"]
    classified = next(s for s in a["signals"] if s["stage"] == "classified")["detail"]
    assert "56% above the usual salary of € 3.120,00" in classified


def test_owned_cover_is_not_offered_again(client):
    connect(client, "payroll")
    w = post(client, "payroll").json()["analysis"]["whisper"]
    answers = {**default_answers(w), "dep": "My partner"}
    q = client.post(f"/api/whispers/{w['id']}/quotes", json={"answers": answers}).json()
    assert client.post(f"/api/quotes/{q['id']}/accept", json={"item_ids": ["bike", "income"]}).status_code == 201
    w2 = post(client, "payroll").json()["analysis"]["whisper"]
    assert "bike cover" not in w2["body"] and "income protection" not in w2["body"]
    q2 = client.post(f"/api/whispers/{w2['id']}/quotes", json={"answers": answers}).json()
    assert not {"bike", "income"} & {i["id"] for i in q2["items"]}


def test_pension_cap_rechecked_on_accept(client):
    quotes = []
    for _ in range(2):
        w = post(client, "payroll").json()["analysis"]["whisper"]
        quotes.append(client.post(f"/api/whispers/{w['id']}/quotes", json={"answers": default_answers(w)}).json())
    assert client.post(f"/api/quotes/{quotes[0]['id']}/accept", json={"item_ids": ["pension"]}).status_code == 201
    second = client.post(f"/api/quotes/{quotes[1]['id']}/accept", json={"item_ids": ["pension"]})
    assert second.status_code == 409
    assert client.get("/api/customers/elise").json()["pension_ytd_cents"] == 105000


def test_partner_records_must_match_the_transaction(client):
    connect(client, "car")
    a = post(client, "car", amount_cents=650000, message="Invoice order 7777").json()["analysis"]
    assert a["data_source"] == "recognition"
    enriched = next(s for s in a["signals"] if s["stage"] == "enriched")
    assert enriched["status"] == "skipped"
    connect(client, "payroll")
    assert post(client, "payroll", amount_cents=500000).json()["analysis"]["data_source"] == "recognition"
