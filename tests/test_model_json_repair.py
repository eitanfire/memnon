import io
import json
import unittest
from unittest.mock import patch

from tests.test_daily_feed_slice import load_main_module


# The shape that failed in production on 2026-11-17 and 11-18: the model
# closed "segments" and dropped the document's final brace, which json.loads
# reports as "Expecting ',' delimiter: line 13 column 2".
MISSING_FINAL_BRACE = """{
  "title": "November 18 \\u2014 Protect the morning",
  "description": "Shaped by a hard Tuesday.",
  "time_anchor": "Wednesday morning",
  "continuity_anchor": "Last week's robotics class",
  "voices_used": [],
  "segments": {
    "opening": "Good morning. Yesterday's \\"lesson that fell apart\\" is still with you.",
    "calendar_today": "",
    "practical_briefing": "Start with the {one} thing.",
    "reflective_grounding": "You said: [keep it simple].",
    "meditative_close": "Breathe."
}"""


def _api_response(content, finish_reason="stop"):
    body = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]})
    response = io.BytesIO(body.encode())
    response.__enter__ = lambda self=response: self
    response.__exit__ = lambda *args: False
    return response


class ModelJsonRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()

    def test_production_failure_shape_is_unparseable_without_repair(self):
        with self.assertRaises(json.JSONDecodeError) as caught:
            json.loads(MISSING_FINAL_BRACE)
        self.assertIn("Expecting ',' delimiter", caught.exception.msg)
        self.assertEqual(caught.exception.lineno, 13)

    def test_missing_final_brace_is_repaired(self):
        result = self.main._parse_model_json(MISSING_FINAL_BRACE)
        self.assertEqual(result["segments"]["meditative_close"], "Breathe.")
        self.assertEqual(result["segments"]["practical_briefing"], "Start with the {one} thing.")

    def test_missing_closers_after_an_array_are_repaired_in_order(self):
        self.assertEqual(self.main._parse_model_json('{"a": [1, {"b": 2}'), {"a": [1, {"b": 2}]})

    def test_code_fences_are_still_stripped(self):
        self.assertEqual(self.main._parse_model_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_valid_json_is_untouched(self):
        self.assertEqual(self.main._parse_model_json('{"a": "}"}'), {"a": "}"})

    def test_broken_json_in_the_middle_still_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            self.main._parse_model_json('{"a": 1 "b": 2}')

    def test_truncated_inside_a_string_still_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            self.main._parse_model_json('{"a": "cut off mid')

    def test_mismatched_closer_is_not_repaired(self):
        with self.assertRaises(json.JSONDecodeError):
            self.main._parse_model_json('{"a": [1}')

    def test_summarize_retries_once_when_json_is_unparseable(self):
        responses = [_api_response('{"a": 1 "b": 2}'), _api_response('{"a": 1}')]
        with patch.object(self.main.urllib.request, "urlopen", side_effect=responses) as urlopen:
            self.assertEqual(self.main._summarize("prompt", "key", json_mode=True), {"a": 1})
        self.assertEqual(urlopen.call_count, 2)

    def test_summarize_repairs_without_a_second_call(self):
        with patch.object(self.main.urllib.request, "urlopen", side_effect=[_api_response(MISSING_FINAL_BRACE)]) as urlopen:
            result = self.main._summarize("prompt", "key", json_mode=True)
        self.assertEqual(result["title"], "November 18 — Protect the morning")
        self.assertEqual(urlopen.call_count, 1)

    def test_summarize_raises_when_the_retry_also_fails(self):
        responses = [_api_response('{"a": 1 "b": 2}'), _api_response('{"a": 1 "b": 2}')]
        with patch.object(self.main.urllib.request, "urlopen", side_effect=responses):
            with self.assertRaises(json.JSONDecodeError):
                self.main._summarize("prompt", "key", json_mode=True)

    def test_retry_log_never_contains_model_content(self):
        secret = "my student Ana had a meltdown"
        responses = [_api_response('{"a": "%s" "b": 2}' % secret), _api_response('{"a": 1}')]
        with patch.object(self.main.urllib.request, "urlopen", side_effect=responses), \
                patch("builtins.print") as printed:
            self.main._summarize("prompt", "key", json_mode=True)
        logged = " ".join(str(arg) for call in printed.call_args_list for arg in call.args)
        self.assertIn("retrying once", logged)
        self.assertNotIn("Ana", logged)


if __name__ == "__main__":
    unittest.main()
