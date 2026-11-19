"""Report which gallery projects are behind the page they were frozen from.

For each project in public/gallery/manifest.js, looks at its newest version's
source page plus the local stylesheets that page links, and reports whether
any of those files changed between that version's commit and --ref
(default: the working tree, including uncommitted edits).

    python3 scripts/design_gallery_status.py            # vs working tree
    python3 scripts/design_gallery_status.py --ref main # vs main

Exit code is always 0; this is information for a person or agent deciding
whether a change was significant enough to freeze, not a gate.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC = REPO_ROOT / "public"
MANIFEST = PUBLIC / "gallery" / "manifest.js"


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=check)


def load_manifest() -> dict:
    text = MANIFEST.read_text(encoding="utf-8")
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def design_files(page: str) -> list[str]:
    """The page plus local stylesheets it links, as repo-relative paths."""
    files = [f"public/{page}"]
    source = PUBLIC / page
    if source.is_file():
        html = source.read_text(encoding="utf-8")
        for href in re.findall(r'<link\b[^>]*rel="stylesheet"[^>]*href="(/[^"?#]+)"', html):
            files.append(f"public{href}")
    return files


def changed_since(commit: str, files: list[str], ref: str | None) -> list[str]:
    target = [ref] if ref else []
    result = git("diff", "--name-only", commit, *target, "--", *files, check=False)
    if result.returncode != 0:
        return [f"(cannot diff from {commit})"]
    return [line for line in result.stdout.splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ref", help="Compare against this ref instead of the working tree")
    args = parser.parse_args()

    manifest = load_manifest()
    covered = set()
    for project in manifest["projects"]:
        versions = sorted(project["versions"], key=lambda v: (v["date"], v["id"]))
        if not versions:
            continue
        # The newest *state* of the page is what matters: any version frozen
        # from the latest commit covers it, whichever mock state it shows.
        newest_commit_version = max(
            versions, key=lambda v: git("log", "-1", "--format=%ct", v["commit"].split("+")[0], check=False).stdout.strip() or "0"
        )
        page = newest_commit_version["source"].split("?")[0].removeprefix("public/")
        covered.add(page)
        commit = newest_commit_version["commit"].split("+")[0]
        changed = changed_since(commit, design_files(page), args.ref)
        if changed:
            print(f"BEHIND  {project['id']}: newest version is from {commit}; changed since: {', '.join(changed)}")
        else:
            print(f"CURRENT {project['id']}: matches {commit}")

    pages = sorted(p.name for p in PUBLIC.glob("*.html"))
    missing = [p for p in pages if p not in covered]
    if missing:
        print(f"NOT IN GALLERY: {', '.join(missing)}")


if __name__ == "__main__":
    main()
