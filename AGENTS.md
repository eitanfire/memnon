# Agent instructions for memnon

These apply to every coding agent working in this repo (Claude Code reads them through `CLAUDE.md`).

## Design gallery: freeze significant UI changes

`public/gallery/` keeps a write-once record of how Memnon's screens looked over time (see "Design Gallery" in `README.md`). When you change what a page in `public/` looks like in a significant way, the same change must add gallery versions. A design PR without them is incomplete.

### What counts as significant

Freeze when a change alters what a person sees, beyond a fix:

- a section, card, control, or state is added, removed, moved, or restructured
- the visual system shifts: color tokens, accent, type scale, spacing, layout grid
- a new page or a new user-facing state (a new brief state, an empty/error state)
- copy changes that reframe a screen (new headline, renamed core action)

Do not freeze for: typo or small copy fixes, bug fixes that restore intended look, refactors with no visible change, backend-only work. If you can't tell, ask the user in one line before the PR, not after.

### How

1. **Before editing**, run `python3 scripts/design_gallery_status.py --ref main`. If the project you're about to change shows `BEHIND`, freeze the current design first so there's a "before" to compare against:
   ```bash
   python3 scripts/freeze_design.py today.html --project today --ref main --label "Before <your change>" --query "mock=complete&brief=ready"
   ```
2. **After the change**, freeze the working tree with the same `--query` states that show the change (one version per state that changed, usually one or two):
   ```bash
   python3 scripts/freeze_design.py today.html --project today --label "<short name>" --query "mock=complete&brief=ready" --note "<what changed and why, one sentence>"
   ```
   The `--note` is the point of the record: say why, not just what.
3. Commit the new `public/gallery/` files in the same commit or PR as the design change, and mention the versions in the PR description.
4. For a page not in the gallery yet, pass `--project-name` and `--project-description` on the first freeze.

Useful `--query` states for Today: `mock=complete|grounded|practical` × `brief=off|setup|before|after|unknown|ready|error`.

### Never freeze

- `research.html` (founder dashboard with usage data and field notes) or any page showing real user, student, or interview data. The gallery is publicly reachable by link.
- `privacy.html` / `terms.html`: legal text, not design.

Never edit or delete an existing version folder; versions are write-once. Native iOS screens (the Action Button app) aren't covered by the freeze script.
