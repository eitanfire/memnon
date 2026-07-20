from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit


WORKFLOWS_ENTRYPOINT = "/workflows.html"


def rewrite_workflows_path(raw_path: str) -> str:
    parsed = urlsplit(raw_path)
    path = parsed.path or "/"

    if path in {"/workflows", "/workflows/", "/workflows/saved"} or path.startswith("/workflows/result/"):
        rewritten = SplitResult(
            scheme="",
            netloc="",
            path=WORKFLOWS_ENTRYPOINT,
            query=parsed.query,
            fragment="",
        )
        return urlunsplit(rewritten)

    return raw_path


class WorkflowsStaticHandler(SimpleHTTPRequestHandler):
    NO_CACHE_EXTENSIONS = (".html", ".js", ".css")

    def end_headers(self):
        path_only = self.path.split("?", 1)[0]
        if path_only.endswith(self.NO_CACHE_EXTENSIONS) or path_only in ("", "/"):
            self.send_header("Cache-Control", "no-cache, max-age=0, must-revalidate")
        super().end_headers()

    def do_GET(self):
        original_path = self.path
        self.path = rewrite_workflows_path(self.path)
        try:
            super().do_GET()
        finally:
            self.path = original_path

    def do_HEAD(self):
        original_path = self.path
        self.path = rewrite_workflows_path(self.path)
        try:
            super().do_HEAD()
        finally:
            self.path = original_path


def create_workflows_static_server(host: str, port: int, public_dir: Path | str):
    directory = str(Path(public_dir).resolve())
    handler = partial(WorkflowsStaticHandler, directory=directory)
    return ThreadingHTTPServer((host, port), handler)
