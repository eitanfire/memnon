from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TIMEOUT_SECONDS = 4.0
US_STATE_NAMES = {
    "AL": "alabama",
    "AK": "alaska",
    "AZ": "arizona",
    "AR": "arkansas",
    "CA": "california",
    "CO": "colorado",
    "CT": "connecticut",
    "DE": "delaware",
    "FL": "florida",
    "GA": "georgia",
    "HI": "hawaii",
    "ID": "idaho",
    "IL": "illinois",
    "IN": "indiana",
    "IA": "iowa",
    "KS": "kansas",
    "KY": "kentucky",
    "LA": "louisiana",
    "ME": "maine",
    "MD": "maryland",
    "MA": "massachusetts",
    "MI": "michigan",
    "MN": "minnesota",
    "MS": "mississippi",
    "MO": "missouri",
    "MT": "montana",
    "NE": "nebraska",
    "NV": "nevada",
    "NH": "new hampshire",
    "NJ": "new jersey",
    "NM": "new mexico",
    "NY": "new york",
    "NC": "north carolina",
    "ND": "north dakota",
    "OH": "ohio",
    "OK": "oklahoma",
    "OR": "oregon",
    "PA": "pennsylvania",
    "RI": "rhode island",
    "SC": "south carolina",
    "SD": "south dakota",
    "TN": "tennessee",
    "TX": "texas",
    "UT": "utah",
    "VT": "vermont",
    "VA": "virginia",
    "WA": "washington",
    "WV": "west virginia",
    "WI": "wisconsin",
    "WY": "wyoming",
    "DC": "district of columbia",
}


def build_weather_anchor(user_data: dict) -> str:
    school_name = str((user_data or {}).get("school_name") or "").strip()
    school_city = str((user_data or {}).get("school_city") or "").strip()
    school_state = str((user_data or {}).get("school_state") or "").strip()
    if not school_name:
        return school_city
    return f"{school_name}, {school_state}" if school_state else school_name


def clear_weather_cache_fields() -> dict:
    return {
        "weather_location_label": "",
        "weather_latitude": None,
        "weather_longitude": None,
        "weather_timezone": "",
        "weather_geocoded_from": "",
        "weather_location_updated_at": None,
    }


