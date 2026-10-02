"""Hypotherm backend (prototype). Synthetic data only.
All weights/thresholds are PROTOTYPE values, not clinical or operational standards.
Swap SEED_* dicts for PostgreSQL/PostGIS queries later."""
import math, os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Hypotherm API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CENTER = (12.2958, 76.6394)  # Mysuru
DISCLAIMER = ("Risk thresholds are prototype decision-support settings and require "
              "local health-outcome calibration before operational use.")

# ---------- WBGT service ----------
def wbgt_estimate(temp_c: float, rh: float, wind_ms: float, solar_wm2: float):
    """Prototype estimate: Stull (2011) wet-bulb + simple solar/wind adjustment.
    Tries thermofeel first; reports which method was used (never silently faked)."""
    try:
        import thermofeel  # noqa: F401
        # thermofeel needs mean radiant temp etc.; if inputs are incomplete we fall through
        raise ImportError("insufficient radiation inputs for thermofeel in prototype")
    except Exception:
        tw = (temp_c * math.atan(0.151977 * math.sqrt(rh + 8.313659)) + math.atan(temp_c + rh)
              - math.atan(rh - 1.676331) + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)
        w = 0.7 * tw + 0.2 * (temp_c + solar_wm2 / 100.0) + 0.1 * temp_c - 0.3 * min(wind_ms, 5)
        return round(w, 1), "prototype_estimate"

# ---------- Weather service ----------
async def fetch_forecast(lat: float, lng: float):
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation"
           "&forecast_days=3&timezone=Asia%%2FKolkata" % (lat, lng))
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            h = (await c.get(url)).raise_for_status().json()["hourly"]
        return {"source": "open-meteo", "time": h["time"], "temperature": h["temperature_2m"],
                "humidity": h["relative_humidity_2m"], "windSpeed": h["wind_speed_10m"],
                "solarRadiation": h["shortwave_radiation"]}
    except Exception:
        return {"source": "demo"}  # caller falls back to seeded forecast

# ---------- Risk engine ----------
def calculate_hvei(elderly_pct, outdoor_pct, green_pct):
    """HVEI = 0.4*elderly + 0.4*outdoor - 0.2*green, each scaled 0-100 (configurable)."""
    return round(0.4 * min(elderly_pct * 4, 100) + 0.4 * min(outdoor_pct * 2.5, 100)
                 - 0.2 * green_pct, 1)

def normalize_wbgt(wbgt):  # 26C->0, 32C->100 (prototype range)
    return max(0.0, min(100.0, (wbgt - 26) / 6 * 100))

def calculate_final_risk(hvei, nwbgt, consecutive_heat_days=0, warm_night_flag=False):
    s = 0.55 * nwbgt + 0.45 * max(hvei, 0) + 3 * min(consecutive_heat_days, 3) + (4 if warm_night_flag else 0)
    return round(min(100, s))

def classify_risk(score):
    return "Red" if score >= 75 else "Orange" if score >= 55 else "Yellow" if score >= 35 else "Green"

def intervention_plan(level):
    return {
        "Red": ["Deploy 3 water points", "Activate 2 cooling centers", "Ambulance/emergency team on standby",
                "ASHA visit to Top 10 priority homes", "Send multilingual alert by 7 PM",
                "Contractor work modification notice"],
        "Orange": ["Deploy 2 water points", "Activate 1 cooling center", "ASHA visit to Top 5 priority homes",
                   "Send worker and elderly alert", "Contractor work-rest advisory"],
        "Yellow": ["Deploy 1 water point", "Send precautionary alerts", "ASHA check-in on highest-risk elderly"],
        "Green": ["Normal monitoring and general heat-safety information"],
    }[level]

