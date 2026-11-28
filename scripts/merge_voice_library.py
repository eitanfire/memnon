#!/usr/bin/env python3
"""Merge the staged voice library into data/sources.json.

Usage:
    python3 scripts/merge_voice_library.py ~/Desktop/Memnon\\ Product/seeds/library
    ./sync-sources.sh

Each staged file (schema in the library README) replaces its guide's
metadata and adds its entries as passages. Existing passages in a staged
guide must each be accounted for: removed by the audit, or replaced by the
staged entry that duplicates them. A replaced id is kept in the new
passage's `same_as`, so a voice spoken under the old id still counts for
rotation. The script fails rather than guess.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "data" / "sources.json"

# Rerunning the merge is safe: a staged id already in data/ keeps its
# same_as.
# Old id -> staged id that carries the same teaching. Every fix_ref and
# fix_text verdict in the audits names one of these staged entries, which
# already has the corrected ref and wording.
REPLACED_BY = {
    "pa_1_14": "pa_t08",
    "pa_2_4": "pa_t12",
    "pa_2_21": "pa_t21",
    "pa_4_1": "pa_t24",
    "mai_1": "mai_t16",
    "mb_1": "mb_t01",
    "mb_2": "mb_t02",
    "pp_1": "pp_t01",
    "pp_2": "pp_t02",
    "pf_1": "pf_t21",
    "pf_2": "pf_t03",
    "jd_2": "jd_t05",
    "bh_1": "bh_t01",
    "bh_2": "bh_t24",
}

# Different sources, same teaching: rotation treats each pair as one.
# The Rambam restates Avot 4:18 in Hilchot De'ot 5:7.
SAME_TEACHING = [("mai_t10", "pa_t29")]

GUIDE_FIELDS = ("label", "spoken_name", "tradition", "work", "dates", "public_domain", "bio")
PASSAGE_FIELDS = ("id", "ref", "kind", "text", "verbatim", "tags", "verified_url", "note")
KINDS = {"teaching", "story", "saying"}


def fail(message: str) -> None:
    print(f"✗  {message}", file=sys.stderr)
    sys.exit(1)


def staged_passage(entry: dict) -> dict:
    passage = {field: entry[field] for field in PASSAGE_FIELDS if field in entry}
    passage["tags"] = [tag.strip() for tag in entry.get("tags", [])]
    passage.setdefault("verbatim", "")
    return passage


def merge(sources: dict, staged_files: list[dict]) -> dict:
    vocabulary = set(sources.get("tag_vocabulary") or [])
    if not vocabulary:
        fail("data/sources.json has no tag_vocabulary")
    guides = sources["guides"]
    traditions = sources["traditions"]
    removed = []

    for staged in staged_files:
        guide_id = staged["guide_id"]
        old = guides.get(guide_id, {})
        verdicts = {a["id"]: a["verdict"] for a in staged.get("audit_existing", [])}
        passages = [staged_passage(e) for e in staged["entries"]]
        by_id = {p["id"]: p for p in passages}

        for p in passages:
            if p.get("kind") not in KINDS:
                fail(f"{p['id']}: kind {p.get('kind')!r}")
            if not p.get("text") or not p.get("ref") or not p.get("verified_url"):
                fail(f"{p['id']}: text, ref and verified_url are required")
            unknown = set(p["tags"]) - vocabulary
            if unknown:
                fail(f"{p['id']}: tags not in vocabulary: {sorted(unknown)}")

        for p in old.get("passages", []):
            old_id = p["id"]
            if old_id in by_id:
                for same in p.get("same_as", []):
                    if same not in by_id[old_id].setdefault("same_as", []):
                        by_id[old_id]["same_as"].append(same)
            elif verdicts.get(old_id) == "remove":
                removed.append(old_id)
            elif old_id in REPLACED_BY and REPLACED_BY[old_id] in by_id:
                by_id[REPLACED_BY[old_id]].setdefault("same_as", []).append(old_id)
            else:
                fail(f"{guide_id}: existing passage {old_id} is neither removed nor replaced")

        guide = {field: staged[field] for field in GUIDE_FIELDS if field in staged}
        guide["passages"] = passages
        guides[guide_id] = guide

        tradition = traditions.get(staged["tradition"])
        if not tradition:
            fail(f"{guide_id}: unknown tradition {staged['tradition']!r}")
        if guide_id not in tradition["guides"]:
            tradition["guides"].append(guide_id)

    every = {p["id"]: p for g in guides.values() for p in g.get("passages", [])}
    for a, b in SAME_TEACHING:
        if a not in every or b not in every:
            fail(f"same_as pair {a}/{b} not found")
        for one, other in ((a, b), (b, a)):
            if other not in every[one].setdefault("same_as", []):
                every[one]["same_as"].append(other)

    ids = [p["id"] for g in guides.values() for p in g.get("passages", [])]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        fail(f"passage ids repeat: {sorted(duplicates)}")

    print(f"✓  merged {len(staged_files)} guides; {len(ids)} passages; removed {removed or 'none'}")
    return sources


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("library", type=Path, help="folder of staged <guide_id>.json files")
    args = parser.parse_args()

    staged = [json.loads(p.read_text()) for p in sorted(args.library.glob("*.json"))]
    if not staged:
        fail(f"no staged files in {args.library}")
    sources = merge(json.loads(SOURCES.read_text()), staged)
    SOURCES.write_text(json.dumps(sources, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
