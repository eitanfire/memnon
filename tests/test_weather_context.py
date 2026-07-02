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

    def test_build_weather_anchor_falls_back_to_school_city_and_state(self):
        anchor = self.weather.build_weather_anchor({
            "school_city": "BROOMFIELD",
            "school_state": "CO",
        })
        self.assertEqual(anchor, "BROOMFIELD")

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
        context, updates, diagnostics = self.weather.load_weather_context({})
        self.assertIsNone(context)
        self.assertEqual(updates, {})
        self.assertEqual(diagnostics["available"], False)
        self.assertEqual(diagnostics["unavailable_reason"], "missing_location_context")

    def test_load_weather_context_uses_cached_coordinates(self):
        forecast_payload = '{"daily":{"temperature_2m_max":[78.0],"temperature_2m_min":[52.0],"precipitation_probability_max":[70],"precipitation_sum":[0.3],"weathercode":[95]}}'
        with patch.object(self.weather.urllib.request, "urlopen", return_value=_FakeResponse(forecast_payload)) as mocked:
            context, updates, diagnostics = self.weather.load_weather_context({
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
        self.assertEqual(diagnostics["available"], True)
        self.assertEqual(diagnostics["source"], "cached_coordinates")

    def test_load_weather_context_geocodes_and_caches_new_location(self):
        geocode_payload = '{"results":[{"name":"Jefferson Academy","latitude":39.7392,"longitude":-104.9903,"timezone":"America/Denver","admin1":"Colorado"}]}'
        forecast_payload = '{"daily":{"temperature_2m_max":[71.0],"temperature_2m_min":[49.0],"precipitation_probability_max":[5],"precipitation_sum":[0.0],"weathercode":[1]}}'
        with patch.object(
            self.weather.urllib.request,
            "urlopen",
            side_effect=[_FakeResponse(geocode_payload), _FakeResponse(forecast_payload)],
        ):
            context, updates, diagnostics = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_state": "CO",
            })
        self.assertEqual(context["day_type"], "clear")
        self.assertEqual(updates["weather_geocoded_from"], "Jefferson Academy, CO")
        self.assertEqual(updates["weather_timezone"], "America/Denver")
        self.assertEqual(diagnostics["available"], True)
        self.assertEqual(diagnostics["source"], "geocoded_school_name")

    def test_load_weather_context_skips_ambiguous_geocode(self):
        geocode_payload = '{"results":[{"name":"Wrong Place","latitude":40.0,"longitude":-105.0,"timezone":"","admin1":"Wyoming"}]}'
        with patch.object(self.weather.urllib.request, "urlopen", return_value=_FakeResponse(geocode_payload)):
            context, updates, diagnostics = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_state": "CO",
            })
        self.assertIsNone(context)
        self.assertEqual(updates, {})
        self.assertEqual(diagnostics["available"], False)
        self.assertEqual(diagnostics["unavailable_reason"], "geocoding_failed")

    def test_load_weather_context_falls_back_to_school_city_when_school_name_does_not_geocode(self):
        empty_geocode_payload = '{"generationtime_ms":0.4}'
        city_geocode_payload = '{"results":[{"name":"Broomfield","latitude":39.92054,"longitude":-105.08665,"timezone":"America/Denver","admin1":"Colorado","country_code":"US"}]}'
        forecast_payload = '{"daily":{"temperature_2m_max":[82.0],"temperature_2m_min":[55.0],"precipitation_probability_max":[20],"precipitation_sum":[0.1],"weathercode":[3]}}'
        with patch.object(
            self.weather.urllib.request,
            "urlopen",
            side_effect=[
                _FakeResponse(empty_geocode_payload),
                _FakeResponse(city_geocode_payload),
                _FakeResponse(forecast_payload),
            ],
        ):
            context, updates, diagnostics = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_city": "BROOMFIELD",
                "school_state": "CO",
            })
        self.assertIsNotNone(context)
        self.assertEqual(updates["weather_geocoded_from"], "BROOMFIELD")
        self.assertEqual(updates["weather_location_label"], "Broomfield Colorado")
        self.assertEqual(diagnostics["available"], True)
        self.assertEqual(diagnostics["source"], "geocoded_school_city")

    def test_load_weather_context_marks_mild_day_as_available_but_omitted(self):
        forecast_payload = '{"daily":{"temperature_2m_max":[71.0],"temperature_2m_min":[52.0],"precipitation_probability_max":[5],"precipitation_sum":[0.0],"weathercode":[1],"wind_speed_10m_max":[9.0]}}'
        with patch.object(self.weather.urllib.request, "urlopen", return_value=_FakeResponse(forecast_payload)):
            context, updates, diagnostics = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_state": "CO",
                "weather_geocoded_from": "Jefferson Academy, CO",
                "weather_latitude": 39.7392,
                "weather_longitude": -104.9903,
                "weather_timezone": "America/Denver",
            })
        self.assertEqual(diagnostics["available"], True)
        self.assertEqual(context["should_surface"], False)
        self.assertEqual(context["omission_reason"], "mild_conditions")
        self.assertTrue(context["micro_cue_text"].startswith("Outside context:"))

    def test_load_weather_context_marks_storm_day_as_speakable(self):
        forecast_payload = '{"daily":{"temperature_2m_max":[78.0],"temperature_2m_min":[52.0],"precipitation_probability_max":[70],"precipitation_sum":[0.3],"weathercode":[95],"wind_speed_10m_max":[18.0]}}'
        with patch.object(self.weather.urllib.request, "urlopen", return_value=_FakeResponse(forecast_payload)):
            context, updates, diagnostics = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_state": "CO",
                "weather_geocoded_from": "Jefferson Academy, CO",
                "weather_latitude": 39.7392,
                "weather_longitude": -104.9903,
                "weather_timezone": "America/Denver",
            })
        self.assertEqual(diagnostics["available"], True)
        self.assertEqual(context["should_surface"], True)
        self.assertEqual(context["micro_cue_label"], "Outside context")
        self.assertIn("stormy", context["micro_cue_text"].lower())

    def test_load_weather_context_marks_windy_day_as_speakable(self):
        forecast_payload = '{"daily":{"temperature_2m_max":[72.0],"temperature_2m_min":[47.0],"precipitation_probability_max":[10],"precipitation_sum":[0.0],"weathercode":[1],"wind_speed_10m_max":[29.0]}}'
        with patch.object(self.weather.urllib.request, "urlopen", return_value=_FakeResponse(forecast_payload)):
            context, updates, diagnostics = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_state": "CO",
                "weather_geocoded_from": "Jefferson Academy, CO",
                "weather_latitude": 39.7392,
                "weather_longitude": -104.9903,
                "weather_timezone": "America/Denver",
            })
        self.assertEqual(diagnostics["available"], True)
        self.assertEqual(context["should_surface"], True)
        self.assertIn("wind", context["micro_cue_text"].lower())

    def test_load_weather_context_reports_forecast_failure(self):
        with patch.object(self.weather.urllib.request, "urlopen", side_effect=RuntimeError("timeout")):
            context, updates, diagnostics = self.weather.load_weather_context({
                "school_name": "Jefferson Academy",
                "school_state": "CO",
                "weather_geocoded_from": "Jefferson Academy, CO",
                "weather_latitude": 39.7392,
                "weather_longitude": -104.9903,
                "weather_timezone": "America/Denver",
            })
        self.assertIsNone(context)
        self.assertEqual(updates, {})
        self.assertEqual(diagnostics["available"], False)
        self.assertEqual(diagnostics["unavailable_reason"], "forecast_failed")


if __name__ == "__main__":
    unittest.main()
