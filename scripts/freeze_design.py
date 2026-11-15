"""Freeze a page from public/ into a write-once design version for the gallery.

Renders the page in headless Chrome (so mock/preview states built by JS are
captured), then writes a static copy:

    public/gallery/projects/<project>/<YYYY-MM-DD-slug>/index.html
    public/gallery/assets/<name>.<hash>.<ext>   (shared, write-once)

and appends the version to public/gallery/manifest.js.

What the frozen copy keeps and drops:
  - Local stylesheets are inlined.
  - Local images/fonts/icons the page references are copied into gallery/assets/
    under content-hashed names, so the version still renders after the live asset changes or is deleted.
  - All <script> tags are removed: a version is a picture of the design, not
    a running app (no Firebase, no /api calls).
  - Links to other site pages become inert so clicking around inside the
    gallery never lands on the live app.

Usage:
    python3 scripts/freeze_design.py today.html --project today \
        --label "Complete profile, brief ready" --query "mock=complete&brief=ready"
    python3 scripts/freeze_design.py research-snapshot.html --project research-snapshot \
        --label "First public snapshot"

Needs Google Chrome (or set CHROME=/path/to/chrome). No Python packages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC = REPO_ROOT / "public"
GALLERY = PUBLIC / "gallery"
MANIFEST = GALLERY / "manifest.js"
ASSET_STORE = GALLERY / "assets"
MANIFEST_PREFIX = "window.GALLERY = "

CHROME_CANDIDATES = [
    os.environ.get("CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]

ASSET_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif",
    ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".webm",
}


# ---------------------------------------------------------------- serving

class CleanUrlHandler(http.server.SimpleHTTPRequestHandler):
    """Serves public/ the way Firebase does: /today -> today.html."""

    def translate_path(self, path):
        local = Path(super().translate_path(path))
        if not local.exists() and local.with_suffix(".html").exists():
            return str(local.with_suffix(".html"))
        return str(local)

    def log_message(self, *args):  # keep the CLI quiet
        pass


def serve_public():
    handler = functools.partial(CleanUrlHandler, directory=str(PUBLIC))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------- render

def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("Could not find Chrome. Set CHROME=/path/to/chrome.")


def render(url: str, wait_ms: int) -> str:
    with tempfile.TemporaryDirectory() as profile:
        result = subprocess.run(
            [
                find_chrome(),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--window-size=1280,900",
                f"--virtual-time-budget={wait_ms}",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    if result.returncode != 0 or "<html" not in result.stdout.lower():
        sys.exit(f"Chrome failed to render {url}:\n{result.stderr[-2000:]}")
    return result.stdout


# ---------------------------------------------------------------- rewrite

def local_file(ref: str) -> Path | None:
    """Map a root-relative URL like /brand/x.svg?v=2 to a file in public/."""
    if not ref.startswith("/") or ref.startswith("//"):
        return None
    path = ref.split("#", 1)[0].split("?", 1)[0]
    candidate = (PUBLIC / path.lstrip("/")).resolve()
    if PUBLIC.resolve() not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def inline_stylesheets(html: str) -> str:
    def replace(match: re.Match) -> str:
        tag = match.group(0)
        href = re.search(r'href="([^"]+)"', tag)
        source = local_file(href.group(1)) if href else None
        if not source:
            return tag
        css = source.read_text(encoding="utf-8")
        return f"<style data-frozen-from=\"{href.group(1)}\">\n{css}\n</style>"

    return re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>', replace, html)


def strip_scripts(html: str) -> str:
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<script\b[^>]*/>", "", html, flags=re.I)
    html = re.sub(r'<link\b[^>]*rel="(manifest|modulepreload|preload)"[^>]*>', "", html)
    # Inline handlers would reference functions that no longer exist.
    return re.sub(r'\son[a-z]+="[^"]*"', "", html)


def copy_assets(html: str, out_dir: Path) -> str:
    copied: dict[str, str] = {}

    def relocate(ref: str) -> str | None:
        source = local_file(ref)
        if not source or source.suffix.lower() not in ASSET_EXTS:
            return None
        if ref not in copied:
            # Content-addressed and shared by every version: an image that
            # never changes is stored once, and a changed image gets a new
            # name, so old versions keep pointing at the bytes they had.
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:10]
            name = f"{source.stem}.{digest}{source.suffix.lower()}"
            dest = ASSET_STORE / name
            if not dest.exists():
                ASSET_STORE.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
            copied[ref] = os.path.relpath(dest, out_dir).replace(os.sep, "/")
        return copied[ref]

    def attr(match: re.Match) -> str:
        new = relocate(match.group(2))
        return f'{match.group(1)}="{new}"' if new else match.group(0)

    def css_url(match: re.Match) -> str:
        new = relocate(match.group(2))
        return f"url({match.group(1)}{new}{match.group(1)})" if new else match.group(0)

    html = re.sub(r'\b(src|href|poster)="(/[^"]*)"', attr, html)
    html = re.sub(r"""url\((["']?)(/[^)"']+)\1\)""", css_url, html)
    return html


def neutralize_links(html: str) -> str:
    """Page links (anything still root-relative after asset copying) go nowhere."""
    return re.sub(
        r'<a\b([^>]*?)\shref="(/[^"]*)"',
        lambda m: f'<a{m.group(1)} href="#" data-live-href="{m.group(2)}"',
        html,
    )


def add_head_meta(html: str, note: str) -> str:
    meta = (
        '\n<meta name="robots" content="noindex, nofollow" />'
        f"\n<!-- {note} -->\n"
    )
    return re.sub(r"<head\b[^>]*>", lambda m: m.group(0) + meta, html, count=1)


# ---------------------------------------------------------------- manifest

def load_manifest() -> dict:
    if not MANIFEST.exists():
        return {"projects": []}
    text = MANIFEST.read_text(encoding="utf-8")
    body = text[text.index("{"): text.rindex("}") + 1]
    return json.loads(body)


def save_manifest(data: dict) -> None:
    header = (
        "// Design gallery manifest. Hand-editable, but keep it valid JSON\n"
        "// (double quotes, no trailing commas): scripts/freeze_design.py\n"
        "// reads and rewrites it.\n"
    )
    MANIFEST.write_text(
        header + MANIFEST_PREFIX + json.dumps(data, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "version"


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("page", help="Page under public/, e.g. today.html")
    parser.add_argument("--project", required=True, help="Project id, e.g. today")
    parser.add_argument("--label", required=True, help="Short version name")
    parser.add_argument("--note", default="", help="What changed / why this version")
    parser.add_argument("--query", default="", help="Query string, e.g. mock=complete&brief=ready")
    parser.add_argument("--project-name", help="Display name when creating a new project")
    parser.add_argument("--project-description", default="", help="Description for a new project")
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--slug", help="Override the version folder slug")
    parser.add_argument("--wait-ms", type=int, default=8000, help="Virtual time for JS to settle")
    args = parser.parse_args()

    page = args.page.lstrip("/")
    if not (PUBLIC / page).is_file():
        sys.exit(f"No such page: public/{page}")

    version_id = f"{args.date}-{args.slug or slugify(args.label)}"
    out_dir = GALLERY / "projects" / args.project / version_id
    if out_dir.exists():
        sys.exit(f"{out_dir.relative_to(REPO_ROOT)} already exists. Versions are write-once; pick a new --label or --slug.")

    query = "preview=1" + (f"&{args.query}" if args.query else "")
    server = serve_public()
    try:
        url = f"http://127.0.0.1:{server.server_port}/{page}?{query}"
        print(f"Rendering {url}")
        html = render(url, args.wait_ms)
    finally:
        server.shutdown()

    out_dir.mkdir(parents=True)
    commit = git_commit()
    html = inline_stylesheets(html)
    html = strip_scripts(html)
    html = copy_assets(html, out_dir)
    html = neutralize_links(html)
    html = add_head_meta(
        html,
        f"Frozen from public/{page}?{query} at commit {commit or 'unknown'} on {args.date}. Do not edit.",
    )
    if not html.lstrip().lower().startswith("<!doctype"):
        html = "<!DOCTYPE html>\n" + html
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    manifest = load_manifest()
    project = next((p for p in manifest["projects"] if p["id"] == args.project), None)
    if project is None:
        project = {
            "id": args.project,
            "name": args.project_name or args.project.replace("-", " ").title(),
            "description": args.project_description,
            "versions": [],
        }
        manifest["projects"].append(project)
    project["versions"].append({
        "id": version_id,
        "date": args.date,
        "label": args.label,
        "note": args.note,
        "source": f"public/{page}" + (f"?{args.query}" if args.query else ""),
        "commit": commit,
        "path": f"projects/{args.project}/{version_id}/index.html",
    })
    save_manifest(manifest)
    print(f"Wrote {out_dir.relative_to(REPO_ROOT)}/index.html and updated manifest.js")


if __name__ == "__main__":
    main()
