"""Connector Phase 1: the remote MCP server at /api/mcp and its find_eap tool.

Requests go through the Flask app the Cloud Function serves, with no token,
the way Claude's custom connector calls it.
"""
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tests.test_daily_feed_slice import FUNCTIONS_DIR, load_main_module

ROOT = FUNCTIONS_DIR.parent


class ConnectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()
        cls.client = cls.main.flask_app.test_client()

    def rpc(self, method, params=None, request_id=1):
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        response = self.client.post(
            "/mcp", data=json.dumps(message),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        return response

    def find_eap(self, district):
        response = self.rpc("tools/call", {"name": "find_eap", "arguments": {"district": district}})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        return result["structuredContent"]

    # ── Protocol ─────────────────────────────────────────────────────────────

    def test_initialize_negotiates_the_protocol_version(self):
        body = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "claude", "version": "1"}}).get_json()
        self.assertEqual(body["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(body["result"]["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(body["result"]["serverInfo"]["name"], "memnon")
        unknown = self.rpc("initialize", {"protocolVersion": "1999-01-01"}).get_json()
        self.assertEqual(unknown["result"]["protocolVersion"], "2025-06-18")

    def test_notification_is_accepted_without_a_body(self):
        response = self.client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, b"")

    def test_get_is_not_allowed(self):
        response = self.client.get("/mcp")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "POST")

    def test_bad_json_and_unknown_method(self):
        response = self.client.post("/mcp", data=b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], -32700)
        self.assertEqual(self.rpc("resources/list").get_json()["error"]["code"], -32601)

    def test_every_tool_is_read_only(self):
        tools = self.rpc("tools/list").get_json()["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["find_eap"])
        for t in tools:
            self.assertTrue(t["annotations"]["readOnlyHint"])
            self.assertFalse(t["annotations"]["destructiveHint"])
        self.assertEqual(tools[0]["inputSchema"]["required"], ["district"])

    def test_bad_arguments_are_rejected(self):
        body = self.rpc("tools/call", {"name": "find_eap", "arguments": {"school": "x"}}).get_json()
        self.assertEqual(body["error"]["code"], -32602)
        body = self.rpc("tools/call", {"name": "delete_everything", "arguments": {}}).get_json()
        self.assertEqual(body["error"]["code"], -32602)

    # ── find_eap ─────────────────────────────────────────────────────────────

    def test_find_eap_works_with_no_token(self):
        result = self.find_eap("St. Vrain Valley")
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["district_name"], "St. Vrain Valley School District RE-1J")
        self.assertEqual(result["phone"], "855-699-6908 (24/7)")
        self.assertEqual(result["page_url"], "https://memnon.app/eap/st-vrain-valley")
        self.assertIn("988", result["crisis"])
        self.assertTrue(result["official_source_urls"])
        self.assertEqual(result["last_verified"], "2026-11-27")
        self.assertIn("per episode", result["session_basis_conflict"])

    def test_fuzzy_match_on_district_name_and_city(self):
        for query in ("st vrain", "SVVSD", "Longmont", "Saint Vrain Valley"):
            self.assertEqual(self.find_eap(query).get("district_name"), "St. Vrain Valley School District RE-1J", query)
        self.assertEqual(self.find_eap("Bolder Valley")["district_name"], "Boulder Valley School District RE-2")
        self.assertEqual(self.find_eap("Fort Collins")["district_name"], "Poudre School District R-1")
        both = self.find_eap("Thornton")
        self.assertEqual(both["status"], "several_matches")
        self.assertEqual({m["district_name"] for m in both["matches"]},
                         {"Adams 12 Five Star Schools", "Mapleton Public Schools"})

    def test_unknown_district_is_not_in_the_directory_yet(self):
        result = self.find_eap("Pueblo City Schools")
        self.assertEqual(result["status"], "not_found")
        self.assertIn("not in the directory yet", result["message"])
        self.assertIn("988", result["crisis"])
        self.assertNotIn("c_seap_note", result)

    def test_unknown_state_employer_gets_the_c_seap_note(self):
        result = self.find_eap("Colorado Department of Transportation")
        self.assertEqual(result["status"], "not_found")
        self.assertIn("C-SEAP (303-866-4314", result["c_seap_note"])
        self.assertIn("does not cover most K-12", result["c_seap_note"])

    def test_same_fields_as_the_web_page(self):
        low = self.find_eap("Denver Public Schools")
        self.assertEqual(low["answer"], "We could not confirm this district's EAP from a public source; ask HR.")
        self.assertNotIn("provider", low)
        page = (ROOT / "public" / "eap" / "westminster.html").read_text()
        result = self.find_eap("Westminster")
        self.assertIn(result["answer"].replace("'", "&#x27;"), page)
        self.assertNotRegex(json.dumps(result), r"(?i)password|username")
        self.assertIn("Login details are in your district's benefits guide", result["login_details"])

    def test_logs_tool_name_and_uid_only(self):
        out = StringIO()
        with redirect_stdout(out):
            self.find_eap("Aurora")
        self.assertEqual(out.getvalue().strip(), "[mcp] tool=find_eap uid=anonymous")

    def test_connector_data_matches_the_source_of_truth(self):
        self.assertEqual(
            (FUNCTIONS_DIR / "eap" / "colorado-districts.json").read_text(),
            (ROOT / "data" / "eap" / "colorado-districts.json").read_text(),
        )


class ConnectPageTests(unittest.TestCase):
    def test_connect_page_gives_the_steps_and_url(self):
        page = (ROOT / "public" / "connect.html").read_text()
        self.assertIn("<h1>Add Memnon to Claude</h1>", page)
        for step in ("Customize", "Connectors", "Add custom connector", "https://memnon.app/api/mcp"):
            self.assertIn(step, page)

    def test_eap_pages_link_the_connect_page_and_it_is_in_the_sitemap(self):
        self.assertIn('href="/connect"', (ROOT / "public" / "eap" / "st-vrain-valley.html").read_text())
        self.assertIn("<loc>https://memnon.app/connect</loc>", (ROOT / "public" / "sitemap.xml").read_text())


if __name__ == "__main__":
    unittest.main()
