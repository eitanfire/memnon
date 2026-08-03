import http.client
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.workflows_static_server import create_workflows_static_server


class WorkflowsStaticServerTests(unittest.TestCase):
    def setUp(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()

        self.server = create_workflows_static_server(
            host="127.0.0.1",
            port=self.port,
            public_dir=REPO_ROOT / "public",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path: str):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_root_returns_index_html(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Memnon", body)

    def test_today_html_returns_ok(self):
        status, body = self.request("/today.html")
        self.assertEqual(status, 200)
        self.assertIn(b"Capture a thought", body)

    def test_today_route_rewrites_to_today_html(self):
        status, body = self.request("/today")
        self.assertEqual(status, 200)
        self.assertIn(b"Capture a thought", body)

    def test_today_result_route_rewrites_to_today_html(self):
        status, body = self.request("/today/result/example")
        self.assertEqual(status, 200)
        self.assertIn(b"Capture a thought", body)

    def test_today_saved_route_rewrites_to_today_html(self):
        status, body = self.request("/today/saved")
        self.assertEqual(status, 200)
        self.assertIn(b"Capture a thought", body)

    def test_retired_workflows_route_does_not_rewrite(self):
        status, _body = self.request("/workflows")
        self.assertEqual(status, 404)

    def test_missing_asset_returns_404(self):
        status, _body = self.request("/missing-file.js")
        self.assertEqual(status, 404)

    def test_runner_script_serves_today_route(self):
        port = self.port + 1000
        process = subprocess.Popen(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_workflows_static.py"),
            ],
            cwd=REPO_ROOT,
            env={**os.environ, "PORT": str(port)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.time() + 5
            while time.time() < deadline:
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=1)
                    self.fail(f"runner exited early\nstdout:\n{stdout}\nstderr:\n{stderr}")
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                    connection.request("GET", "/today")
                    response = connection.getresponse()
                    body = response.read()
                    connection.close()
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"Capture a thought", body)
                    return
                except OSError:
                    time.sleep(0.1)
            self.fail("runner did not start in time")
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
