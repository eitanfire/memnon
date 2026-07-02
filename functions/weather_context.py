from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TIMEOUT_SECONDS = 4.0
WEATHER_MICRO_CUE_LABEL = "Outside context"


def build_weather_anchor(user_data: dict) -> str:
    school_name = str((user_data or {}).get("school_name") or "").strip()
    school_state = str((user_data or {}).get("school_state") or "").strip()
    if not school_name:
        return ""
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
        return "snowy"
    if code in {0, 1}:
        return "clear"
    return "mixed"


def _build_orientation_cue(day_type: str, high: float, low: float, precip_probability: int, wind_speed: float) -> str:
    if day_type == "stormy":
        return "A stormy afternoon may make transitions and end-of-day energy heavier than usual."
    if day_type == "rainy":
        return "Rain is likely today, so it helps to plan for wetter transitions and a more compressed rhythm."
    if day_type == "snowy":
        return "Snow or wintry conditions may slow movement and make transitions feel heavier than usual."
    if high >= 88:
        return "Heat may drain energy faster than usual, especially later in the day."
    if low <= 35:
        return "A colder day may make the morning start feel slower and more effortful."
    if wind_speed >= 25:
        return "Strong wind may make movement and transitions feel choppier than usual."
    if precip_probability >= 35:
        return "A wetter pattern may make the day's pacing less straightforward than usual."
    if precip_probability <= 10 and high >= 60 and low >= 45 and wind_speed < 20:
        return "The weather looks steady enough to support a more open, straightforward day."
    return "The weather looks mixed, so it is worth keeping your pacing a little flexible."


def _weather_should_surface(day_type: str, high: float, low: float, precip_probability: int, wind_speed: float) -> tuple[bool, str]:
    if day_type in {"stormy", "rainy", "snowy"}:
        return True, ""
    if high >= 88 or low <= 35:
        return True, ""
    if precip_probability >= 35:
        return True, ""
    if wind_speed >= 25:
        return True, ""
    return False, "mild_conditions"


def _resolved_location_matches_anchor(top: dict, anchor: str) -> bool:
    school_name = anchor.split(",")[0].strip().lower()
    resolved_name = str(top.get("name") or "").strip().lower()
    return bool(school_name and resolved_name and school_name in resolved_name)


def load_weather_context(
    user_data: dict,
    *,
    timeout_seconds: float = WEATHER_TIMEOUT_SECONDS,
) -> tuple[dict | None, dict, dict]:
    anchor = build_weather_anchor(user_data)
    if not anchor:
        return None, {}, {
            "available": False,
            "source": "",
            "unavailable_reason": "missing_location_context",
        }

    cached_anchor = str((user_data or {}).get("weather_geocoded_from") or "").strip()
    latitude = (user_data or {}).get("weather_latitude")
    longitude = (user_data or {}).get("weather_longitude")
    timezone_name = str((user_data or {}).get("weather_timezone") or "").strip()
    cache_updates: dict = {}
    source = ""

    if cached_anchor == anchor and latitude is not None and longitude is not None:
        source = "cached_coordinates"
    else:
        try:
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
        except Exception:
            return None, {}, {
                "available": False,
                "source": "",
                "unavailable_reason": "geocoding_failed",
            }
        results = geocode_payload.get("results") or []
        if not results:
            return None, {}, {
                "available": False,
                "source": "",
                "unavailable_reason": "geocoding_failed",
            }
        top = results[0]
        if not top.get("timezone") or not _resolved_location_matches_anchor(top, anchor):
            return None, {}, {
                "available": False,
                "source": "",
                "unavailable_reason": "geocoding_failed",
            }
        latitude = top.get("latitude")
        longitude = top.get("longitude")
        timezone_name = str(top.get("timezone") or "").strip()
        if latitude is None or longitude is None or not timezone_name:
            return None, {}, {
                "available": False,
                "source": "",
                "unavailable_reason": "geocoding_failed",
            }
        source = "geocoded_school_name"
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

    forecast_query = urllib.parse.urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone_name or "auto",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,weathercode,wind_speed_10m_max",
        "forecast_days": 1,
    })
    try:
        forecast_payload = _fetch_json(
            f"{OPEN_METEO_FORECAST_URL}?{forecast_query}",
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        return None, {}, {
            "available": False,
            "source": source,
            "unavailable_reason": "forecast_failed",
        }
    daily = forecast_payload.get("daily") or {}
    high = float((daily.get("temperature_2m_max") or [0])[0])
    low = float((daily.get("temperature_2m_min") or [0])[0])
    precip_probability = int((daily.get("precipitation_probability_max") or [0])[0])
    precip_sum = float((daily.get("precipitation_sum") or [0])[0])
    wind_speed = float((daily.get("wind_speed_10m_max") or [0])[0])
    weather_code = int((daily.get("weathercode") or [3])[0])
    day_type = _weather_code_to_day_type(weather_code)
    orientation_cue = _build_orientation_cue(day_type, high, low, precip_probability, wind_speed)
    should_surface, omission_reason = _weather_should_surface(day_type, high, low, precip_probability, wind_speed)

    context = {
        "day_type": day_type,
        "temperature_summary": f"High {round(high)}F, low {round(low)}F",
        "precipitation_summary": f"{precip_probability}% precipitation risk, {precip_sum:.1f} mm expected",
        "wind_summary": f"Peak wind {round(wind_speed)} mph",
        "orientation_cue": orientation_cue,
        "should_surface": should_surface,
        "omission_reason": "" if should_surface else omission_reason,
        "micro_cue_label": WEATHER_MICRO_CUE_LABEL,
        "micro_cue_text": f"{WEATHER_MICRO_CUE_LABEL}: {orientation_cue}",
    }
    diagnostics = {
        "available": True,
        "source": source,
        "unavailable_reason": "",
    }
    return context, cache_updates, diagnostics