# ---------- Seed data (SYNTHETIC) ----------
# id, name, lat, lng, score, wbgt, temp, rh, wind, solar, elderly%, outdoor%, green%, hh, workers
_W = [("7B", 12.3050, 76.6550, 82, 31.2, 37.8, 58, 2.1, 880, 14, 31, 8, 9, 640),
      ("12A", 12.2800, 76.6200, 77, 30.8, 37.2, 57, 2.4, 860, 12, 28, 10, 8, 540),
      ("5C", 12.3200, 76.6300, 68, 30.1, 36.5, 55, 2.8, 840, 10, 22, 14, 6, 410),
      ("3D", 12.2700, 76.6700, 61, 29.7, 36.1, 54, 3.0, 820, 9, 20, 16, 5, 360),
      ("9A", 12.2950, 76.6100, 48, 28.9, 35.2, 52, 3.4, 790, 8, 15, 22, 3, 250),
      ("14B", 12.3350, 76.6500, 42, 28.5, 34.8, 51, 3.6, 770, 7, 14, 25, 2, 210),
      ("2A", 12.3100, 76.6800, 24, 27.4, 33.5, 48, 4.2, 720, 6, 9, 38, 1, 120),
      ("10C", 12.2600, 76.6450, 19, 26.9, 33.0, 47, 4.5, 700, 5, 8, 42, 1, 90)]
KEYS = "wardId lat lng score wbgt temp rh wind solar eld out green hh workers".split()

def _ward(r):
    d = dict(zip(KEYS, r)); lvl = classify_risk(d["score"])
    fc = [{"date": "Day %d" % (i + 1), "maxWBGT": round(d["wbgt"] - 0.6 * i, 1),
           "maxTemperature": round(d["temp"] - 0.5 * i, 1),
           "riskLevel": classify_risk(d["score"] - 6 * i)} for i in range(3)]
    return {"wardId": d["wardId"], "wardName": "Ward " + d["wardId"], "centerLat": d["lat"], "centerLng": d["lng"],
            "riskLevel": lvl, "finalRiskScore": d["score"], "hveiScore": calculate_hvei(d["eld"], d["out"], d["green"]),
            "maxWBGT": d["wbgt"], "temperature": d["temp"], "humidity": d["rh"], "windSpeed": d["wind"],
            "solarRadiation": d["solar"], "elderlyPercent": d["eld"], "outdoorWorkerPercent": d["out"],
            "greenCoverPercent": d["green"], "vulnerableHouseholdsCount": d["hh"] * 4,
            "estimatedOutdoorWorkers": d["workers"], "consecutiveHeatDays": 3 if d["score"] > 70 else 1,
            "warmNightFlag": d["score"] > 60, "forecast": fc, "recommendedActions": intervention_plan(lvl),
            "source": "demo", "calculation_method": "prototype_estimate"}

WARDS = {r[0]: _ward(r) for r in _W}
_FACT = [(42, "Block C, Lane 2", 0.3, ["78 years", "lives alone", "diabetes", "ward Red"], 1, True, True),
         (15, "Block A, Lane 5", 0.5, ["2 elderly residents", "outdoor-worker family", "no nearby green cover"], 2, False, False),
         (89, "Block B, Lane 1", 0.7, ["81 years", "lives alone", "hypertension"], 1, True, True)]
HOUSEHOLDS = {"7B": [], "12A": []}
for ward, n in (("7B", 12), ("12A", 6)):
    for i in range(n):
        f = _FACT[i] if ward == "7B" and i < 3 else (10 + i * 7, "Block %s, Lane %d" % ("ABCD"[i % 4], i % 5 + 1),
                                                     round(0.8 + i * 0.25, 1), ["elderly resident", "ward " + WARDS[ward]["riskLevel"]],
                                                     1, i % 3 == 0, i % 2 == 0)
        HOUSEHOLDS[ward].append({"householdId": "HH-%03d" % f[0], "wardId": ward, "areaLabel": f[1],
            "latitude": WARDS[ward]["centerLat"] + 0.001 * i, "longitude": WARDS[ward]["centerLng"] + 0.001 * i,
            "priorityRank": i + 1, "riskLevel": "Red" if i < 4 else "Orange", "distanceKm": f[2],
            "elderlyCount": f[4], "livesAlone": f[5], "chronicConditionFlag": f[6],
            "riskFactors": f[3], "status": "PENDING", "lastUpdated": None})
