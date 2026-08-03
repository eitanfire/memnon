import http.client
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.public_static_server import create_public_static_server


class _ApiStubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, status: int, payload: dict, headers: dict | None = None):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/api/workflows/captures":
            self._send_json(405, {"error": "method not allowed"}, {"Allow": "POST"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/workflows/captures":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            self._send_json(201, {"ok": True, "body": body})
            return
        self._send_json(404, {"error": "not found"})

    def log_message(self, format, *args):
        return


def _reserve_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class PublicStaticServerTests(unittest.TestCase):
    def setUp(self):
        self.api_port = _reserve_port()
        self.api_server = ThreadingHTTPServer(("127.0.0.1", self.api_port), _ApiStubHandler)
        self.api_thread = threading.Thread(target=self.api_server.serve_forever, daemon=True)
        self.api_thread.start()

        self.public_port = _reserve_port()
        self.public_server = create_public_static_server(
            host="127.0.0.1",
            port=self.public_port,
            public_dir=REPO_ROOT / "public",
            api_origin=f"http://127.0.0.1:{self.api_port}",
        )
        self.public_thread = threading.Thread(target=self.public_server.serve_forever, daemon=True)
        self.public_thread.start()

    def tearDown(self):
        self.public_server.shutdown()
        self.public_server.server_close()
        self.public_thread.join(timeout=2)

        self.api_server.shutdown()
        self.api_server.server_close()
        self.api_thread.join(timeout=2)

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.public_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        response_headers = dict(response.getheaders())
        connection.close()
        return status, response_headers, payload

    def test_today_route_rewrites_to_today_html(self):
        status, _headers, body = self.request("GET", "/today")
        self.assertEqual(status, 200)
        self.assertIn(b"Memnon Today", body)
        self.assertIn(b"Open capture", body)

    def test_today_result_route_rewrites_to_today_html(self):
        status, _headers, body = self.request("GET", "/today/result/example")
        self.assertEqual(status, 200)
        self.assertIn(b"Capture a thought", body)

    def test_retired_dashboard_route_does_not_rewrite(self):
        status, _headers, _body = self.request("GET", "/dashboard")
        self.assertEqual(status, 404)

    def test_retired_workflows_route_does_not_rewrite(self):
        status, _headers, _body = self.request("GET", "/workflows")
        self.assertEqual(status, 404)

    def test_api_get_is_proxied_instead_of_dropping_connection(self):
        status, headers, body = self.request("GET", "/api/workflows/captures")
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "POST")
        self.assertEqual(json.loads(body.decode("utf-8")), {"error": "method not allowed"})

    def test_api_post_is_proxied_to_upstream(self):
        status, headers, body = self.request(
            "POST",
            "/api/workflows/captures",
            body=b'{"source_text":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertEqual(
            json.loads(body.decode("utf-8")),
            {"ok": True, "body": '{"source_text":"hello"}'},
        )


if __name__ == "__main__":
    unittest.main()