def _fetch_json(url: str, *, timeout_seconds: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "memnon-weather/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _weather_code_to_day_type(code: int) -> str:
    if code in {95, 96, 99}:
        return "stormy"
    if code in {61, 63, 65, 80, 81, 82}:
        return "rainy"
    if code in {71, 73, 75, 77, 85, 86}:
        return "cold"
    if code in {0, 1}:
        return "clear"
    return "mixed"


def _build_orientation_cue(day_type: str, high: float, low: float, precip_probability: int) -> str:
    if day_type == "stormy":
        return "A stormy afternoon may make transitions and end-of-day energy heavier than usual."
    if day_type == "rainy":
        return "Rain is likely today, so it helps to plan for wetter transitions and a more compressed rhythm."
    if high >= 88:
        return "Heat may drain energy faster than usual, especially later in the day."
    if low <= 35:
        return "A colder day may make the morning start feel slower and more effortful."
    if precip_probability <= 10 and high >= 60 and low >= 45:
        return "The weather looks steady enough to support a more open, straightforward day."
    return "The weather looks mixed, so it is worth keeping your pacing a little flexible."


def _resolved_location_matches_anchor(top: dict, anchor: str) -> bool:
    school_name = anchor.split(",")[0].strip().lower()
    resolved_name = str(top.get("name") or "").strip().lower()
    return bool(school_name and resolved_name and school_name in resolved_name)


def _resolved_location_matches_city(top: dict, user_data: dict) -> bool:
    school_city = str((user_data or {}).get("school_city") or "").strip().lower()
    school_state = str((user_data or {}).get("school_state") or "").strip().lower()
    resolved_name = str(top.get("name") or "").strip().lower()
    resolved_state = str(top.get("admin1") or "").strip().lower()
    if not school_city or resolved_name != school_city:
        return False
    if not school_state:
        return True
    if resolved_state == school_state:
        return True
    return US_STATE_NAMES.get(school_state.upper(), "") == resolved_state


def _build_weather_queries(user_data: dict) -> list[tuple[str, str]]:
    school_name = str((user_data or {}).get("school_name") or "").strip()
    school_city = str((user_data or {}).get("school_city") or "").strip()
    school_state = str((user_data or {}).get("school_state") or "").strip()
    queries: list[tuple[str, str]] = []
    if school_name:
        queries.append(("school_name", f"{school_name}, {school_state}" if school_state else school_name))
    if school_city and school_city.lower() != school_name.lower():
        queries.append(("school_city", school_city))
    return queries


def load_weather_context(
    user_data: dict,
    *,
    timeout_seconds: float = WEATHER_TIMEOUT_SECONDS,
) -> tuple[dict | None, dict]:
    anchors = _build_weather_queries(user_data)
    if not anchors:
        return None, {}

    cached_anchor = str((user_data or {}).get("weather_geocoded_from") or "").strip()
    latitude = (user_data or {}).get("weather_latitude")
    longitude = (user_data or {}).get("weather_longitude")
    timezone_name = str((user_data or {}).get("weather_timezone") or "").strip()
    cache_updates: dict = {}

    allowed_cached_anchors = {anchor for _, anchor in anchors}
    if not (cached_anchor in allowed_cached_anchors and latitude is not None and longitude is not None):
        for anchor_kind, anchor in anchors:
            geocode_query = urllib.parse.urlencode({
                "name": anchor,
                "count": 1,
                "language": "en",
                "format": "json",
            })
            geocode_payload = _fetch_json(
                f"{OPEN_METEO_GEOCODE_URL}?{geocode_query}",
                timeout_seconds=timeout_seconds,
            )
            results = geocode_payload.get("results") or []
            if not results:
                continue
            top = results[0]
            if anchor_kind == "school_name":
                matches_anchor = _resolved_location_matches_anchor(top, anchor)
            else:
                matches_anchor = _resolved_location_matches_city(top, user_data)
            if not top.get("timezone") or not matches_anchor:
                continue
            latitude = top.get("latitude")
            longitude = top.get("longitude")
            timezone_name = str(top.get("timezone") or "").strip()
            if latitude is None or longitude is None or not timezone_name:
                continue
            resolved_name = " ".join(
                str(top.get(key) or "").strip()
                for key in ("name", "admin1")
                if str(top.get(key) or "").strip()
            )
            cache_updates = {
                "weather_location_label": resolved_name,
                "weather_latitude": latitude,
                "weather_longitude": longitude,
                "weather_timezone": timezone_name,
                "weather_geocoded_from": anchor,
                "weather_location_updated_at": datetime.now(timezone.utc),
            }
            break
        if latitude is None or longitude is None or not timezone_name:
            return None, {}

    forecast_query = urllib.parse.urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone_name or "auto",
        "temperature_unit": "fahrenheit",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,weathercode",
        "forecast_days": 1,
    })
    forecast_payload = _fetch_json(
        f"{OPEN_METEO_FORECAST_URL}?{forecast_query}",
        timeout_seconds=timeout_seconds,
    )
    daily = forecast_payload.get("daily") or {}
    high = float((daily.get("temperature_2m_max") or [0])[0])
    low = float((daily.get("temperature_2m_min") or [0])[0])
    precip_probability = int((daily.get("precipitation_probability_max") or [0])[0])
    precip_sum = float((daily.get("precipitation_sum") or [0])[0])
    weather_code = int((daily.get("weathercode") or [3])[0])
    day_type = _weather_code_to_day_type(weather_code)

    context = {
        "day_type": day_type,
        "temperature_summary": f"High {round(high)}F, low {round(low)}F",
        "precipitation_summary": f"{precip_probability}% precipitation risk, {precip_sum:.1f} mm expected",
        "orientation_cue": _build_orientation_cue(day_type, high, low, precip_probability),
    }
    return context, cache_updates
