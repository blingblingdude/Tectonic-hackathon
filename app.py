from flask import Flask, render_template, request, jsonify
from datetime import datetime

app = Flask(__name__)

# --- IN-MEMORY DATABASE (PoC State) ---
STATE = {
    "partners": {
        "ryanair": False,
        "sdworx": False,
        "dealer": False,
        "immo": False
    },
    "policies_purchased": []
}

def fmt_eur(n, decimals=2):
    """Formats a number as Belgian Euro string: € 1.050 or € 18,40"""
    if decimals == 0:
        s = f"{n:,.0f}".replace(",", ".")
    else:
        s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {s}"

def log_event(tag, message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{tag}] {message}")


# --- ROUTES ---

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/partners", methods=["GET", "POST"])
def handle_partners():
    if request.method == "POST":
        data = request.json
        partner_id = data.get("id")
        if partner_id in STATE["partners"]:
            STATE["partners"][partner_id] = bool(data.get("connected"))
            status = "CONNECTED" if STATE["partners"][partner_id] else "DISCONNECTED"
            log_event("CONSENT-API", f"Partner '{partner_id}' is now {status}")
    return jsonify(STATE["partners"])


@app.route("/api/analyze-transaction", methods=["POST"])
def analyze_transaction():
    """Simulates real-time transaction detection and partner enrichment."""
    data = request.json
    scenario = data.get("scenario")
    merchant = data.get("merchant")
    amount = data.get("amount")
    partner = data.get("partner")

    is_connected = STATE["partners"].get(partner, False)
    mode = "connected" if is_connected else "recognised"

    log_event("TX-STREAM", f"Detected € {amount} with '{merchant}' (Scenario: {scenario})")
    if is_connected:
        log_event("PARTNER-API", f"Enriching transaction via '{partner}' partner link (Consent: ACTIVE)")
    else:
        log_event("CLASSIFIER", f"No partner link for '{partner}'. Falling back to bank statement heuristics.")

    return jsonify({
        "status": "whisper_triggered",
        "scenario": scenario,
        "mode": mode,
        "partner_connected": is_connected,
        "timestamp": datetime.now().isoformat()
    })


