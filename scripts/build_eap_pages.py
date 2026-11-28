#!/usr/bin/env python3
"""Render the Colorado district EAP directory into static pages.

Usage:
    python3 scripts/build_eap_pages.py

Reads data/eap/colorado-districts.json (the source of truth) and writes
public/eap/index.html, public/eap/<slug>.html for each district where
has_eap is not null, and public/sitemap.xml, then copies the data to
functions/eap/ for the connector. Standard library only.

Rules the pages keep:
- Low-confidence entries say "ask HR" and never guess.
- Company and group codes are shown; usernames and passwords never are,
  even when a district's guide prints them.
- Session-basis conflicts are stated plainly. Other conflicts stay in the
  data: `phone` already holds the number on the district's own EAP page.
"""
from __future__ import annotations

import html
import json
import shutil
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "eap" / "colorado-districts.json"
PUBLIC = ROOT / "public"
SITE = "https://memnon.app"
sys.path.insert(0, str(ROOT / "functions"))
from eap_directory import (  # noqa: E402
    ASK_HR,
    LOGIN_IN_GUIDE,
    REFERENCE_SLUGS,
    SECRET,
    DataError,
    confirmed,
    facts,
    guide_url,
    has_page,
    load,
    login,
    _sentence,
)
from eap_directory import DATA as FUNCTIONS_DATA  # noqa: E402

# Static pages listed in the sitemap alongside the generated ones.
STATIC_PATHS = ["/eap/guide", "/connect", "/privacy", "/terms"]

CRISIS = 'In crisis? Call or text <a href="tel:988">988</a> (Suicide &amp; Crisis Lifeline, 24/7).'
CONNECT_LINE = (
    'Memnon can carry what you learn in sessions into a short daily brief. '
    '<a href="/connect">Set it up</a>.'
)


def nice_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def source_label(url: str) -> str:
    parsed = urlparse(url)
    label = parsed.netloc.removeprefix("www.")
    return label + (" (PDF)" if parsed.path.lower().endswith(".pdf") else "")




def faq_jsonld(entry: dict) -> str:
    name = entry["district_name"]
    f = facts(entry)
    questions = [(f"Does {name} have an employee assistance program?", f["answer"])]
    if confirmed(entry):
        sessions = f["sessions"]
        if entry.get("session_basis_conflict"):
            sessions += " " + _sentence(f"Sources disagree: {entry['session_basis_conflict']}")
        questions.append((f"How many free EAP counseling sessions do {name} employees get?", sessions))
        questions.append((f"How do I contact the {name} EAP?", f["contact"]))
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in questions
        ],
    }
    # Escape "</" so the JSON cannot close the script element.
    return json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")


