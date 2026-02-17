# GB Electricity Market Dashboard — Backend Proxy

A lightweight Flask proxy that sits between your dashboard frontend and the
[Elexon BMRS Insights API](https://developer.data.elexon.co.uk/), adding:

- **In-memory caching** — reduces Elexon API calls dramatically
- **CORS headers** — your frontend can live on any domain
- **A single `/api/summary` endpoint** — one call returns all KPIs
- **Raw passthrough** — access any Elexon endpoint via `/api/raw/<path>`
- **Cache management** — inspect and clear via API

---

## Project structure

```
electricity-backend/
├── app.py               ← Flask proxy server (main file)
├── requirements.txt     ← Python dependencies
├── Procfile             ← For Railway / Render / Heroku
├── Dockerfile           ← For container deployments
├── railway.toml         ← Railway-specific config
├── render.yaml          ← Render-specific config
├── .gitignore
├── README.md
└── static/
    └── index.html       ← Your dashboard frontend (served by the proxy)
```

---

## Run locally

```bash
# 1. Create a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the server
python app.py
# → Running on http://localhost:8000

# 4. Open your dashboard
open http://localhost:8000/static/index.html

# Or hit the API directly
curl http://localhost:8000/api/summary | python3 -m json.tool
```

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check + list of all endpoints |
| `GET` | `/api/settlement-period` | Current SP number, date, window |
| `GET` | `/api/summary` | All KPIs in one call (cached 60s) |
| `GET` | `/api/generation` | Half-hourly generation by fuel (FUELHH) |
| `GET` | `/api/generation/latest` | Latest SP fuel breakdown only |
| `GET` | `/api/fuel-mix/latest` | Clean % mix + renewable/low-carbon totals |
| `GET` | `/api/demand` | System demand outturn (INDO) |
| `GET` | `/api/price` | Market index price £/MWh (MID) |
| `GET` | `/api/imbalance` | System imbalance (IMBALNGC) |
| `GET` | `/api/frequency` | System frequency (FREQ) |
| `GET` | `/api/raw/<elexon_path>` | Pass any Elexon endpoint through |
| `GET` | `/api/cache/stats` | Inspect cache keys + TTLs |
| `POST` | `/api/cache/clear` | Clear all cache (or `?key=...` for one key) |

### Query parameters

Most endpoints accept a `?date=YYYY-MM-DD` parameter:

```bash
curl "http://localhost:8000/api/price?date=2025-02-16"
curl "http://localhost:8000/api/generation?date_from=2025-02-16&date=2025-02-17"
```

### Cache TTLs

| Endpoint | TTL | Reason |
|----------|-----|--------|
| `frequency` | 60s | Published every ~30s |
| `summary` | 60s | Composite — refresh often |
| `generation`, `demand`, `price`, `imbalance` | 600s | Half-hourly data |
| `raw` passthrough | 300s | Conservative default |

---

## Connect the frontend

In `static/index.html`, find this line near the top of the `<script>`:

```javascript
const PROXY_BASE = "";   // ← CHANGE THIS after deploying your backend
```

Change it to your deployed URL:

```javascript
const PROXY_BASE = "https://your-app.up.railway.app";
```

The frontend will then show **"Via Proxy"** (green badge) in the header instead of "Direct API".

---

## Deploy to Railway (recommended — free tier available)

Railway is the easiest option. Takes about 3 minutes.

```bash
# 1. Push your code to GitHub
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/electricity-backend.git
git push -u origin main

# 2. Go to railway.app → New Project → Deploy from GitHub repo
# 3. Select your repo — Railway auto-detects Python and deploys
# 4. Copy the generated URL (e.g. https://electricity-backend.up.railway.app)
# 5. Paste it into PROXY_BASE in static/index.html
```

Railway gives you a free tier with 500 hours/month and automatic HTTPS.

---

## Deploy to Render (also free)

```bash
# 1. Push to GitHub (same as above)
# 2. Go to render.com → New → Web Service
# 3. Connect your GitHub repo
# 4. Render detects render.yaml automatically
# 5. Click Deploy — takes ~2 minutes
# 6. Copy the .onrender.com URL into PROXY_BASE
```

---

## Deploy with Docker

```bash
# Build
docker build -t electricity-proxy .

# Run locally
docker run -p 8000:8000 electricity-proxy

# Deploy to any container host (Fly.io, Google Cloud Run, etc.)
docker tag electricity-proxy registry.fly.io/your-app:latest
flyctl deploy
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | Port to listen on (set automatically by Railway/Render) |
| `FLASK_DEBUG` | `false` | Enable debug mode (never use in production) |

---

## Adding persistent storage (optional upgrade)

The current cache is **in-memory only** — it resets when the server restarts.

To persist data across restarts and store historical time-series, add Redis:

```python
# In app.py, replace the in-memory cache with:
import redis, json
r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))

def cache_get(key):
    v = r.get(key)
    return json.loads(v) if v else None

def cache_set(key, data, ttl):
    r.setex(key, ttl, json.dumps(data))
```

Railway and Render both offer free Redis add-ons.
