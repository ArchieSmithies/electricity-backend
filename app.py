"""
GB Electricity Market Dashboard — Backend Proxy
================================================
Proxies and caches the Elexon BMRS public API.
Cache TTL is set per endpoint based on how often data updates.

Run locally:
    python app.py

Deploy (Railway / Render):
    See README.md
"""

import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from functools import wraps

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ── Config ────────────────────────────────────────────────────────────────────

ELEXON_BASE = "https://data.elexon.co.uk/bmrs/api/v1"

# Cache TTL (seconds) per route key
CACHE_TTL = {
    "generation":   600,   # 10 min — half-hourly data, republished ~5 min after SP end
    "demand":       600,
    "price":        600,
    "imbalance":    600,
    "frequency":     60,   # 1 min  — published every ~30 sec
    "default":      300,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static")
CORS(app)  # allow requests from any origin (your HTML frontend)

# ── In-memory cache ───────────────────────────────────────────────────────────

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def cache_get(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() < entry["expires"]:
            age = int(time.time() - entry["stored"])
            log.info(f"CACHE HIT  {key} (age {age}s)")
            return entry["data"]
    return None


def cache_set(key: str, data, ttl: int):
    with _cache_lock:
        _cache[key] = {
            "data":    data,
            "stored":  time.time(),
            "expires": time.time() + ttl,
        }
    log.info(f"CACHE SET  {key} ttl={ttl}s")


def cache_stats():
    with _cache_lock:
        stats = {}
        now = time.time()
        for key, entry in _cache.items():
            stats[key] = {
                "age_seconds":     int(now - entry["stored"]),
                "expires_in":      max(0, int(entry["expires"] - now)),
                "cached_at_utc":   datetime.fromtimestamp(entry["stored"], tz=timezone.utc).isoformat(),
            }
        return stats

# ── Helpers ───────────────────────────────────────────────────────────────────

def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def yesterday_str() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

def current_settlement_period() -> dict:
    now = datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    sp = minutes // 30 + 1
    sp_start = now.replace(minute=(sp - 1) % 2 * 30, second=0, microsecond=0)
    if (sp - 1) * 30 >= 60:
        sp_start = sp_start.replace(hour=(sp - 1) * 30 // 60, minute=(sp - 1) * 30 % 60)
    sp_end = sp_start + timedelta(minutes=30)
    return {
        "settlement_date":   now.strftime("%Y-%m-%d"),
        "settlement_period": sp,
        "period_start_utc":  sp_start.isoformat(),
        "period_end_utc":    sp_end.isoformat(),
        "periods_per_day":   48,
        "next_sp":           (sp % 48) + 1,
    }

def fetch_elexon(path: str, params: dict = None) -> dict:
    """Fetch from Elexon API and return parsed JSON."""
    url = f"{ELEXON_BASE}/{path.lstrip('/')}"
    params = {**(params or {}), "format": "json"}
    log.info(f"ELEXON GET {url} params={params}")
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()

def proxy_route(cache_key_fn, ttl_key="default"):
    """
    Decorator: wraps a view function that returns (elexon_path, params).
    Handles caching, error wrapping, and CORS for all proxy routes.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            cache_key = cache_key_fn(*args, **kwargs)
            cached = cache_get(cache_key)
            if cached is not None:
                return jsonify({**cached, "_cache": "hit", "_key": cache_key})

            try:
                path, params = fn(*args, **kwargs)
                data = fetch_elexon(path, params)
                ttl = CACHE_TTL.get(ttl_key, CACHE_TTL["default"])
                cache_set(cache_key, data, ttl)
                return jsonify({**data, "_cache": "miss", "_key": cache_key})
            except requests.HTTPError as e:
                log.error(f"Elexon HTTP error: {e}")
                return jsonify({"error": str(e), "status": e.response.status_code}), 502
            except requests.RequestException as e:
                log.error(f"Elexon request failed: {e}")
                return jsonify({"error": "Upstream unavailable", "detail": str(e)}), 503
        return wrapper
    return decorator


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Health check / info."""
    return jsonify({
        "service":     "GB Electricity Market Proxy",
        "status":      "ok",
        "upstream":    ELEXON_BASE,
        "current_sp":  current_settlement_period(),
        "endpoints": {
            "settlement_period":  "/api/settlement-period",
            "generation":         "/api/generation?date=YYYY-MM-DD",
            "generation_latest":  "/api/generation/latest",
            "demand":             "/api/demand?date=YYYY-MM-DD",
            "price":              "/api/price?date=YYYY-MM-DD",
            "imbalance":          "/api/imbalance?date=YYYY-MM-DD",
            "frequency":          "/api/frequency",
            "fuel_mix_latest":    "/api/fuel-mix/latest",
            "summary":            "/api/summary",
            "cache_stats":        "/api/cache/stats",
            "cache_clear":        "POST /api/cache/clear",
        }
    })


@app.route("/api/settlement-period")
def settlement_period():
    """Current settlement period info."""
    return jsonify(current_settlement_period())


# ── Generation ────────────────────────────────────────────────────────────────

@app.route("/api/generation")
@proxy_route(
    cache_key_fn=lambda: f"generation:{request.args.get('date', today_str())}:{request.args.get('date_from', yesterday_str())}",
    ttl_key="generation"
)
def generation():
    date_to   = request.args.get("date", today_str())
    date_from = request.args.get("date_from", yesterday_str())
    return "generation/outturn/halfHourly", {
        "settlementDateFrom": date_from,
        "settlementDateTo":   date_to,
    }


@app.route("/api/generation/latest")
def generation_latest():
    """Return only the most recent settlement period's generation data."""
    cache_key = "generation_latest"
    cached = cache_get(cache_key)
    if cached:
        return jsonify({**cached, "_cache": "hit"})
    try:
        raw = fetch_elexon("generation/outturn/halfHourly", {
            "settlementDateFrom": today_str(),
            "settlementDateTo":   today_str(),
        })
        data = raw.get("data", [])
        if not data:
            return jsonify({"error": "No data available"}), 404

        # Find latest SP
        max_sp = max(d["settlementPeriod"] for d in data)
        latest = [d for d in data if d["settlementPeriod"] == max_sp]
        total  = sum(d.get("generation", d.get("quantity", 0)) for d in latest)

        result = {
            "settlement_date":   today_str(),
            "settlement_period": max_sp,
            "total_mw":          round(total),
            "fuels": {
                d["fuelType"]: round(d.get("generation", d.get("quantity", 0)))
                for d in latest if d.get("fuelType")
            }
        }
        cache_set(cache_key, result, CACHE_TTL["generation"])
        return jsonify({**result, "_cache": "miss"})
    except Exception as e:
        return jsonify({"error": str(e)}), 503


# ── Demand ────────────────────────────────────────────────────────────────────

@app.route("/api/demand")
@proxy_route(
    cache_key_fn=lambda: f"demand:{request.args.get('date', today_str())}",
    ttl_key="demand"
)
def demand():
    date = request.args.get("date", today_str())
    return "demand/outturn", {
        "settlementDateFrom": date,
        "settlementDateTo":   date,
    }


# ── Market Index Price ────────────────────────────────────────────────────────

@app.route("/api/price")
@proxy_route(
    cache_key_fn=lambda: f"price:{request.args.get('date', today_str())}",
    ttl_key="price"
)
def price():
    date = request.args.get("date", today_str())
    return "balancing/pricing/market-index", {"settlementDate": date}


# ── Imbalance ─────────────────────────────────────────────────────────────────

@app.route("/api/imbalance")
@proxy_route(
    cache_key_fn=lambda: f"imbalance:{request.args.get('date', today_str())}",
    ttl_key="imbalance"
)
def imbalance():
    date = request.args.get("date", today_str())
    return "datasets/IMBALNGC", {
        "settlementDateFrom": date,
        "settlementDateTo":   date,
    }


# ── Frequency ─────────────────────────────────────────────────────────────────

@app.route("/api/frequency")
@proxy_route(
    cache_key_fn=lambda: "frequency:latest",
    ttl_key="frequency"
)
def frequency():
    from_dt = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "datasets/FREQ", {"publishDateTimeFrom": from_dt}


# ── Fuel mix summary ──────────────────────────────────────────────────────────

@app.route("/api/fuel-mix/latest")
def fuel_mix_latest():
    """
    Returns clean fuel mix percentages for the latest SP.
    Useful for embedding in other apps or widgets.
    """
    cache_key = "fuel_mix_latest"
    cached = cache_get(cache_key)
    if cached:
        return jsonify({**cached, "_cache": "hit"})

    try:
        raw = fetch_elexon("generation/outturn/halfHourly", {
            "settlementDateFrom": today_str(),
            "settlementDateTo":   today_str(),
        })
        data = raw.get("data", [])
        if not data:
            return jsonify({"error": "No data"}), 404

        max_sp = max(d["settlementPeriod"] for d in data)
        latest = [d for d in data if d["settlementPeriod"] == max_sp]
        total  = sum(d.get("generation", d.get("quantity", 0)) for d in latest)

        fuel_labels = {
            "CCGT": "Gas CCGT", "OCGT": "Gas OCGT", "OIL": "Oil", "COAL": "Coal",
            "NUCLEAR": "Nuclear", "WIND": "Wind", "PS": "Pumped Storage",
            "NPSHYD": "Hydro", "OTHER": "Other", "INTFR": "France IC",
            "INTIRL": "Ireland IC", "INTNED": "Netherlands IC", "INTEW": "E-W IC",
            "INTNEM": "NEMO IC", "BIOMASS": "Biomass", "SOLAR": "Solar",
        }

        fuels = []
        for d in sorted(latest, key=lambda x: x.get("generation", 0), reverse=True):
            ft = d.get("fuelType")
            mw = round(d.get("generation", d.get("quantity", 0)))
            if ft and mw > 0:
                fuels.append({
                    "fuel_type":  ft,
                    "label":      fuel_labels.get(ft, ft),
                    "mw":         mw,
                    "percentage": round(mw / total * 100, 1) if total else 0,
                })

        renewable_types = {"WIND", "SOLAR", "NPSHYD", "HYDRO", "BIOMASS"}
        renewable_mw    = sum(f["mw"] for f in fuels if f["fuel_type"] in renewable_types)
        low_carbon_types = renewable_types | {"NUCLEAR"}
        low_carbon_mw   = sum(f["mw"] for f in fuels if f["fuel_type"] in low_carbon_types)

        result = {
            "settlement_date":     today_str(),
            "settlement_period":   max_sp,
            "total_mw":            round(total),
            "renewable_mw":        round(renewable_mw),
            "renewable_pct":       round(renewable_mw / total * 100, 1) if total else 0,
            "low_carbon_mw":       round(low_carbon_mw),
            "low_carbon_pct":      round(low_carbon_mw / total * 100, 1) if total else 0,
            "fuels":               fuels,
        }
        cache_set(cache_key, result, CACHE_TTL["generation"])
        return jsonify({**result, "_cache": "miss"})
    except Exception as e:
        log.error(f"fuel-mix error: {e}")
        return jsonify({"error": str(e)}), 503


# ── Summary (all KPIs in one call) ────────────────────────────────────────────

@app.route("/api/summary")
def summary():
    """
    Single endpoint that returns all dashboard KPIs.
    Frontend can call just this instead of 5 separate endpoints.
    Useful for reducing page-load requests.
    """
    cache_key = "summary"
    cached = cache_get(cache_key)
    if cached:
        return jsonify({**cached, "_cache": "hit"})

    result = {"settlement_period": current_settlement_period()}
    errors = {}

    # Generation / fuel mix
    try:
        raw = fetch_elexon("generation/outturn/halfHourly", {
            "settlementDateFrom": today_str(),
            "settlementDateTo":   today_str(),
        })
        data = raw.get("data", [])
        if data:
            max_sp = max(d["settlementPeriod"] for d in data)
            latest = [d for d in data if d["settlementPeriod"] == max_sp]
            total  = sum(d.get("generation", d.get("quantity", 0)) for d in latest)
            fuels  = {d["fuelType"]: round(d.get("generation", d.get("quantity", 0)))
                      for d in latest if d.get("fuelType")}
            result["generation"] = {
                "total_mw":   round(total),
                "fuels":      fuels,
                "wind_mw":    fuels.get("WIND", 0),
                "solar_mw":   fuels.get("SOLAR", 0),
                "nuclear_mw": fuels.get("NUCLEAR", 0),
                "wind_pct":   round(fuels.get("WIND", 0) / total * 100, 1) if total else 0,
                "solar_pct":  round(fuels.get("SOLAR", 0) / total * 100, 1) if total else 0,
            }
    except Exception as e:
        errors["generation"] = str(e)

    # Demand
    try:
        raw = fetch_elexon("demand/outturn", {
            "settlementDateFrom": today_str(),
            "settlementDateTo":   today_str(),
        })
        data = sorted(raw.get("data", []), key=lambda d: d["settlementPeriod"])
        if data:
            latest = data[-1]
            result["demand"] = {
                "mw":               latest.get("initialDemandOutturn", latest.get("demand", 0)),
                "settlement_period": latest["settlementPeriod"],
            }
    except Exception as e:
        errors["demand"] = str(e)

    # Price
    try:
        raw = fetch_elexon("balancing/pricing/market-index", {"settlementDate": today_str()})
        data = sorted([d for d in raw.get("data", []) if d.get("price") is not None],
                      key=lambda d: d["settlementPeriod"])
        if data:
            latest = data[-1]
            prev   = data[-2] if len(data) >= 2 else latest
            result["price"] = {
                "gbp_per_mwh":      round(latest["price"], 2),
                "settlement_period": latest["settlementPeriod"],
                "change_gbp":       round(latest["price"] - prev["price"], 2),
            }
    except Exception as e:
        errors["price"] = str(e)

    # Imbalance
    try:
        raw = fetch_elexon("datasets/IMBALNGC", {
            "settlementDateFrom": today_str(),
            "settlementDateTo":   today_str(),
        })
        data = sorted(raw.get("data", []), key=lambda d: d["settlementPeriod"])
        if data:
            latest = data[-1]
            result["imbalance"] = {
                "mw":               round(latest.get("imbalance", latest.get("indicatedImbalance", latest.get("value", 0)))),
                "settlement_period": latest["settlementPeriod"],
            }
    except Exception as e:
        errors["imbalance"] = str(e)

    # Frequency
    try:
        from_dt = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw  = fetch_elexon("datasets/FREQ", {"publishDateTimeFrom": from_dt})
        data = sorted(raw.get("data", []), key=lambda d: d.get("publishTime", ""))
        if data:
            latest = data[-1]
            freq   = latest.get("frequency", latest.get("value", 50.0))
            result["frequency"] = {
                "hz":          round(freq, 3),
                "nominal_hz":  50.0,
                "deviation":   round(freq - 50.0, 3),
                "status":      "normal" if abs(freq - 50) < 0.05 else "deviation" if abs(freq - 50) < 0.2 else "alert",
                "published_at": latest.get("publishTime"),
            }
    except Exception as e:
        errors["frequency"] = str(e)

    if errors:
        result["_errors"] = errors

    cache_set(cache_key, result, 60)  # 1 min TTL for the summary
    return jsonify({**result, "_cache": "miss"})


# ── Cache management ──────────────────────────────────────────────────────────

@app.route("/api/cache/stats")
def api_cache_stats():
    return jsonify({"cache": cache_stats(), "total_keys": len(_cache)})


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    key = request.args.get("key")
    with _cache_lock:
        if key:
            removed = _cache.pop(key, None)
            return jsonify({"cleared": key, "found": removed is not None})
        count = len(_cache)
        _cache.clear()
    return jsonify({"cleared": "all", "count": count})


# ── Raw passthrough (escape hatch) ───────────────────────────────────────────

@app.route("/api/raw/<path:elexon_path>")
def raw_passthrough(elexon_path):
    """
    Pass any Elexon endpoint through directly.
    e.g. GET /api/raw/datasets/BOAL?settlementDate=2025-02-17
    Uses a short 5-min cache keyed by full URL + params.
    """
    params = dict(request.args)
    cache_key = f"raw:{elexon_path}:{sorted(params.items())}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify({**cached, "_cache": "hit"})
    try:
        data = fetch_elexon(elexon_path, params)
        cache_set(cache_key, data, 300)
        return jsonify({**data, "_cache": "miss"})
    except requests.HTTPError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 503


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "endpoint not found", "hint": "GET / for available routes"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "internal server error", "detail": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    log.info(f"Starting GB Electricity Proxy on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