def _page(title: str, description: str, path: str, body: str, head_extra: str = "") -> str:
    e = html.escape
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{e(title)}</title>
  <meta name="description" content="{e(description)}" />
  <link rel="canonical" href="{SITE}{path}" />
  <link rel="stylesheet" href="/style.css" />
  <link rel="icon" type="image/x-icon" href="/favicon.ico" />
  <style>
    .crisis {{ background:#fff4e5; border:1px solid #f0c27b; border-radius:8px; padding:10px 14px; margin-bottom:24px; }}
    .answer {{ font-size:1.05rem; line-height:1.7; }}
    .eap-list {{ list-style:none; padding:0; }}
    .eap-list li {{ padding:10px 0; border-bottom:1px solid #eee; }}
    .quiet {{ color:#666; font-size:0.9rem; margin-top:32px; }}
  </style>
{head_extra}</head>
<body>
  <nav>
    <a href="/" class="nav-brand" style="text-decoration:none;"><img class="brand-mark" src="/nav-logo.png" alt="" /> Memnon</a>
  </nav>

  <main class="container" style="max-width:680px;">
    <p class="crisis">{CRISIS}</p>
{body}
  </main>
</body>
</html>
"""


def _sources_block(entry: dict) -> str:
    e = html.escape
    links = "\n".join(
        f'        <li><a href="{e(u)}" rel="noopener">{e(source_label(u))}</a></li>'
        for u in entry["official_source_urls"]
    )
    return f"""    <section class="card">
      <p>Last verified <time datetime="{e(entry['last_verified'])}">{nice_date(entry['last_verified'])}</time>. Official sources:</p>
      <ul>
{links}
      </ul>
    </section>"""


GUIDE_SECTION = """    <section class="card">
      <h2>Making the most of your sessions</h2>
      <ul>
        <li>Call the number, not just the website: the intake call is where you get matched.</li>
        <li>Ask how sessions are counted, per issue or per year.</li>
        <li>Write down what is hardest, what you have tried, and what "better" looks like by the last session.</li>
      </ul>
      <p><a href="/eap/guide">How to get the most from your EAP sessions</a></p>
    </section>"""


def render_district(entry: dict) -> str:
    e = html.escape
    name = entry["district_name"]
    f = facts(entry)
    parts = [f"    <h1>Does {e(name)} have an employee assistance program?</h1>",
             f'    <p class="answer">{e(f["answer"])}</p>']
    if confirmed(entry):
        if entry.get("who_is_covered"):
            parts.append(f"    <p>{e(_sentence('Who is covered: ' + entry['who_is_covered']))}</p>")
        if entry.get("session_basis_conflict"):
            parts.append(
                "    <p><strong>How sessions are counted is unclear.</strong> "
                f"{e(_sentence(entry['session_basis_conflict']))} Ask which applies when you call.</p>"
            )
        if login(entry)[1]:
            parts.append(f'    <p><a href="{e(guide_url(entry))}" rel="noopener">{LOGIN_IN_GUIDE}</a>.</p>')
        if entry.get("confidence") == "medium":
            parts.append("    <p>Some of this comes from a past-year or benefits-trust guide, so confirm it with HR.</p>")
        if entry.get("extra_benefits"):
            parts.append(f"    <p><strong>Also listed in the district's guide:</strong> {e(entry['extra_benefits'])}</p>")
    parts.append(_sources_block(entry))
    parts.append(GUIDE_SECTION)
    parts.append(f'    <p class="quiet">{CONNECT_LINE}</p>')
    parts.append('    <p class="muted"><a href="/eap">All Colorado districts</a></p>')

    description = (f"{name} employee assistance program: provider, free sessions, phone and login."
                   if confirmed(entry) else f"Does {name} have an employee assistance program?")
    jsonld = f'  <script type="application/ld+json">\n{faq_jsonld(entry)}\n  </script>\n'
    return _page(f"{name} EAP: employee assistance program", description,
                 f"/eap/{entry['slug']}", "\n".join(parts), jsonld)


def render_index(entries: list[dict]) -> str:
    e = html.escape
    items = []
    for entry in sorted((x for x in entries if x["slug"] not in REFERENCE_SLUGS), key=lambda x: x["district_name"]):
        if not has_page(entry):
            items.append(f"      <li>{e(entry['district_name'])} ({e(entry.get('city') or '')}): {ASK_HR}</li>")
            continue
        summary = e(entry["provider"]) if confirmed(entry) and entry.get("provider") else ASK_HR
        items.append(
            f'      <li><a href="/eap/{e(entry["slug"])}">{e(entry["district_name"])}</a> '
            f'({e(entry.get("city") or "")}): {summary}</li>'
        )
    reference = [x for x in entries if x["slug"] in REFERENCE_SLUGS]
    reference_html = "".join(
        f'\n    <p class="muted">{e(x["district_name"])}: {e(x.get("notes") or "")} '
        f'<a href="{e(x["official_source_urls"][0])}" rel="noopener">{e(source_label(x["official_source_urls"][0]))}</a></p>'
        for x in reference
    )
    latest = max(x["last_verified"] for x in entries)
    body = f"""    <h1>Colorado school district employee assistance programs</h1>
    <p class="answer">Most Colorado school districts give staff a few free, confidential counseling sessions through an employee assistance program (EAP). Find your district for the provider, the number of sessions, the phone number and how to log in.</p>
    <ul class="eap-list">
{chr(10).join(items)}
    </ul>{reference_html}
    <p class="muted">Every entry comes from the district's own pages or benefits guide. Last verified <time datetime="{latest}">{nice_date(latest)}</time>.</p>
{GUIDE_SECTION}
    <p class="quiet">{CONNECT_LINE}</p>"""
    return _page("Colorado school district EAPs: employee assistance programs",
                 "Employee assistance programs for Colorado school district staff: provider, free sessions, phone and login for each district.",
                 "/eap", body)


def render_sitemap(entries: list[dict]) -> str:
    paths = ["/", "/eap", *STATIC_PATHS] + [f"/eap/{x['slug']}" for x in entries if has_page(x)]
    lastmod = {f"/eap/{x['slug']}": x["last_verified"] for x in entries}
    lastmod["/eap"] = max(x["last_verified"] for x in entries)
    urls = "\n".join(
        f"  <url><loc>{SITE}{p}</loc>"
        + (f"<lastmod>{lastmod[p]}</lastmod>" if p in lastmod else "")
        + "</url>"
        for p in paths
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n'


def build(data_path: Path = DATA, public_dir: Path = PUBLIC) -> list[Path]:
    entries = load(data_path)
    out = public_dir / "eap"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for entry in entries:
        if has_page(entry):
            path = out / f"{entry['slug']}.html"
            path.write_text(render_district(entry))
            written.append(path)
    (out / "index.html").write_text(render_index(entries))
    (public_dir / "sitemap.xml").write_text(render_sitemap(entries))
    written += [out / "index.html", public_dir / "sitemap.xml"]
    for path in written:
        if SECRET.search(path.read_text()):
            raise DataError(f"{path.name}: a username or password reached the page")
    return written


def main() -> None:
    try:
        written = build()
    except DataError as exc:
        print(f"✗  {exc}", file=sys.stderr)
        sys.exit(1)
    FUNCTIONS_DATA.parent.mkdir(exist_ok=True)
    shutil.copyfile(DATA, FUNCTIONS_DATA)
    print(f"✓  wrote {len(written)} files to public/eap and public/sitemap.xml; copied data to functions/eap/")


if __name__ == "__main__":
    main()
