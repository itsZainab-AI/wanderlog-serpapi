# Wanderlog — Your AI Travel Buddy

**Live demo → [wanderlog-u89f.onrender.com](https://wanderlog-u89f.onrender.com/)**

*(hosted on Render's free tier — if it's been idle a while, the first load can take 30–50 seconds to wake up. Worth the wait.)*

An AI-powered travel itinerary planner that generates real, destination-specific day-by-day trip plans — not generic "visit a museum" filler — and presents them with an editorial, human-touch design instead of a typical AI-dashboard look.

## What it does

- **Acts as an AI Reasoning Travel Agent** — not a static template generator. It searches live data and reasons over real options before building your trip.
- **Fetches live search results** — integrates SerpApi for real Google Flights, Google Hotels, and Google Maps/Local results.
- **Visible Reasoning Trail** — displays "Why I picked this" rationale for selected flights, hotels, and neighborhood clusters.
- **Interactive Day Adjustment Loop** — edit or re-reason single days ("make day 2 lighter") without regenerating the whole trip.
- **Editable on the fly & Chat companion** — swap places or text with your travel buddy who knows your itinerary.

## How this project uses SerpApi

Wanderlog operates as an autonomous reasoning agent powered by **SerpApi**:

1. **Google Flights API (`engine="google_flights"`)**: Pulls real flight routes, prices, and durations from origin to destination for the requested dates. Gemini evaluates these options against the user's budget and schedule.
2. **Google Hotels API (`engine="google_hotels"`)**: Searches live accommodations near the destination. The AI agent evaluates hotel locations relative to planned attraction hubs to minimize daily transit.
3. **Google Maps / Local API (`engine="google_maps"`)**: Fetches real top-rated attractions, restaurants, and hidden gems matching the trip vibe (foodie, culture, adventure, relaxed).
4. **Agent Reasoning Step**: Instead of echoing API data, Gemini receives the raw SerpApi JSON and performs spatial clustering (grouping places in close geographic proximity per day) and generates an explicit **Reasoning Trail** shown in the UI.

---

## Tech stack

- **Backend:** FastAPI (Python) + SQLAlchemy
- **AI:** Google Gemini API — `gemini-3.6-flash` for itinerary generation, `gemini-3.5-flash-lite` for chat
- **Frontend:** Vanilla HTML/CSS/JS, served directly by the FastAPI backend
- **Deployment:** Render

## Running it locally

```bash
git clone https://github.com/itsZainab-AI/wanderlog.git
cd wanderlog/backend

python -m venv venv
venv\Scripts\Activate.ps1   # Windows PowerShell
# source venv/bin/activate  # macOS/Linux

pip install -r requirements.txt
```

Create a `.env` file in `backend/` with:
```
GOOGLE_API_KEY=your-gemini-api-key-here
```
(get a free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey))

Then run:
```bash
uvicorn main:app --reload --port 8000
```
Visit `http://localhost:8000`.

## Project structure

```
wanderlog/
├── backend/
│   ├── main.py           # FastAPI app, itinerary + chat generation
│   ├── requirements.txt
│   └── ...
└── frontend/
    └── index.html         # Single-page app UI
```

---

Built by [Zainab](https://github.com/itsZainab-AI) —  AI/ML student.