# HYPOTHERM — Heat Intelligence for Safer Communities
Prototype • synthetic Mysuru demo data. Wage-protection card = "Prototype Simulation — Not an Insurance Product".
Thresholds are prototype settings needing local health-outcome calibration.

## Layout
index.html, css/, js/  — frontend (static)  |  api/index.py — FastAPI backend (also the Vercel function)
## Run locally (frontend + backend together)
    python -m venv .venv && .venv\Scripts\activate      (Windows)
    pip install -r requirements.txt
    uvicorn api.index:app --reload --port 8000
Open http://localhost:8000 — Command Centre shows "Data source: FastAPI backend". API docs: /docs
(Opening index.html with Live Server alone still works, using built-in demo data.)
## Deploy
Push to GitHub, import the repo in Vercel (Framework: Other, no build command). Vercel serves the static files and runs api/index.py.
Note: serverless has no persistent memory; ASHA statuses persist in the browser (localStorage), the API call is best-effort.
