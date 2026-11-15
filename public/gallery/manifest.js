// Design gallery manifest. Hand-editable, but keep it valid JSON
// (double quotes, no trailing commas): scripts/freeze_design.py
// reads and rewrites it.
window.GALLERY = {
  "projects": [
    {
      "id": "today",
      "name": "Today",
      "description": "The signed-in daily page: capture, daily brief, standing context.",
      "versions": [
        {
          "id": "2026-11-14-complete-profile-brief-ready",
          "date": "2026-11-14",
          "label": "Complete profile, brief ready",
          "note": "",
          "source": "public/today.html?mock=complete&brief=ready",
          "commit": "28b83e6",
          "path": "projects/today/2026-11-14-complete-profile-brief-ready/index.html"
        },
        {
          "id": "2026-11-14-brief-setup-state",
          "date": "2026-11-14",
          "label": "Brief setup state",
          "note": "New user who hasn't turned on the daily brief yet.",
          "source": "public/today.html?mock=complete&brief=setup",
          "commit": "28b83e6",
          "path": "projects/today/2026-11-14-brief-setup-state/index.html"
        },
        {
          "id": "2026-11-14-grounded-profile-brief-before",
          "date": "2026-11-14",
          "label": "Grounded profile, brief before",
          "note": "Reflect lane, contemplative tradition, brief not generated yet.",
          "source": "public/today.html?mock=grounded&brief=before",
          "commit": "28b83e6",
          "path": "projects/today/2026-11-14-grounded-profile-brief-before/index.html"
        }
      ]
    },
    {
      "id": "research-snapshot",
      "name": "Research snapshot",
      "description": "Public write-up of discovery research with teachers and mentors.",
      "versions": [
        {
          "id": "2026-11-07-first-public-snapshot",
          "date": "2026-11-07",
          "label": "First public snapshot",
          "note": "Committed Nov 7 with social preview images.",
          "source": "public/research-snapshot.html",
          "commit": "28b83e6",
          "path": "projects/research-snapshot/2026-11-07-first-public-snapshot/index.html"
        }
      ]
    },
    {
      "id": "landing",
      "name": "Landing page",
      "description": "memnon.app home page.",
      "versions": [
        {
          "id": "2026-11-14-current-landing",
          "date": "2026-11-14",
          "label": "Current landing",
          "note": "",
          "source": "public/index.html",
          "commit": "28b83e6",
          "path": "projects/landing/2026-11-14-current-landing/index.html"
        }
      ]
    }
  ]
};