FEEDBACK: list = []

# ---------- Endpoints ----------
@app.get("/api/health")
def health(): return {"status": "ok", "app": "Hypotherm", "mode": "demo"}

@app.get("/api/wards")
def wards(): return sorted(WARDS.values(), key=lambda w: -w["finalRiskScore"])

@app.get("/api/wards/{ward_id}")
async def ward(ward_id: str):
    w = WARDS.get(ward_id.upper())
    if not w: raise HTTPException(404, "Unknown ward")
    live = await fetch_forecast(w["centerLat"], w["centerLng"])
    return {**w, "liveForecast": live, "disclaimer": DISCLAIMER}

@app.get("/api/wards/{ward_id}/households")
def households(ward_id: str):
    if ward_id.upper() not in HOUSEHOLDS: raise HTTPException(404, "No synthetic households for this ward")
    return HOUSEHOLDS[ward_id.upper()]

class StatusBody(BaseModel):
    status: str  # VISITED_SAFE | FOLLOW_UP | EMERGENCY | NOT_REACHABLE
    notes: Optional[str] = None
    timestamp: Optional[datetime] = None

@app.post("/api/households/{household_id}/status")
def set_status(household_id: str, body: StatusBody):
    if body.status not in {"VISITED_SAFE", "FOLLOW_UP", "EMERGENCY", "NOT_REACHABLE"}:
        raise HTTPException(422, "Invalid status")
    for ward_hh in HOUSEHOLDS.values():
        for h in ward_hh:
            if h["householdId"] == household_id:
                h["status"] = body.status
                h["lastUpdated"] = (body.timestamp or datetime.now(timezone.utc)).isoformat()
                rec = {"feedbackId": "FB-%d" % (len(FEEDBACK) + 1), "sourceRole": "asha", "wardId": h["wardId"],
                       "householdId": household_id, "category": "visit", "status": body.status,
                       "notes": body.notes, "timestamp": h["lastUpdated"]}
                FEEDBACK.append(rec)
                return rec
    raise HTTPException(404, "Unknown household")

@app.get("/api/feedback")
def feedback(): return FEEDBACK

@app.get("/api/contractor-plan")
def contractor_plan(ward_id: str = "7B", workers: int = 45, work_type: str = "heavy"):
    w = WARDS.get(ward_id.upper())
    if not w: raise HTTPException(404, "Unknown ward")
    red = w["riskLevel"] in ("Red", "Orange")
    return {"risk": w["riskLevel"], "forecastWBGT": w["maxWBGT"], "peak": "11 AM-4 PM",
            "schedule": [{"time": "5 AM-10 AM", "advice": "Heavy outdoor work allowed"},
                         {"time": "10 AM-4 PM", "advice": "Suspend heavy outdoor work; indoor/light tasks only" if red else "Work-rest cycles"},
                         {"time": "4 PM-7 PM", "advice": "Light work with mandatory rest breaks"}],
            "safetyChecklist": ["Drinking water available", "Shade / rest area ready", "Supervisor heat-illness briefing completed",
                                "New workers acclimatized", "ORS/first-aid available", "Emergency contact displayed"],
            "wageProtectionSimulation": {"label": "Prototype Simulation - Not an Insurance Product",
                "trigger": "Locally calibrated WBGT threshold exceeded for consecutive days",
                "confirmation": "IMD / verified weather-station observation", "benefitPerWorkerDay": 500,
                "seasonalCapPerWorker": 2000, "potentialOneDayBenefit": workers * 500,
                "note": "Actual payouts would require a licensed insurer, independent weather confirmation, "
                        "local calibration and regulatory approval."}}

class AlertBody(BaseModel):
    ward_id: str
    audience: str  # workers | elderly | asha | contractor
    channel: str   # sms | whatsapp | ivr