@app.route("/api/calculate-quote", methods=["POST"])
def calculate_quote():
    """Backend pricing & recommendation engine for all 4 scenarios."""
    data = request.json
    sc = data.get("scenario")
    a = data.get("answers", {})

    log_event("PRICING-ENGINE", f"Calculating options for scenario='{sc}' with answers={a}")

    # 1. Flight Insurance Pricing
    if sc == "flight":
        len_map = {"Weekend (≤ 3 days)": 4.9, "Up to a week": 7.9, "Up to a month": 17.9}
        len_val = len_map.get(a.get("len"), 7.9)
        dest = 2.1 if a.get("dest") == "Outside Europe" else 1.0
        stud = 0.85

        basic = round(len_val * dest * stud * 0.7, 2)
        plus = round((len_val * dest + 184.2 * 0.06) * stud * 0.7 + 0.1, 2)
        annual = round((99 if a.get("dest") == "Outside Europe" else 69) * stud, 2)
        rec_annual = a.get("freq") != "Just this once"

        return jsonify({
            "type": "quote",
            "plans": [
                {"n": "Trip Basic", "p": basic, "u": "this trip", "f": ["Medical costs abroad", "Repatriation", "Lost or delayed luggage"], "rec": False},
                {"n": "Trip Plus", "p": plus, "u": "this trip", "f": ["Everything in Basic", "Cancellation up to € 185 (ticket value)", "24/7 assistance"], "rec": not rec_annual},
                {"n": "Student Travel Year", "p": annual, "u": "per year", "f": ["Every trip for 12 months", "Cancellation included", "Great if you fly 2+ times a year"], "rec": rec_annual}
            ],
            "factors": [
                "Student rate −15%",
                f"Destination: {a.get('dest')}",
                f"Trip length: {a.get('len')}",
                "Ticket value € 184,20 sets the cancellation limit"
            ]
        })

    # 2. Car Insurance Pricing
    elif sc == "car":
        reg_map = {"1000 Brussels": 1.3, "2000 Antwerp": 1.15, "3000 Leuven": 1.0, "9000 Ghent": 1.05, "8400 Ostend": 0.93}
        fuel_map = {"Petrol": 1.0, "Diesel": 1.05, "Hybrid": 0.95, "Electric": 0.9}
        reg = reg_map.get(a.get("zip"), 1.0)
        fuel = fuel_map.get(a.get("fuel"), 1.0)
        v = 18450
        liab = 34 * reg * fuel
        new_car = a.get("cond") == "New"

        return jsonify({
            "type": "quote",
            "plans": [
                {"n": "Liability only", "p": round(liab, 2), "u": "/ month", "f": ["Legally required cover", "Damage you cause to others"], "rec": False},
                {"n": "Mini-omnium", "p": round(liab + v * 0.017 / 12, 2), "u": "/ month", "f": ["Liability", "Theft, fire, glass, storm", "Collision with animals"], "rec": not new_car},
                {"n": "Full omnium", "p": round(liab + v * 0.033 / 12, 2), "u": "/ month", "f": ["Everything in Mini-omnium", "Own damage, even if it's your fault", "Replacement vehicle"], "rec": new_car}
            ],
            "factors": [
                f"Parked in {a.get('zip')} (regional factor ×{reg:.2f})",
                f"{a.get('fuel')} engine",
                f"{a.get('cond')} car · value € 18.450",
                "Full omnium recommended for new cars in the first 3 years"
            ]
        })

    # 3. Home & Fire Insurance Pricing
    elif sc == "house":
        risk_map = {"3000 Leuven": 1.0, "9000 Ghent": 1.08, "2000 Antwerp": 1.1, "8620 Nieuwpoort": 1.35, "4000 Liège": 1.28}
        size_map = {"1": 0.85, "2": 1.0, "3": 1.2, "4+": 1.45}
        risk = risk_map.get(a.get("zip"), 1.0)
        size = size_map.get(a.get("size"), 1.0)
        type_mult = 1.4 if a.get("type") == "House" else 1.0
        own_mult = 2.3 if a.get("sit") == "Buying" else 1.0
        base = 6.5 * risk * size * type_mult * own_mult
        high = risk >= 1.08
        is_buying = a.get("sit") == "Buying"

        return jsonify({
            "type": "quote",
            "plans": [
                {"n": "Fire (building)" if is_buying else "Tenant fire liability", "p": round(base, 2), "u": "/ month", "f": ["Fire, explosion, smoke", "Rebuild value of the home" if is_buying else "Damage to the landlord's property", "Usually required"], "rec": not high},
                {"n": "Comfort", "p": round(base * 1.5, 2), "u": "/ month", "f": ["Everything in the base cover", "Water damage & flooding", "Storm, hail and your contents"], "rec": high},
                {"n": "Complete", "p": round(base * 1.9, 2), "u": "/ month", "f": ["Everything in Comfort", "Theft & vandalism", "Legal assistance"], "rec": False}
            ],
            "factors": [
                f"Regional risk {a.get('zip')}: ×{risk:.2f}",
                f"{a.get('type')}, {a.get('size')} bedroom(s)",
                a.get("sit", "Renting"),
                "Comfort recommended because of local water risk" if high else "Base cover fits the low local risk"
            ]
        })

    # 4. Payroll / Bonus Smart Actions
    elif sc == "payroll":
        out = []
        if a.get("pens") != "Already maxed":
            amt = 1050 if a.get("pens") == "Not yet" else 550
            out.append({
                "id": "pens", "n": "KBC pension savings", "p": f"{fmt_eur(amt, 0)} once", "amt": amt,
                "tx": "To KBC pension savings", "ti": "🏦", "rec": True,
                "f": [
                    f"Your bonus is taxed at up to 50%. A deposit of {fmt_eur(amt, 0)} earns about {fmt_eur(amt * 0.3, 0)} back as a 30% tax reduction",
                    "Deposit now, benefit on next year's tax return",
                    "Choose a low- or medium-risk fund"
                ]
            })
        if a.get("cafe") == "E-bike lease":
            out.append({
                "id": "bike", "n": "E-bike theft & damage cover", "p": "€ 7,90 / month", "amt": 7.90,
                "tx": "KBC e-bike insurance · 1st month", "ti": "🚲", "rec": True,
                "f": ["Theft, vandalism and accident damage", "Pick-up assistance if you break down", "Check your lease: if theft isn't included, add it here"]
            })
        if a.get("cafe") == "Laptop / smartphone":
            out.append({
                "id": "dev", "n": "Device cover", "p": "€ 4,50 / month", "amt": 4.50,
                "tx": "KBC device cover · 1st month", "ti": "💻", "rec": True,
                "f": ["Theft and accidental damage", "Covers the device from your cafeteria plan"]
            })

        is_raise = a.get("event") in ["Salary increase", "Promotion"]
        just_me = a.get("dep") == "Just me"
        out.append({
            "id": "inc", "n": "Guaranteed income insurance",
            "p": "€ 16 / month" if just_me else "€ 22 / month",
            "amt": 16 if just_me else 22,
            "tx": "KBC income insurance · 1st month", "ti": "🛡️",
            "rec": is_raise or not just_me,
            "f": [
                "Your salary went up. Keep up to 80% of your new income if you can't work" if is_raise else "Keep up to 80% of your income if illness keeps you from working",
                "Tops up the legal sickness benefit" if just_me else "Protects the people who rely on you"
            ]
        })
        out.append({
            "id": "buf", "n": "Emergency buffer", "p": "€ 1.000 to savings", "amt": 1000,
            "tx": "To KBC savings account", "ti": "🐷", "rec": not is_raise,
            "f": ["Move part of the bonus to a KBC savings account", "Around three months of fixed costs is a healthy target"]
        })

        return jsonify({"type": "actions", "actions": out})

    return jsonify({"error": "Unknown scenario"}), 400


@app.route("/api/checkout", methods=["POST"])
def checkout():
    """Records selected policies or financial actions."""
    data = request.json
    scenario = data.get("scenario")
    persona = data.get("persona")
    items = data.get("items", [])
    total = sum(i.get("amt", 0) for i in items)

    record = {
        "timestamp": datetime.now().isoformat(),
        "persona": persona,
        "scenario": scenario,
        "items": [i.get("n") for i in items],
        "total_debited": round(total, 2)
    }
    STATE["policies_purchased"].append(record)
    log_event("CHECKOUT", f"{persona} activated {record['items']} (Total debited: € {total:.2f})")

    return jsonify({"status": "confirmed", "record": record})


if __name__ == "__main__":
    print("🚀 KBC Whisper PoC Backend running on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)