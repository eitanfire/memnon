import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path("/Users/eitan/memnon")
FUNCTIONS_DIR = REPO_ROOT / "functions"
WEATHER_PATH = FUNCTIONS_DIR / "weather_context.py"


def load_weather_module():
    sys.path.insert(0, str(FUNCTIONS_DIR))
    spec = importlib.util.spec_from_file_location("memnon_weather_context", WEATHER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, payload: str):
        self.payload = payload.encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class WeatherContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.weather = load_weather_module()

    def test_build_weather_anchor_uses_school_name_and_state(self):
        anchor = self.weather.build_weather_anchor({
            "school_name": "Jefferson Academy",
            "school_state": "CO",
        })
        self.assertEqual(anchor, "Jefferson Academy, CO")

    def test_clear_weather_cache_fields_returns_expected_reset(self):
        self.assertEqual(
            self.weather.clear_weather_cache_fields(),
            {
                "weather_location_label": "",
                "weather_latitude": None,
                "weather_longitude": None,
                "weather_timezone": "",
                "weather_geocoded_from": "",
                "weather_location_updated_at": None,
            },
        )

    def test_load_weather_context_skips_without_anchor(self):
        context, updates = self.weather.load_weather_context({})
        self.assertIsNone(context)
        self.assertEqual(updates, {})

    def test_load_weather_context_uses_cached_coordinates(self):
        forecast_payload = '{"daily":{"temperature_2m_max":[78.0],"temperature_2m_min":[52.0],"precipitation_probability_max":[70],"precipitation_sum":[0.3],"weathercode":[95]}}'
        with patch.object(self.weather.urllib.request, "urlopen", return_value=_FakeResponse(forecast_payload)) as mocked:
            context, updates = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_state": "CO",
                "weather_geocoded_from": "Jefferson Academy, CO",
                "weather_latitude": 39.7392,
                "weather_longitude": -104.9903,
                "weather_timezone": "America/Denver",
            })
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(context["day_type"], "stormy")
        self.assertIn("storm", context["orientation_cue"].lower())
        self.assertEqual(updates, {})

    def test_load_weather_context_geocodes_and_caches_new_location(self):
        geocode_payload = '{"results":[{"name":"Jefferson Academy","latitude":39.7392,"longitude":-104.9903,"timezone":"America/Denver","admin1":"Colorado"}]}'
        forecast_payload = '{"daily":{"temperature_2m_max":[71.0],"temperature_2m_min":[49.0],"precipitation_probability_max":[5],"precipitation_sum":[0.0],"weathercode":[1]}}'
        with patch.object(
            self.weather.urllib.request,
            "urlopen",
            side_effect=[_FakeResponse(geocode_payload), _FakeResponse(forecast_payload)],
        ):
            context, updates = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_state": "CO",
            })
        self.assertEqual(context["day_type"], "clear")
        self.assertEqual(updates["weather_geocoded_from"], "Jefferson Academy, CO")
        self.assertEqual(updates["weather_timezone"], "America/Denver")

    def test_load_weather_context_skips_ambiguous_geocode(self):
        geocode_payload = '{"results":[{"name":"Wrong Place","latitude":40.0,"longitude":-105.0,"timezone":"","admin1":"Wyoming"}]}'
        with patch.object(self.weather.urllib.request, "urlopen", return_value=_FakeResponse(geocode_payload)):
            context, updates = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_state": "CO",
            })
        self.assertIsNone(context)
        self.assertEqual(updates, {})


if __name__ == "__main__":
    unittest.main()
