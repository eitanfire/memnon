from __future__ import annotations

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from src.workflows_static_server import rewrite_workflows_path


def rewrite_public_path(raw_path: str) -> str:
    rewritten = rewrite_workflows_path(raw_path)
    if rewritten != raw_path:
        return rewritten

    parsed = urlsplit(raw_path)
    path = parsed.path or "/"

    static_routes = {
        "/research": "/research.html",
        "/setup": "/setup.html",
        "/privacy": "/privacy.html",
        "/terms": "/terms.html",
    }
    destination = static_routes.get(path.rstrip("/") or "/")
    if not destination:
        return raw_path

    return urlunsplit(
        SplitResult(
            scheme="",
            netloc="",
            path=destination,
            query=parsed.query,
            fragment="",
        )
    )


class PublicStaticHandler(SimpleHTTPRequestHandler):
    api_origin: str | None = None

    def _proxy_api_request(self) -> bool:
        if not self.api_origin:
            return False

        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/api/"):
            return False

        upstream_url = urljoin(self.api_origin.rstrip("/") + "/", parsed.path.lstrip("/"))
        if parsed.query:
            upstream_url = f"{upstream_url}?{parsed.query}"

        body = b""
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length:
            body = self.rfile.read(content_length)

        headers = {}
        for key, value in self.headers.items():
            if key.lower() in {"host", "connection", "content-length"}:
                continue
            headers[key] = value
        if body:
            headers["Content-Length"] = str(len(body))

        request = Request(
            upstream_url,
            data=body if self.command not in {"GET", "HEAD"} else None,
            headers=headers,
            method=self.command,
        )

        try:
            with urlopen(request, timeout=30) as response:
                self._relay_upstream_response(
                    status=response.status,
                    headers=response.headers.items(),
                    body=response.read() if self.command != "HEAD" else b"",
                )
        except HTTPError as exc:
            self._relay_upstream_response(
                status=exc.code,
                headers=exc.headers.items(),
                body=exc.read() if self.command != "HEAD" else b"",
            )
        except URLError:
            payload = json.dumps({"error": "Local API upstream unavailable."}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        return True

    def _relay_upstream_response(self, *, status: int, headers, body: bytes):
        self.send_response(status)
        excluded_headers = {
            "connection",
            "content-length",
            "date",
            "server",
            "transfer-encoding",
            "cache-control",
        }
        for key, value in headers:
            if key.lower() in excluded_headers:
                continue
            self.send_header(key, value)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    NO_CACHE_EXTENSIONS = (".html", ".js", ".css")

    def end_headers(self):
        path_only = self.path.split("?", 1)[0]
        if path_only.endswith(self.NO_CACHE_EXTENSIONS) or path_only in ("", "/"):
            self.send_header("Cache-Control", "no-cache, max-age=0, must-revalidate")
        super().end_headers()

    def _serve_static(self, method: str):
        if self._proxy_api_request():
            return

        original_path = self.path
        self.path = rewrite_public_path(self.path)
        try:
            super_method = getattr(super(), method)
            super_method()
        finally:
            self.path = original_path

    def do_GET(self):
        self._serve_static("do_GET")

    def do_HEAD(self):
        self._serve_static("do_HEAD")

    def do_POST(self):
        if self._proxy_api_request():
            return
        self.send_error(404, "File not found")

    def do_OPTIONS(self):
        if self._proxy_api_request():
            return
        self.send_error(404, "File not found")


def create_public_static_server(
    host: str,
    port: int,
    public_dir: Path | str,
    api_origin: str | None = None,
):
    directory = str(Path(public_dir).resolve())
    handler_class = type(
        "ConfiguredPublicStaticHandler",
        (PublicStaticHandler,),
        {"api_origin": api_origin},
    )
    handler = partial(handler_class, directory=directory)
    return ThreadingHTTPServer((host, port), handler)