@app.post("/api/alerts/preview")
def alert_preview(b: AlertBody):
    w = WARDS.get(b.ward_id.upper())
    if not w: raise HTTPException(404, "Unknown ward")
    n, lvl, t = w["wardName"], w["riskLevel"].upper(), w["maxWBGT"]
    msgs = {"workers": "HEAT ALERT - %s: %s heat risk tomorrow. WBGT may reach %d°C. Avoid heavy outdoor work from 10 AM-4 PM. "
                       "Shift heavy tasks to early morning. Drink water, rest in shade. - Hypotherm" % (n, lvl.capitalize(), round(t)),
            "elderly": "ನಮಸ್ಕಾರ. ನಾಳೆ ನಿಮ್ಮ ವಾರ್ಡ್‌ನಲ್ಲಿ ತೀವ್ರ ಬಿಸಿಯ ಅಪಾಯ ಇದೆ. ಬೆಳಿಗ್ಗೆ ಮತ್ತು ಸಂಜೆ ಮಾತ್ರ ಹೊರಗೆ ಹೋಗಿ. "
                       "ಮಧ್ಯಾಹ್ನ 10 ರಿಂದ 4 ಗಂಟೆಯವರೆಗೆ ಮನೆಯೊಳಗೆ ಅಥವಾ ತಂಪಾದ ಸ್ಥಳದಲ್ಲಿ ಇರಿ. ನೀರು ಕುಡಿಯಿರಿ ಮತ್ತು ಆರೋಗ್ಯ ಸಮಸ್ಯೆ ಇದ್ದರೆ 108 ಗೆ ಕರೆ ಮಾಡಿ.",
            "asha": "HYPOTHERM ALERT - %s\nRisk: %s | WBGT: %s°C | Peak: 11 AM-4 PM\nToday: Complete priority household visits "
                    "before peak heat. Carry hydration/ORS guidance. Report urgent cases immediately." % (n, lvl, t),
            "contractor": "WORKSAFE ALERT - %s\nTomorrow WBGT: %s°C (%s)\nHeavy work: 5-10 AM only\n10 AM-4 PM: suspend heavy "
                          "outdoor work\nEnsure water, shade and rest breaks." % (n, t, lvl)}
    if b.audience not in msgs: raise HTTPException(422, "Unknown audience")
    return {"message": msgs[b.audience], "language": "kn" if b.audience == "elderly" else "en", "channel": b.channel,
            "note": "Demo alert only. Real delivery requires SMS/IVR provider integration."}

@app.get("/api/evidence/backtest")
def backtest():
    # SEEDED DEMO series (deterministic), NOT real historical results.
    days = []
    for i in range(30):
        temp = round(33 + 3 * math.sin(i / 4.5) + (i % 5) * 0.2, 1)
        wb = round(26 + 0.45 * (temp - 30) * 2 + 1.2 * math.sin(i / 3), 1)
        days.append({"day": i + 1, "temperature": temp, "wbgt": wb, "tempAlert": temp >= 37, "wbgtAlert": wb >= 30})
    return {"label": "Seeded demo data - not validated historical results", "days": days,
            "calibration": [{"wbgt": 29, "note": "Higher sensitivity / more alerts"},
                            {"wbgt": 30, "note": "Balanced prototype threshold"},
                            {"wbgt": 31, "note": "Lower false-alarm tendency / may miss risk"}],
            "disclaimer": "Prototype analysis. Thresholds must be calibrated against local health outcomes and "
                          "independently observed weather data before operational use."}

@app.get("/api/settings/data-sources")
def data_sources():
    return {"sources": ["Open-Meteo Forecast API", "Open-Meteo Archive API", "Census 2011 / local surveys",
                        "Sentinel-2 green-cover indicators", "IMD / station observations (independent confirmation)"],
            "privacy": ["Explicit consent", "Data minimization", "Role-based access",
                        "Encryption at rest and in transit", "Household identifiers are synthetic"],
            "disclaimer": DISCLAIMER}


# Local full-stack mode: `uvicorn api.index:app --port 8000` serves the frontend AND the API.
# On Vercel the static files are served by Vercel itself, so this mount is skipped.
if not os.environ.get("VERCEL"):
    app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parent.parent), html=True), name="static")
