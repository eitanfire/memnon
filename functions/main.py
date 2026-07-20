"""
Memnon SaaS — Firebase Cloud Functions.

Two functions:
  api      HTTP function — Google Drive OAuth flow + user setup + data endpoints
  worker   Scheduled function — polls every user's Drive inbox every minute

Environment variables (set via Firebase Secret Manager):
  OPENAI_API_KEY          Your OpenAI key — used for Whisper + GPT-4o-mini
  GOOGLE_CLIENT_SECRETS   Contents of the OAuth client secrets JSON from Google Cloud Console
  FLASK_SECRET            Any random string for Flask session signing

Set secrets:
  firebase functions:secrets:set OPENAI_API_KEY
  firebase functions:secrets:set GOOGLE_CLIENT_SECRETS
  firebase functions:secrets:set FLASK_SECRET

OAuth redirect URI to register in Google Cloud Console:
  https://api-4hth6oktaa-uc.a.run.app/auth/callback
"""

import base64
from collections import Counter
import hashlib
import io
import json
import os
import re
import secrets
import tempfile
import traceback
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape as xml_escape

import firebase_admin
from firebase_admin import auth as fb_auth
from firebase_admin import firestore
from firebase_admin import storage
from firebase_functions import https_fn, options, scheduler_fn
from flask import Flask, jsonify, redirect, request
from flask_cors import CORS
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

from audio_generation import synthesize_daily_brief_bytes, synthesize_reflection_bytes, synthesize_reflection_mp3
from hf_inference import EMBEDDING_MODEL, EMBEDDING_PROVIDER, embed_text, embed_text_details, embedding_runtime_status, rerank_candidates
from lanes import extract_themes, professional_prompt, reflect_prompt, teaching_practical_prompt
from weather_context import clear_weather_cache_fields, load_weather_context
from workflows.ai import (
    generate_professional_analysis,
    generate_professional_note,
    generate_social_post,
    load_openai_api_key,
    transcribe_audio_bytes,
)
from workflows.blueprint import create_workflows_blueprint
from workflows.continuity_bridge import (
    DAILY_FEED_NOTES_COLLECTION,
    write_firestore_continuity_note,
)
from workflows.repository import FirestoreWorkflowRepository
from workflows.service import WorkflowService

# ── lazy init — do NOT call at module level (hangs CLI analysis) ──────────────

_firebase_app = None
_firestore_client = None
DEFAULT_STORAGE_BUCKET = os.environ.get(
    "FIREBASE_STORAGE_BUCKET",
    "gcf-v2-uploads-714155490867.us-central1.cloudfunctions.appspot.com",
)


def _get_db():
    global _firebase_app, _firestore_client
    if _firebase_app is None:
        _firebase_app = firebase_admin.initialize_app(options={
            "storageBucket": DEFAULT_STORAGE_BUCKET
        })
    if _firestore_client is None:
        _firestore_client = firestore.client()
    return _firestore_client


flask_app = Flask(__name__)
flask_app.secret_key = os.environ.get("FLASK_SECRET", "dev-change-me")
CORS(flask_app, origins=[
    "https://memnon.app",
    "https://memnon-app.web.app",
    "https://memnon-app.firebaseapp.com",
    "http://localhost:5000",
    "http://localhost:5050",
    "http://localhost:8000",
    "http://localhost:8080",
])


def _workflow_service():
    return WorkflowService(
        repository=FirestoreWorkflowRepository(_get_db()),
        note_generator=generate_professional_note,
        now_provider=lambda: datetime.now(timezone.utc).isoformat(),
        api_key_provider=load_openai_api_key,
        continuity_bridge_writer=lambda **payload: write_firestore_continuity_note(
            db=_get_db(),
            **payload,
        ),
        social_post_generator=generate_social_post,
        professional_analysis_generator=generate_professional_analysis,
    )


def _workflow_capture_note_label(record) -> str:
    result = dict(getattr(record, "result", {}) or {})
    artifact = dict(
        result.get("primary_artifact")
        or result.get("saved_note_artifact")
        or {}
    )
    return (artifact.get("title") or "Saved result").strip() or "Saved result"


def _workflow_capture_compat_response(record):
    return jsonify({
        "ok": True,
        "capture_id": record.capture_id,
        "next_route": f"/workflows/result/{record.capture_id}",
        "note": _workflow_capture_note_label(record),
        "reflection_audio": None,
    })


# ── constants ─────────────────────────────────────────────────────────────────

BASE_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",  # access only files created by this app
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]
TASKS_READONLY_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"
API_BASE = "https://api-4hth6oktaa-uc.a.run.app"
REDIRECT_URI = f"{API_BASE}/auth/callback"
FRONTEND_URL = "https://memnon.app"
ALLOWED_FRONTEND_ORIGINS = {
    "https://memnon.app",
    "https://memnon-app.web.app",
    "https://memnon-app.firebaseapp.com",
    "http://localhost:5000",
    "http://localhost:5050",
    "http://localhost:8000",
    "http://localhost:8080",
}

AUDIO_MIME_TYPES = {
    "audio/mp4", "audio/x-m4a", "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/x-wav", "audio/aac", "audio/flac",
    "audio/webm", "audio/ogg", "video/mp4",
}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".mp4", ".webm", ".ogg"}
IMAGE_MIME_PREFIX = "image/"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_NARRATION_VOICE = "en-IE-EmilyNeural"
VOICE_PREVIEW_TEXT = "Did Nature, creator of all,\ngive perception and voice to stone?"
NARRATION_VOICES = {
    "en-IE-EmilyNeural": {"label": "Emily", "descriptor": "warm"},
    "en-US-JennyNeural": {"label": "Jenny", "descriptor": "clear"},
    "en-GB-RyanNeural": {"label": "Ryan", "descriptor": "measured"},
}
GOOGLE_TASKS_WEB_URL = "https://tasks.google.com/tasks/"
HUGGING_FACE_API_KEY = os.environ.get("HUGGING_FACE_API_KEY", "")
DEFAULT_DAILY_FEED_TIMEZONE = "America/Denver"
DEFAULT_DAILY_FEED_PUBLISH_HOUR = 4
DEFAULT_DAILY_FEED_RECENT_NOTE_LIMIT = 4
DEFAULT_DAILY_FEED_STANDARD_LOOKBACK_DAYS = 7
DAILY_FEED_AUDIO_PREFIX = "daily-feed"
DAILY_FEED_ROUTE_BASE = f"{API_BASE}/feed"
FOUNDER_METRICS_EMAILS = {
    email.strip().lower()
    for email in (os.environ.get("FOUNDER_METRICS_EMAILS", "eitanfire@gmail.com")).split(",")
    if email.strip()
}
INNEDCO_RESEARCH = {
    "saved_dates": {
        "recording_ledger": "2026-06-18",
        "conference_user_stories": "2026-06-18",
        "speaker_notes": "2026-06-16",
    },
    "recording_ledger": {
        "summary": {
            "total_recordings": 17,
            "already_processed": 13,
            "needs_transcription": 0,
            "needs_review": 2,
            "skip": 1,
        },
        "priority_items": [
            {
                "file": "Beaver Run Resort & Conference Center 9.m4a",
                "duration": "36:47",
                "status": "probable duplicate",
                "type": "session talk",
                "action": "already captured",
                "people": "Confirmed as another capture of the AI/citizenship session family already represented by Beaver Run Resort & Conference Center 4.m4a.",
            },
            {
                "file": "New Recording 27.m4a",
                "duration": "25:14",
                "status": "probable duplicate",
                "type": "session talk",
                "action": "already captured",
                "people": "Confirmed as another capture of the Alexandra Seabourn new-teacher-support session already represented by New Recording 24.m4a and New Recording 25.m4a.",
            },
        ],
    },
    "recording_coverage": {
        "summary": {
            "featured_in_research_view": 6,
            "tracked_background_or_session_audio": 11,
            "still_needs_follow_up": 0,
        },
        "items": [
            {
                "file": "Los Pinos Condos.m4a",
                "duration": "47:02",
                "source_status": "probable duplicate",
                "research_visibility": "tracked background",
                "notes": "Probable duplicate of Los Pinos Condos 2.m4a; keep the better copy for provenance, but do not treat it as separate evidence.",
            },
            {
                "file": "Los Pinos Condos 2.m4a",
                "duration": "47:02",
                "source_status": "already processed",
                "research_visibility": "featured in research view",
                "notes": "Now represented through the Jaylen McGrew user story and manually re-transcribed to verify the Memnon workflow signal.",
            },
            {
                "file": "508 Kings Crown Rd.m4a",
                "duration": "01:50",
                "source_status": "already processed",
                "research_visibility": "tracked background",
                "notes": "Marked already captured in the ledger, but not yet surfaced as its own conference story card.",
            },
            {
                "file": "508 Kings Crown Rd 2.m4a",
                "duration": "05:31",
                "source_status": "already processed",
                "research_visibility": "tracked background",
                "notes": "Mostly ambient or indistinct chatter; kept only for provenance.",
            },
            {
                "file": "Beaver Run Resort & Conference Center 2.m4a",
                "duration": "02:41",
                "source_status": "already processed",
                "research_visibility": "tracked background",
                "notes": "Marked already captured in the ledger, but not yet surfaced as its own conference story card.",
            },
            {
                "file": "Beaver Run Resort & Conference Center 3.m4a",
                "duration": "24:22",
                "source_status": "already processed",
                "research_visibility": "tracked background",
                "notes": "Session talk; not user-research evidence.",
            },
            {
                "file": "Beaver Run Resort & Conference Center 4.m4a",
                "duration": "36:47",
                "source_status": "already processed",
                "research_visibility": "tracked background",
                "notes": "Session talk; not user-research evidence.",
            },
            {
                "file": "Beaver Run Resort & Conference Center 5.m4a",
                "duration": "07:45",
                "source_status": "already processed",
                "research_visibility": "featured in research view",
                "notes": "Represented through the Jasmine McGarr and Erin Quakenbush user story.",
            },
            {
                "file": "Beaver Run Resort & Conference Center 6.m4a",
                "duration": "00:06",
                "source_status": "skip",
                "research_visibility": "tracked background",
                "notes": "Too short and unclear to treat as research evidence.",
            },
            {
                "file": "Beaver Run Resort & Conference Center 7.m4a",
                "duration": "15:43",
                "source_status": "already processed",
                "research_visibility": "featured in research view",
                "notes": "Recovered from the failed folder and reviewed. This is a live Memnon reaction conversation about second-brain framing, Greek naming, and voice-to-workflow AI patterns.",
            },
            {
                "file": "Beaver Run Resort & Conference Center 8.m4a",
                "duration": "04:05",
                "source_status": "already processed",
                "research_visibility": "tracked background",
                "notes": "Session talk; not user-research evidence.",
            },
            {
                "file": "Beaver Run Resort & Conference Center 9.m4a",
                "duration": "36:47",
                "source_status": "probable duplicate",
                "research_visibility": "tracked background",
                "notes": "Reviewed and confirmed as another capture of the same AI/citizenship session family already represented by Beaver Run Resort & Conference Center 4.m4a.",
            },
            {
                "file": "New Recording 23.m4a",
                "duration": "48:27",
                "source_status": "already processed",
                "research_visibility": "tracked background",
                "notes": "Session talk; not user-research evidence.",
            },
            {
                "file": "New Recording 24.m4a",
                "duration": "25:14",
                "source_status": "already processed",
                "research_visibility": "featured in research view",
                "notes": "Represented through the Alexandra Seabourn user story.",
            },
            {
                "file": "New Recording 25.m4a",
                "duration": "25:14",
                "source_status": "already processed",
                "research_visibility": "featured in research view",
                "notes": "Second Alexandra Seabourn recording; represented in the combined Alexandra user story.",
            },
            {
                "file": "New Recording 26.m4a",
                "duration": "01:28",
                "source_status": "already processed",
                "research_visibility": "featured in research view",
                "notes": "Represented as the unidentified educator conversation focused on AI access, creativity, and diversity constraints.",
            },
            {
                "file": "New Recording 27.m4a",
                "duration": "25:14",
                "source_status": "probable duplicate",
                "research_visibility": "tracked background",
                "notes": "Reviewed and confirmed as another capture of the Alexandra Seabourn mentoring and new-teacher-support session already represented by New Recording 24.m4a and New Recording 25.m4a.",
            },
        ],
    },
    "session_highlights": [
        {
            "title": "AI citizenship session: process over product",
            "recordings": [
                "Beaver Run Resort & Conference Center 4.m4a",
                "Beaver Run Resort & Conference Center 9.m4a",
            ],
            "kind": "session talk",
            "why_it_matters": "Relevant because it framed classroom AI use around reflection, verbal response, ethical modeling, and shifting assessment toward thinking rather than speed.",
            "key_points": [
                "Teachers were already using AI as a reflection tool and considering verbal-response workflows that make copy-paste shortcuts less central.",
                "A strong line from the session was that if students are only graded on right answers, they will take the fastest path; the real shift is valuing explanation, critique, and process.",
                "The session reinforced a Memnon-adjacent theme: teachers and students both need guided, visible modeling of ethical AI use rather than abstract prohibition.",
            ],
        },
        {
            "title": "Alexandra Seabourn session: scaffolding new teachers",
            "recordings": [
                "New Recording 24.m4a",
                "New Recording 25.m4a",
                "New Recording 27.m4a",
            ],
            "kind": "session talk",
            "why_it_matters": "Relevant because it made the teacher-support problem concrete: behavior scaffolds, de-escalating language, parent communication templates, observation support, and emotional safety for novice teachers.",
            "key_points": [
                "The session emphasized that novice teachers often seek advice but still need scaffolded language and concrete moves they can actually use in the moment.",
                "Parent communication templates, walkthrough encouragement, and non-punitive observation routines were framed as practical retention support rather than compliance tools.",
                "This session sharpened the Memnon fit around reflective grounding, emotional safety, and practical coaching support for new teachers.",
            ],
        },
    ],
    "user_stories": [
        {
            "name": "Alexandra Seabourn",
            "aliases": ["Alexandra Alexsi Seabourn"],
            "recording": "New Recording 24.m4a and New Recording 25.m4a",
            "role_organization": "Instructional Coach / Mentor context",
            "user_type": "teacher-support leader",
            "active_problem": "supporting and retaining new teachers through practical coaching and encouragement",
            "urgency": "high",
            "signal_strength": "strong",
            "reaction_to_memnon": "strong resonance with honest, vulnerable, safe reflection",
            "what_matters": "new teachers need concrete scaffolds, modeled language, support with parent communication, and non-punitive mentoring",
            "best_follow_up_angle": "Memnon as dual support: practical coaching plus reflective grounding",
            "best_next_steps": [
                "Send a follow-up that explicitly recognizes Alexandra Alexsi Seabourn and Alexandra Seabourn as the same contact record.",
                "Share memnon.app with a short note connecting Memnon to mentoring, scaffolded reflection, and new-teacher retention.",
                "Invite a concrete reaction to one specific use case: reflective support for novice teachers after hard parent or classroom moments.",
            ],
            "draft_next_message": "thank her for the session, share memnon.app, note the overlap with her scaffolding and two-mentor framing, and invite feedback",
        },
        {
            "name": "Unknown Educator From New Recording 26",
            "recording": "New Recording 26.m4a",
            "role_organization": "unknown",
            "user_type": "educator interested in classroom AI access",
            "active_problem": "how to teach students to use AI and technology well, especially where access and resource constraints matter",
            "urgency": "medium",
            "signal_strength": "weak",
            "reaction_to_memnon": "not directly captured",
            "what_matters": "students need access to powerful tools; AI may help with creativity and idea generation; diversity and limited supplies may shape how these tools are used",
            "best_follow_up_angle": "only useful if identity can be recovered from surrounding recordings or memory",
            "best_next_steps": [
                "Do not send outreach yet; first identify the person from adjacent conference notes, badge photos, or nearby recordings.",
                "Once identified, follow up around classroom AI access, constrained-resource settings, and how students actually use the tools.",
                "Ask one concrete discovery question rather than pitching broadly: where does AI access currently break down for students?",
            ],
            "draft_next_message": "not ready until person is identified",
        },
        {
            "name": "Unknown Memnon-Reaction Contact From Beaver Run 7",
            "recording": "Beaver Run Resort & Conference Center 7.m4a",
            "role_organization": "unknown conference contact",
            "user_type": "AI-curious educator or conference peer reacting to workflow tools",
            "active_problem": "how to turn voice, memory, and ongoing thought capture into useful AI-supported workflows rather than scattered notes",
            "urgency": "medium",
            "signal_strength": "medium",
            "reaction_to_memnon": "positive reaction to the second-brain framing, the Greek naming, and the path from a Python script to a usable product; explicitly connected it to tools like Pocket AI, Plaud-style devices, and NotebookLM-like outputs",
            "what_matters": "voice-first capture already reads as a meaningful workflow category to this person; Memnon felt legible not because AI was novel, but because the workflow made sense",
            "best_follow_up_angle": "if the identity can be recovered, follow up around voice-first reflection workflows and what current tools still fail to do for teachers or thoughtful professionals",
            "best_next_steps": [
                "Treat this as product-signal evidence even if the identity remains unknown: the category made immediate sense to the listener.",
                "If you can identify the person, ask which existing voice-to-AI workflow products they have actually tried and where those tools still feel shallow, generic, or poorly contextualized.",
                "Use this recording as support for the claim that Memnon is entering an already-recognizable workflow space rather than inventing behavior from scratch.",
            ],
            "draft_next_message": "not ready until person is identified, but the useful follow-up would be about voice-first AI workflows, second-brain tools, and where teacher-specific context is still missing",
        },
        {
            "name": "Jasmine McGarr and Erin Quakenbush",
            "aliases": ["mcgarr_jasmine@svvsd.org", "Beaver Run 5 educators"],
            "recording": "Beaver Run Resort & Conference Center 5.m4a",
            "role_organization": "specialized-program educators; St. Vrain Valley Schools context",
            "user_type": "educators working close to behavior support, autism support, accessibility, and teacher training",
            "active_problem": "how to help adults and then students use powerful tools safely and usefully, especially in specialized settings with communication and accessibility needs",
            "urgency": "high",
            "signal_strength": "medium",
            "reaction_to_memnon": "conversation suggests openness to educator-facing and accessibility-supportive tools",
            "what_matters": "adult-first piloting reduces risk; accessibility and translation use cases are meaningful; strong professional development access changes adoption; AI may help students who otherwise struggle to communicate or access language",
            "best_follow_up_angle": "position Memnon and related tools as practical supports for educator experimentation, accessibility, and reflective adoption in specialized programs",
            "best_next_steps": [
                "Follow up with Jasmine McGarr and Erin Quakenbush directly, tying the note to adult-first experimentation, accessibility, and communication support.",
                "Offer a lightweight conversation or demo framed around specialized-program use cases rather than general AI enthusiasm.",
                "Probe whether Memnon could support staff reflection, documentation, or communication workflows before moving to student-facing claims.",
            ],
            "draft_next_message": "great meeting you both at InnEdCO. I kept thinking about your comments on adult-first experimentation, accessibility, and communication support in specialized settings. I'd love to stay in touch and hear more about how those needs show up in practice, and whether a tool like Memnon might be useful on the staff reflection or support side.",
        },
        {
            "name": "Toni Rose Deanon",
            "aliases": ["TR"],
            "recording": "Conversation at InnEdCO; no linked recording captured yet",
            "role_organization": "mentor / teacher-support context for math teachers",
            "user_type": "teacher mentor and cohort support lead",
            "active_problem": "supporting a group of math teachers in ways that preserve teacher growth, judgment, and instructional energy",
            "urgency": "high",
            "signal_strength": "strong",
            "reaction_to_memnon": "not directly documented, but the mentoring context suggests strong potential fit",
            "what_matters": "the importance of play in education, supporting math teachers well, and helping teachers grow without collapsing into rigid or compliance-heavy practice",
            "best_follow_up_angle": "Memnon as reflective support for mentors and teacher cohorts, especially where teachers need to process classroom moments while preserving play, judgment, and experimentation",
            "best_next_steps": [
                "Send a direct follow-up that references mentoring math teachers and the importance of play in education.",
                "Share memnon.app as a possible support for mentor reflection, coaching debriefs, and teacher cohort growth rather than as a generic AI tool.",
                "Ask whether Toni Rose reacts more to Memnon as support for mentor reflection, teacher processing after hard class moments, or pattern noticing across a cohort.",
            ],
            "draft_next_message": "I really enjoyed our conversation at InnEdCO, especially your perspective on mentoring math teachers and the importance of play in education. I kept thinking Memnon might be relevant in that context, not as another compliance tool, but as a way to help teachers reflect on classroom moments, process challenges, and stay connected to their own judgment. If you're open to it, I'd love to share it and get your reaction from the perspective of someone supporting a teacher cohort.",
        },
        {
            "name": "Joi Lin",
            "recording": "Conversation at InnEdCO; LinkedIn post follow-up",
            "role_organization": "responsible AI / EdTech / access-oriented education contact",
            "user_type": "equity and learning-access oriented connector",
            "active_problem": "how to use AI and educational technology responsibly while improving learning access for underserved or incarcerated individuals",
            "urgency": "medium",
            "signal_strength": "medium",
            "reaction_to_memnon": "not directly documented",
            "what_matters": "responsible AI, better learning access, and practical responsibility in how educational systems and tools are designed",
            "best_follow_up_angle": "start from responsible AI and access rather than a hard Memnon pitch; explore whether reflective support for educators or facilitators working in constrained contexts is relevant",
            "best_next_steps": [
                "Follow up on LinkedIn with a note tied to responsible AI, EdTech, and learning access for incarcerated individuals.",
                "Use the recent LinkedIn post as the bridge rather than switching immediately into product promotion.",
                "Ask one discovery question about where reflective, educator-support, or workflow-support tools might matter in the contexts Joi cares about.",
            ],
            "draft_next_message": "I really appreciated connecting with you and talking about responsible AI, EdTech, and supporting incarcerated individuals through better learning access. I've kept thinking about that conversation. I'd love to stay in touch, and if it's useful, I'd also be glad to share a bit more about what I'm building with Memnon and Teach League in case any of it connects to the work and questions you care about.",
        },
        {
            "name": "Jaylen McGrew",
            "recording": "Los Pinos Condos 2.m4a",
            "role_organization": "educator / entrepreneurial teacher context",
            "user_type": "teacher with entrepreneurial interest and existing AI workflow habits",
            "active_problem": "how to build tools that genuinely support teaching while preserving teacher voice, autonomy, and sustainable reflective practice",
            "urgency": "high",
            "signal_strength": "strong",
            "reaction_to_memnon": "said he would use something like Memnon and noted that he already talks to his chatbot, with that chatbot reflection hitting many of the same points",
            "what_matters": "teacher autonomy, values-aligned educational tools, reflection that helps with isolation and hard classroom moments, and workflows that feel grounded in real teaching rather than generic edtech sales pitches",
            "best_follow_up_angle": "Memnon as a streamlined version of a real workflow he already performs manually: reflective conversation with a chatbot after teaching challenges",
            "best_next_steps": [
                "Follow up by naming both sides of the conversation: his excitement about Teach League and his existing chatbot-based reflection workflow that maps to Memnon.",
                "Ask what his homebrewed chatbot reflection flow currently gets right and where it is clunky, inconsistent, or missing context.",
                "Position Memnon not as a brand-new behavior to adopt, but as a better-structured, teacher-specific version of a problem he is already solving for himself.",
            ],
            "draft_next_message": "I kept thinking about our condo conversation, especially the moment where you said you already talk to your chatbot and that the reflection it gives you hits many of the same points. That feels like a real signal to me that Memnon is pointed at an actual teacher need rather than an invented one. I'd love to hear more about what your current workflow gets right, where it feels clunky, and whether a more teacher-specific version would actually be useful.",
        },
    ],
    "speaker_notes": {
        "session_title": "Teaching Citizenship in the Age of Artificial Intelligence",
        "event": "InnEdCO 2026",
        "speakers": [
            {
                "name": "LeeAnn Lindsey",
                "role": "Director, EdTech and Innovation",
                "organization": "Northern Arizona University",
                "why_relevant": "institutional innovation and responsible AI implementation perspective",
                "outreach_angle": "connect around responsible AI implementation in education and ask how institutional adoption looks from her vantage point",
                "status": "Need a stronger official public bio or in-app speaker bio to sharpen this profile.",
            },
            {
                "name": "Tara Shanley",
                "role": "Sr. Director, Product & AI Innovation",
                "organization": "Learning.com",
                "why_relevant": "classroom experience plus curriculum and product leadership around digital and AI literacy",
                "outreach_angle": "compare notes on digital literacy, AI literacy, and pedagogy-first product design for teachers",
                "status": "Strong overlap with Memnon and teacher-facing AI positioning.",
            },
            {
                "name": "Carolyne Quintana",
                "role": "CEO",
                "organization": "Teaching Matters",
                "why_relevant": "system-level implementation, literacy, equity, and AI integration perspective",
                "outreach_angle": "ask how she evaluates whether AI supports coherence and equity at scale",
                "status": "Strong system-level leader for responsible AI and teacher-support conversations.",
            },
        ],
        "cross_speaker_themes": [
            "AI literacy versus digital literacy",
            "citizenship, ethics, and truth in student interaction with AI",
            "institutional adoption versus classroom-level practicality",
            "how to support teachers without overwhelming them",
            "what responsible AI looks like in real implementation settings",
        ],
        "positioning": [
            "Teach League as teacher-facing AI work around curriculum and planning",
            "Memnon as work around reflection, grounded perspective, and teacher support",
            "CSTA Responsible AI Fellowship",
            "practical, humane, teacher-trust-centered AI adoption",
        ],
    },
}
RESEARCH_SIGNALS_DOC = ("app_metrics", "research_signals")

RESEARCH_THEME_RULES = {
    "problem_themes": {
        "planning_load": ["plan", "planning", "prep", "preparation", "lesson", "curriculum"],
        "burnout_energy": ["burnout", "exhaust", "drained", "overwhelm", "surviv", "fatigue"],
        "student_behavior": ["behavior", "behaviour", "discipline", "classroom management", "disrupt"],
        "parent_communication": ["parent", "family", "guardian", "email home", "conference"],
        "assessment_feedback": ["grade", "grading", "assessment", "feedback", "rubric"],
        "admin_compliance": ["admin", "paperwork", "documentation", "compliance", "meeting"],
        "time_fragmentation": ["time", "juggle", "too much", "no time", "fragment", "bandwidth"],
        "privacy_trust": ["private", "privacy", "trust", "sensitive", "confidential"],
    },
    "objection_themes": {
        "unclear_value": ["why", "not sure", "unclear", "confus", "don't get", "doesn't make sense"],
        "too_personal": ["too personal", "vulnerable", "private", "intimate", "emotional"],
        "too_much_friction": ["friction", "too many steps", "setup", "complicated", "another app"],
        "voice_discomfort": ["voice", "speaking", "recording", "audio", "talking out loud"],
        "workflow_mismatch": ["workflow", "routine", "habit", "fit", "where this goes"],
        "ai_skepticism": ["ai", "halluc", "accur", "trust the output", "robot"],
    },
    "workflow_stages": {
        "during_school_day": ["during class", "between classes", "prep period", "lunch", "school day"],
        "end_of_day": ["after school", "end of day", "after class", "dismissal"],
        "commute_transition": ["drive home", "commute", "car", "walk home"],
        "planning_block": ["planning time", "lesson planning", "prep block", "sunday"],
        "hard_moment_recovery": ["hard day", "rough day", "bad day", "meltdown", "incident"],
    },
    "desired_outcomes": {
        "emotional_processing": ["process", "decompress", "let go", "feel seen", "reflect"],
        "practical_next_steps": ["next step", "tomorrow", "action", "plan", "decide"],
        "captured_context": ["remember", "context", "keep track", "capture", "hold onto"],
        "sensemaking_patterns": ["pattern", "notice", "make sense", "connect dots", "understand"],
        "communication_support": ["share", "communicate", "email", "talk to", "bring to"],
    },
}

RESEARCH_SEGMENT_RULES = {
    "classroom_teacher": ["teacher", "grade", "classroom", "humanities", "math", "science", "ela"],
    "instructional_coach": ["coach", "instructional coach", "mentor", "facilitator"],
    "school_leader": ["principal", "assistant principal", "director", "admin"],
    "specialist": ["counselor", "counsellor", "intervention", "special education", "sped", "librarian"],
}


def _match_research_labels(text: str, rules: dict[str, list[str]], limit: int = 4) -> list[str]:
    haystack = (text or "").lower()
    if not haystack:
        return []
    matches = []
    for label, patterns in rules.items():
        if any(pattern.lower() in haystack for pattern in patterns):
            matches.append(label)
        if len(matches) >= limit:
            break
    return matches


def _score_research_fit(payload: dict) -> int:
    score = 0
    weekly = (payload.get("would_use_weekly") or "").strip().lower()
    if weekly == "yes":
        score += 2
    elif weekly == "maybe":
        score += 1

    if payload.get("strongest_reaction"):
        score += 1
    if payload.get("quote"):
        score += 1
    if payload.get("next_step"):
        score += 1
    if payload.get("confusions"):
        score -= 1

    return max(1, min(score, 5))


def _code_research_note(payload: dict) -> dict:
    combined_problem_text = " ".join([
        payload.get("top_problem") or "",
        payload.get("current_workaround") or "",
        " ".join(payload.get("tags") or []),
    ])
    combined_objection_text = " ".join([
        payload.get("confusions") or "",
        payload.get("strongest_reaction") or "",
        payload.get("quote") or "",
    ])
    combined_workflow_text = " ".join([
        payload.get("top_problem") or "",
        payload.get("current_workaround") or "",
        payload.get("next_step") or "",
        payload.get("quote") or "",
    ])
    combined_outcome_text = " ".join([
        payload.get("strongest_reaction") or "",
        payload.get("quote") or "",
        payload.get("next_step") or "",
    ])
    segment_text = " ".join([
        payload.get("role") or "",
        payload.get("school_context") or "",
    ])

    segment = _match_research_labels(segment_text, RESEARCH_SEGMENT_RULES, limit=1)

    return {
        "problem_themes": _match_research_labels(combined_problem_text, RESEARCH_THEME_RULES["problem_themes"]),
        "objection_themes": _match_research_labels(combined_objection_text, RESEARCH_THEME_RULES["objection_themes"]),
        "workflow_stages": _match_research_labels(combined_workflow_text, RESEARCH_THEME_RULES["workflow_stages"]),
        "desired_outcomes": _match_research_labels(combined_outcome_text, RESEARCH_THEME_RULES["desired_outcomes"]),
        "segment": segment[0] if segment else "other_educator",
        "fit_score": _score_research_fit(payload),
    }


def _top_counter_items(counter: Counter, limit: int = 6) -> list[dict]:
    return [{"label": name, "count": count} for name, count in counter.most_common(limit)]


def _recommend_reflection_style(problem_counter: Counter, outcome_counter: Counter, objection_counter: Counter) -> str:
    practical_signal = (
        problem_counter.get("planning_load", 0)
        + problem_counter.get("time_fragmentation", 0)
        + outcome_counter.get("practical_next_steps", 0)
    )
    grounded_signal = (
        problem_counter.get("burnout_energy", 0)
        + problem_counter.get("privacy_trust", 0)
        + outcome_counter.get("emotional_processing", 0)
    )
    caution_signal = objection_counter.get("too_personal", 0) + objection_counter.get("voice_discomfort", 0)

    if practical_signal >= grounded_signal + 2:
        return "practical"
    if grounded_signal > practical_signal and caution_signal <= grounded_signal:
        return "grounded"
    return "complete"


def _build_research_recommendations_from_counters(
    problem_counter: Counter,
    objection_counter: Counter,
    outcome_counter: Counter,
) -> dict:
    recommended_style = _recommend_reflection_style(problem_counter, outcome_counter, objection_counter)
    guidance = []
    if problem_counter.get("time_fragmentation", 0) or problem_counter.get("planning_load", 0):
        guidance.append("Keep action items small, concrete, and realistic for a busy teaching day.")
    if problem_counter.get("burnout_energy", 0):
        guidance.append("Name sustainability concerns directly when they are present instead of treating them as secondary.")
    if objection_counter.get("too_personal", 0) or problem_counter.get("privacy_trust", 0):
        guidance.append("Use privacy-respecting language and avoid overstating intimacy or certainty.")
    if outcome_counter.get("practical_next_steps", 0):
        guidance.append("Prioritize useful next-step clarity over abstract inspiration.")
    if not guidance:
        guidance.append("Balance practical help with reflective depth and stay close to the user's words.")

    explanation_map = {
        "practical": "Research notes currently lean toward time pressure, planning load, and demand for concrete next steps.",
        "grounded": "Research notes currently lean toward emotional processing and a calmer reflective return.",
        "complete": "Research notes point to mixed needs: practical clarity plus a more grounded reflective synthesis.",
    }
    return {
        "recommended_reflection_style": recommended_style,
        "prompt_guidance": guidance[:4],
        "explanation": explanation_map[recommended_style],
    }


def _recompute_research_signals() -> dict:
    problem_counter = Counter()
    objection_counter = Counter()
    workflow_counter = Counter()
    outcome_counter = Counter()
    segment_counter = Counter()
    weekly_counter = Counter()
    fit_total = 0
    note_count = 0

    for user_snap in _get_db().collection("users").stream():
        docs = _get_db().collection("users").document(user_snap.id).collection("research_notes").stream()
        for snap in docs:
            payload = snap.to_dict() or {}
            coded_research = {
                "problem_themes": _safe_string_list(payload.get("problem_themes")),
                "objection_themes": _safe_string_list(payload.get("objection_themes")),
                "workflow_stages": _safe_string_list(payload.get("workflow_stages")),
                "desired_outcomes": _safe_string_list(payload.get("desired_outcomes")),
                "segment": _safe_string(payload.get("segment")),
                "fit_score": max(0, _safe_int(payload.get("fit_score"), 0)),
            }
            if not any([
                coded_research["problem_themes"],
                coded_research["objection_themes"],
                coded_research["workflow_stages"],
                coded_research["desired_outcomes"],
                coded_research["segment"],
                coded_research["fit_score"],
            ]):
                coded_research = _code_research_note({
                    "top_problem": payload.get("top_problem") or "",
                    "current_workaround": payload.get("current_workaround") or "",
                    "strongest_reaction": payload.get("strongest_reaction") or "",
                    "confusions": payload.get("confusions") or "",
                    "quote": payload.get("quote") or "",
                    "next_step": payload.get("next_step") or "",
                    "role": payload.get("role") or "",
                    "school_context": payload.get("school_context") or "",
                    "would_use_weekly": payload.get("would_use_weekly") or "",
                    "tags": _safe_string_list(payload.get("tags")),
                })

            note_count += 1
            for item in coded_research["problem_themes"]:
                problem_counter[item] += 1
            for item in coded_research["objection_themes"]:
                objection_counter[item] += 1
            for item in coded_research["workflow_stages"]:
                workflow_counter[item] += 1
            for item in coded_research["desired_outcomes"]:
                outcome_counter[item] += 1
            if coded_research["segment"]:
                segment_counter[coded_research["segment"]] += 1
            weekly_counter[_safe_string(payload.get("would_use_weekly")) or "unspecified"] += 1
            fit_total += _safe_int(coded_research["fit_score"], 0)

    recommendations = _build_research_recommendations_from_counters(
        problem_counter,
        objection_counter,
        outcome_counter,
    )
    signals = {
        "updated_at": firestore.SERVER_TIMESTAMP,
        "summary": {
            "research_notes_count": note_count,
            "research_weekly": dict(weekly_counter),
            "research_avg_fit_score": round(fit_total / note_count, 1) if note_count else 0,
        },
        "top_problem_themes": _top_counter_items(problem_counter),
        "top_objection_themes": _top_counter_items(objection_counter),
        "top_workflow_stages": _top_counter_items(workflow_counter),
        "top_desired_outcomes": _top_counter_items(outcome_counter),
        "top_research_segments": _top_counter_items(segment_counter),
        "recommendations": recommendations,
    }
    _get_db().collection(RESEARCH_SIGNALS_DOC[0]).document(RESEARCH_SIGNALS_DOC[1]).set(signals, merge=True)
    return signals


def _load_research_signals() -> dict:
    try:
        doc = _get_db().collection(RESEARCH_SIGNALS_DOC[0]).document(RESEARCH_SIGNALS_DOC[1]).get()
        if doc.exists:
            data = doc.to_dict() or {}
            if isinstance(data.get("recommendations"), dict):
                return data
    except Exception:
        traceback.print_exc()
    return _recompute_research_signals()


def _research_prompt_guidance(mode: str = "general") -> list[str]:
    signals = _load_research_signals()
    guidance = list((signals.get("recommendations") or {}).get("prompt_guidance") or [])
    if mode in {"practical", "complete"}:
        guidance.append("If workload pressure appears, prefer one or two feasible next steps over a larger plan.")
    if mode in {"grounded", "script"}:
        guidance.append("Stay calm and non-performative; do not intensify the emotional tone beyond what the user actually expressed.")
    if mode == "teaching":
        guidance.append("Treat teacher sustainability as part of the instructional context when it is relevant, not as an afterthought.")
    return guidance[:5]


def _append_research_prompt_guidance(prompt: str, mode: str = "general") -> str:
    guidance = _research_prompt_guidance(mode)
    if not guidance:
        return prompt
    lines = "\n".join(f"- {item}" for item in guidance)
    return f"""{prompt}

Additional product guidance from recent teacher research:
{lines}
"""
FOUNDER_METRICS_EXCLUDED_EMAILS = {
    email.strip().lower()
    for email in (os.environ.get("FOUNDER_METRICS_EXCLUDED_EMAILS", "eitanfire@gmail.com")).split(",")
    if email.strip()
}

# ── helpers ───────────────────────────────────────────────────────────────────

def _client_secrets_path() -> str:
    raw = os.environ.get("GOOGLE_CLIENT_SECRETS", "")
    if not raw:
        raise RuntimeError("GOOGLE_CLIENT_SECRETS env var not set")
    p = Path("/tmp/google_client_secrets.json")
    if raw.strip().startswith("{"):
        p.write_text(raw)
    else:
        p.write_text(Path(raw).read_text())
    return str(p)


def _verify_firebase_token(req) -> str | None:
    """Verify Firebase ID token from Authorization header. Returns uid or None."""
    header = req.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        _get_db()
        return fb_auth.verify_id_token(header[7:])["uid"]
    except Exception:
        return None


flask_app.register_blueprint(
    create_workflows_blueprint(
        verify_token=_verify_firebase_token,
        service_provider=_workflow_service,
        transcribe_audio=transcribe_audio_bytes,
        transcription_api_key_provider=load_openai_api_key,
        archive_voice_capture_audio=lambda **kwargs: _upload_workflow_voice_audio(**kwargs),
        download_voice_capture_audio=lambda storage_path: _download_workflow_voice_audio(storage_path),
    ),
    url_prefix="/workflows",
)


def _safe_frontend_return_url(candidate: str | None) -> str:
    """Allow redirects only to known frontend origins."""
    if not candidate:
        return FRONTEND_URL
    try:
        parsed = urllib.parse.urlparse(candidate)
    except Exception:
        return FRONTEND_URL
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in ALLOWED_FRONTEND_ORIGINS:
        return FRONTEND_URL
    path = parsed.path or "/"
    safe_url = f"{origin}{path}"
    if parsed.query:
        safe_url += f"?{parsed.query}"
    return safe_url


def _append_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _sanitize_usage_metadata(value, depth: int = 0):
    if depth > 3:
        return None
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_str = str(key).strip()[:80]
            if not key_str:
                continue
            normalized = _sanitize_usage_metadata(item, depth + 1)
            if normalized is not None:
                cleaned[key_str] = normalized
        return cleaned
    if isinstance(value, list):
        cleaned = []
        for item in value[:12]:
            normalized = _sanitize_usage_metadata(item, depth + 1)
            if normalized is not None:
                cleaned.append(normalized)
        return cleaned
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:240] if text else None


def _log_usage_event(uid: str, event_name: str, metadata: dict | None = None) -> None:
    normalized_name = re.sub(r"[^a-z0-9:_-]+", "_", (event_name or "").strip().lower())[:80]
    if not normalized_name:
        return
    payload = {
        "event_name": normalized_name,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    cleaned_metadata = _sanitize_usage_metadata(metadata or {})
    if cleaned_metadata:
        payload["metadata"] = cleaned_metadata
    _get_db().collection("users").document(uid).collection("usage_events").add(payload)


def _serialize_firestore_value(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return _normalize_datetime(value).isoformat()
    if hasattr(value, "isoformat"):
        try:
            return _normalize_datetime(datetime.fromisoformat(value.isoformat().replace("Z", "+00:00"))).isoformat()
        except Exception:
            pass
    return value


def _ordinal_day(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _format_natural_date(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
        if iso_match:
            dt = datetime(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        else:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except Exception:
                return text
    return f"{dt.strftime('%B')} {_ordinal_day(dt.day)}"


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _coerce_datetime(raw):
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _normalize_datetime(raw)
    if hasattr(raw, "isoformat"):
        try:
            return _normalize_datetime(datetime.fromisoformat(raw.isoformat().replace("Z", "+00:00")))
        except Exception:
            pass
    text = str(raw).strip()
    if not text:
        return None
    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if iso_match:
        return datetime(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
    try:
        return _normalize_datetime(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except Exception:
        return None


def _safe_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _safe_string_list(value, limit: int | None = None) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = []
    cleaned = []
    for item in items:
        text = _safe_string(item)
        if text:
            cleaned.append(text)
        if limit is not None and len(cleaned) >= limit:
            break
    return cleaned


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _empty_research_summary(requester_data: dict | None = None) -> dict:
    requester_data = requester_data or {}
    return {
        "profile": {
            "preferred_name": requester_data.get("preferred_name") or requester_data.get("name") or "",
            "email": requester_data.get("email") or "",
        },
        "summary": {
            "notes_count": 0,
            "reply_count": 0,
            "research_notes_count": 0,
            "styles": {},
            "teaching_context": {"on": 0, "off": 0},
            "events": {},
            "research_weekly": {},
            "research_avg_fit_score": 0,
            "recent_active_users_7d": 0,
            "newly_activated_users_7d": 0,
            "returned_users_7d": 0,
            "core_action_users_7d": 0,
            "anonymized_research_participants": 0,
            "users_count": 0,
        },
        "guide_usage": {},
        "passage_usage_count": 0,
        "top_voices": [],
        "top_frameworks": [],
        "top_problem_themes": [],
        "top_objection_themes": [],
        "top_workflow_stages": [],
        "top_desired_outcomes": [],
        "top_research_segments": [],
        "recommendations": _build_research_recommendations_from_counters(Counter(), Counter(), Counter()),
        "recent_notes": [],
        "recent_events": [],
        "recent_research_notes": [],
        "recent_users": [],
    }


def _sort_key_with_fallback(payload: dict, *keys: str):
    for key in keys:
        value = _coerce_datetime(payload.get(key))
        if value is not None:
            return value
    return datetime.min


def _same_calendar_day(left: datetime | None, right: datetime | None) -> bool:
    if not left or not right:
        return False
    return left.date() == right.date()


def _user_is_founder(uid: str) -> bool:
    user_doc = _get_db().collection("users").document(uid).get()
    if not user_doc.exists:
        return False
    email = str((user_doc.to_dict() or {}).get("email") or "").strip().lower()
    return email in FOUNDER_METRICS_EMAILS


def _email_is_founder(email: str | None) -> bool:
    return _safe_string(email).lower() in FOUNDER_METRICS_EMAILS


def _user_is_excluded_from_founder_metrics(user_data: dict) -> bool:
    email = str((user_data or {}).get("email") or "").strip().lower()
    return email in FOUNDER_METRICS_EXCLUDED_EMAILS


def _user_allows_anonymized_research(user_data: dict) -> bool:
    return bool((user_data or {}).get("allow_anonymized_research"))


def _summarize_research(requester_uid: str) -> dict:
    requester_doc = _get_db().collection("users").document(requester_uid).get()
    requester_data = requester_doc.to_dict() if requester_doc.exists else {}

    now = _normalize_datetime(datetime.now(timezone.utc))
    seven_days_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)

    style_counter = Counter()
    teaching_context_counter = Counter()
    voice_counter = Counter()
    framework_counter = Counter()
    event_counter = Counter()
    research_problem_counter = Counter()
    research_objection_counter = Counter()
    research_workflow_counter = Counter()
    research_outcome_counter = Counter()
    research_segment_counter = Counter()
    research_weekly_counter = Counter()
    research_fit_total = 0
    research_notes_count = 0

    total_notes_count = 0
    total_reply_count = 0
    all_recent_notes = []
    all_recent_events = []
    all_research_notes = []
    recent_users = []

    recent_active_users_7d = 0
    newly_activated_users_7d = 0
    returned_users_7d = 0
    core_action_users_7d = 0
    anonymized_research_participants = 0
    anonymous_user_counter = 0

    for user_snap in _get_db().collection("users").stream():
        user_id = user_snap.id
        user_data = user_snap.to_dict() or {}
        if _user_is_excluded_from_founder_metrics(user_data):
            continue
        allows_anonymized_research = _user_allows_anonymized_research(user_data)
        if allows_anonymized_research:
            anonymized_research_participants += 1
        anonymous_user_counter += 1
        anonymous_label = f"Teacher {anonymous_user_counter}"
        user_ref = _get_db().collection("users").document(user_id)
        notes = []
        for note_snap in user_ref.collection("notes").stream():
            note = note_snap.to_dict() or {}
            note["_id"] = note_snap.id
            notes.append(note)
        notes.sort(key=lambda item: _sort_key_with_fallback(item, "created_at", "date"), reverse=True)

        events = []
        for event_snap in user_ref.collection("usage_events").stream():
            event = event_snap.to_dict() or {}
            event["_id"] = event_snap.id
            events.append(event)
        events.sort(key=lambda item: _sort_key_with_fallback(item, "created_at"), reverse=True)

        captures_count = len(notes)
        plays_count = sum(1 for event in events if event.get("event_name") == "played_reflection_audio")
        replies_count = sum(int(note.get("participant_response_count") or 0) for note in notes)
        total_notes_count += captures_count
        total_reply_count += replies_count

        for note in notes:
            style = note.get("reflection_style") or "complete"
            if allows_anonymized_research:
                style_counter[style] += 1
                if note.get("include_teaching_context") is False:
                    teaching_context_counter["off"] += 1
                else:
                    teaching_context_counter["on"] += 1
                for voice in _safe_string_list(note.get("voice_labels")):
                    voice_name = re.sub(r"\s+", " ", voice).strip()
                    if voice_name:
                        voice_counter[voice_name] += 1
                all_recent_notes.append({
                    "id": note.get("_id") or "",
                    "user_id": user_id,
                    "anonymous_label": anonymous_label,
                    "date": _format_natural_date(note.get("date") or note.get("created_at")),
                    "reflection_style": style,
                    "include_teaching_context": note.get("include_teaching_context") is not False,
                    "voices_count": len(_safe_string_list(note.get("voice_labels"))),
                    "_sort_at": _sort_key_with_fallback(note, "created_at", "date"),
                })

        for event in events:
            event_name = event.get("event_name") or "unknown"
            event_counter[event_name] += 1
            all_recent_events.append({
                "id": event.get("_id") or "",
                "user_id": user_id,
                "anonymous_label": anonymous_label,
                "event_name": event_name,
                "created_at": _serialize_firestore_value(event.get("created_at")),
                "metadata": event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
                "_sort_at": _sort_key_with_fallback(event, "created_at"),
            })

        if allows_anonymized_research:
            for framework in _safe_string_list(user_data.get("state_standards")):
                framework_name = re.sub(r"\s+", " ", framework).strip()
                if framework_name:
                    framework_counter[framework_name] += 1

        for research_snap in user_ref.collection("research_notes").stream():
            research_note = research_snap.to_dict() or {}
            coded_research = {
                "problem_themes": _safe_string_list(research_note.get("problem_themes")),
                "objection_themes": _safe_string_list(research_note.get("objection_themes")),
                "workflow_stages": _safe_string_list(research_note.get("workflow_stages")),
                "desired_outcomes": _safe_string_list(research_note.get("desired_outcomes")),
                "segment": _safe_string(research_note.get("segment")),
                "fit_score": max(0, _safe_int(research_note.get("fit_score"), 0)),
            }
            if not any([
                coded_research["problem_themes"],
                coded_research["objection_themes"],
                coded_research["workflow_stages"],
                coded_research["desired_outcomes"],
                coded_research["segment"],
                coded_research["fit_score"],
            ]):
                coded_research = _code_research_note({
                    "top_problem": research_note.get("top_problem") or "",
                    "current_workaround": research_note.get("current_workaround") or "",
                    "strongest_reaction": research_note.get("strongest_reaction") or "",
                    "confusions": research_note.get("confusions") or "",
                    "quote": research_note.get("quote") or "",
                    "next_step": research_note.get("next_step") or "",
                    "role": research_note.get("role") or "",
                    "school_context": research_note.get("school_context") or "",
                    "would_use_weekly": research_note.get("would_use_weekly") or "",
                    "tags": _safe_string_list(research_note.get("tags")),
                })
            research_notes_count += 1
            for item in coded_research["problem_themes"]:
                research_problem_counter[item] += 1
            for item in coded_research["objection_themes"]:
                research_objection_counter[item] += 1
            for item in coded_research["workflow_stages"]:
                research_workflow_counter[item] += 1
            for item in coded_research["desired_outcomes"]:
                research_outcome_counter[item] += 1

            segment = (coded_research["segment"] or "").strip()
            if segment:
                research_segment_counter[segment] += 1

            weekly_signal = _safe_string(research_note.get("would_use_weekly")) or "unspecified"
            research_weekly_counter[weekly_signal] += 1
            research_fit_total += _safe_int(coded_research["fit_score"], 0)

            all_research_notes.append({
                "id": research_snap.id,
                "user_id": user_id,
                "owner_label": (user_data.get("preferred_name") or user_data.get("name") or user_data.get("email") or "Unknown").strip(),
                "teacher_name": _safe_string(research_note.get("teacher_name")),
                "role": _safe_string(research_note.get("role")),
                "school_context": _safe_string(research_note.get("school_context")),
                "top_problem": _safe_string(research_note.get("top_problem")),
                "strongest_reaction": _safe_string(research_note.get("strongest_reaction")),
                "quote": _safe_string(research_note.get("quote")),
                "next_step": _safe_string(research_note.get("next_step")),
                "would_use_weekly": weekly_signal,
                "problem_themes": coded_research["problem_themes"],
                "objection_themes": coded_research["objection_themes"],
                "workflow_stages": coded_research["workflow_stages"],
                "desired_outcomes": coded_research["desired_outcomes"],
                "segment": segment,
                "fit_score": _safe_int(coded_research["fit_score"], 0),
                "_sort_at": _coerce_datetime(research_note.get("created_at")) or datetime.min,
            })

        note_times = [_coerce_datetime(note.get("created_at") or note.get("date")) for note in notes]
        event_times = [_coerce_datetime(event.get("created_at")) for event in events]
        all_activity_times = [value for value in (note_times + event_times) if value is not None]
        last_seen_at = max(all_activity_times) if all_activity_times else None
        first_capture_at = min([value for value in note_times if value is not None], default=None)
        latest_note = notes[0] if notes else {}
        latest_style = latest_note.get("reflection_style") or (user_data.get("reflection_style") or "complete")
        completed_core_action = captures_count > 0 and plays_count > 0

        capture_days = sorted({value.date().isoformat() for value in note_times if value is not None})
        activity_days = sorted({value.date().isoformat() for value in all_activity_times})
        returned_after_first_use = False
        if first_capture_at:
            for day_text in activity_days:
                if day_text != first_capture_at.date().isoformat():
                    returned_after_first_use = True
                    break

        active_in_last_7d = bool(last_seen_at and last_seen_at >= seven_days_ago)
        if active_in_last_7d:
            recent_active_users_7d += 1
        if first_capture_at and first_capture_at >= seven_days_ago:
            newly_activated_users_7d += 1
        if active_in_last_7d and returned_after_first_use:
            returned_users_7d += 1
        if active_in_last_7d and completed_core_action:
            core_action_users_7d += 1

        recent_users.append({
            "user_id": user_id,
            "anonymous_label": anonymous_label,
            "last_seen_at": _serialize_firestore_value(last_seen_at),
            "first_capture_at": _serialize_firestore_value(first_capture_at),
            "captures_count": captures_count,
            "plays_count": plays_count,
            "replies_count": replies_count,
            "activated": captures_count > 0,
            "completed_core_action": completed_core_action,
            "returned_after_first_use": returned_after_first_use,
            "latest_reflection_style": latest_style,
            "allow_anonymized_research": allows_anonymized_research,
            "_sort_at": last_seen_at or datetime.min,
        })

    recent_users.sort(key=lambda item: item.get("_sort_at") or datetime.min, reverse=True)
    all_recent_notes.sort(key=lambda item: item.get("_sort_at") or datetime.min, reverse=True)
    all_recent_events.sort(key=lambda item: item.get("_sort_at") or datetime.min, reverse=True)
    all_research_notes.sort(key=lambda item: item.get("_sort_at") or datetime.min, reverse=True)

    for item in recent_users:
        item.pop("_sort_at", None)
    for item in all_recent_notes:
        item.pop("_sort_at", None)
    for item in all_recent_events:
        item.pop("_sort_at", None)
    for item in all_research_notes:
        item.pop("_sort_at", None)

    return {
        "profile": {
            "preferred_name": requester_data.get("preferred_name") or requester_data.get("name") or "",
            "email": requester_data.get("email") or "",
        },
        "summary": {
            "notes_count": total_notes_count,
            "reply_count": total_reply_count,
            "research_notes_count": research_notes_count,
            "styles": dict(style_counter),
            "teaching_context": {
                "on": teaching_context_counter.get("on", 0),
                "off": teaching_context_counter.get("off", 0),
            },
            "events": dict(event_counter),
            "research_weekly": dict(research_weekly_counter),
            "research_avg_fit_score": round(research_fit_total / research_notes_count, 1) if research_notes_count else 0,
            "recent_active_users_7d": recent_active_users_7d,
            "newly_activated_users_7d": newly_activated_users_7d,
            "returned_users_7d": returned_users_7d,
            "core_action_users_7d": core_action_users_7d,
            "anonymized_research_participants": anonymized_research_participants,
            "users_count": len(recent_users),
        },
        "guide_usage": {},
        "passage_usage_count": 0,
        "top_voices": [{"label": name, "count": count} for name, count in voice_counter.most_common(6)],
        "top_frameworks": [{"label": name, "count": count} for name, count in framework_counter.most_common(6)],
        "top_problem_themes": _top_counter_items(research_problem_counter),
        "top_objection_themes": _top_counter_items(research_objection_counter),
        "top_workflow_stages": _top_counter_items(research_workflow_counter),
        "top_desired_outcomes": _top_counter_items(research_outcome_counter),
        "top_research_segments": _top_counter_items(research_segment_counter),
        "recommendations": _build_research_recommendations_from_counters(
            research_problem_counter,
            research_objection_counter,
            research_outcome_counter,
        ),
        "conference_research": INNEDCO_RESEARCH,
        "recent_notes": all_recent_notes[:8],
        "recent_events": all_recent_events[:20],
        "recent_research_notes": all_research_notes[:12],
        "recent_users": recent_users[:18],
    }


def _requested_google_scopes(include_tasks: bool = False) -> list[str]:
    scopes = list(BASE_GOOGLE_SCOPES)
    if include_tasks:
        scopes.append(TASKS_READONLY_SCOPE)
    return scopes


def _scopes_from_token_data(token_data: dict | None) -> set[str]:
    if not token_data:
        return set()
    raw = token_data.get("scopes")
    if isinstance(raw, list):
        return {scope for scope in raw if isinstance(scope, str) and scope}
    if isinstance(raw, str):
        return {scope for scope in raw.split() if scope}
    return set()


def _merge_google_token(existing_token: dict | None, new_token: dict) -> dict:
    """Preserve broader existing grants when a narrower re-auth returns later."""
    if not existing_token:
        return new_token

    existing_scopes = _scopes_from_token_data(existing_token)
    new_scopes = _scopes_from_token_data(new_token)

    if existing_token.get("refresh_token") and not new_token.get("refresh_token"):
        new_token["refresh_token"] = existing_token["refresh_token"]

    if TASKS_READONLY_SCOPE in existing_scopes and TASKS_READONLY_SCOPE not in new_scopes:
        merged = dict(existing_token)
        merged["scopes"] = sorted(existing_scopes | new_scopes)
        for key in ("client_id", "client_secret", "token_uri", "refresh_token"):
            if new_token.get(key):
                merged[key] = new_token[key]
        return merged

    if existing_scopes or new_scopes:
        new_token["scopes"] = sorted(existing_scopes | new_scopes)
    return new_token


def _drive_creds(uid: str) -> Credentials | None:
    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return None
    token_data = doc.to_dict().get("google_drive_token")
    if not token_data:
        return None
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", BASE_GOOGLE_SCOPES),
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _get_db().collection("users").document(uid).update(
                {"google_drive_token": json.loads(creds.to_json())}
            )
        except Exception as exc:
            print(f"Token refresh failed for {uid}: {exc}")
            return None
    return creds


# ── Drive helpers ──────────────────────────────────────────────────────────────

def _find_or_create_folder(service, name: str, parent_id: str | None = None) -> str:
    parent_q = f"and '{parent_id}' in parents" if parent_id else "and 'root' in parents"
    q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
         f"{parent_q} and trashed=false")
    files = service.files().list(q=q, fields="files(id)", pageSize=1).execute().get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return service.files().create(body=meta, fields="id").execute()["id"]


def _drive_service_for_user(uid: str):
    creds = _drive_creds(uid)
    if not creds:
        return None
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _tasks_service_for_user(uid: str):
    creds = _drive_creds(uid)
    if not creds:
        return None
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def _user_tasks_connected(user_data: dict | None) -> bool:
    token_scopes = _scopes_from_token_data((user_data or {}).get("google_drive_token"))
    if TASKS_READONLY_SCOPE in token_scopes:
        return True
    return bool((user_data or {}).get("google_tasks_connected"))


def _normalize_task_item(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "title": (item.get("title") or "").strip(),
        "due": item.get("due"),
        "notes": (item.get("notes") or "").strip(),
        "status": item.get("status", ""),
        "updated": item.get("updated"),
    }


def _fetch_open_tasks_for_user(uid: str, user_data: dict, limit: int = 12) -> list[dict]:
    if not _user_tasks_connected(user_data):
        return []
    tasklist_id = (user_data.get("google_tasks_list_id") or "").strip()
    if not tasklist_id:
        return []
    service = _tasks_service_for_user(uid)
    if not service:
        return []
    try:
        result = service.tasks().list(
            tasklist=tasklist_id,
            maxResults=max(1, min(limit, 20)),
            showCompleted=False,
            showHidden=False,
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not fetch tasks for reflection context: {exc}")
        return []
    items = []
    for item in result.get("items", []):
        if item.get("status") == "completed":
            continue
        normalized = _normalize_task_item(item)
        if normalized["title"]:
            items.append(normalized)
    return items


def _derive_task_context(transcript: str, tasks: list[dict]) -> list[str]:
    if not tasks:
        return []

    transcript_lower = transcript.lower()
    transcript_tokens = set(re.findall(r"\b[a-z]{4,}\b", transcript_lower))
    stopwords = {
        "about", "after", "again", "because", "being", "could", "every", "first",
        "from", "have", "into", "just", "like", "many", "more", "most", "need",
        "really", "some", "than", "that", "their", "there", "these", "they",
        "this", "today", "very", "what", "when", "where", "which", "with", "would",
    }
    transcript_tokens = {token for token in transcript_tokens if token not in stopwords}

    def display_task(task: dict) -> str:
        return f'{task["title"]}' + (f' (due {task["due"][:10]})' if task.get("due") else "")

    top_tasks = tasks[:3]
    chosen_by_id = {task["id"]: task for task in top_tasks if task.get("id")}

    scored: list[tuple[int, dict]] = []
    for task in tasks:
        title = task.get("title", "")
        notes = task.get("notes", "")
        haystack = f"{title} {notes}".strip().lower()
        if not haystack:
            continue
        task_tokens = {token for token in re.findall(r"\b[a-z]{4,}\b", haystack) if token not in stopwords}
        overlap = len(task_tokens & transcript_tokens)
        title_phrase_match = title.lower() in transcript_lower if len(title) >= 8 else False
        score = overlap + (2 if title_phrase_match else 0)
        if score > 0:
            scored.append((score, task))

    scored.sort(key=lambda item: item[0], reverse=True)
    for _, task in scored:
        if len(chosen_by_id) >= 6:
            break
        task_id = task.get("id")
        if task_id and task_id not in chosen_by_id:
            chosen_by_id[task_id] = task

    ordered = []
    seen = set()
    for task in top_tasks:
        task_id = task.get("id") or task.get("title")
        if task_id in seen:
            continue
        ordered.append(task)
        seen.add(task_id)
    for _, task in scored:
        task_id = task.get("id") or task.get("title")
        if task_id in seen:
            continue
        if task_id in chosen_by_id:
            ordered.append(task)
            seen.add(task_id)
        if len(ordered) >= len(chosen_by_id):
            break

    return [display_task(task) for task in ordered]


def _find_or_create_media_folder(service) -> str:
    return _find_or_create_folder(service, "memnon-media")


def _find_or_create_recordings_folder(service) -> str:
    return _find_or_create_folder(service, "memnon-recordings")


def _find_or_create_reflections_folder(service) -> str:
    return _find_or_create_folder(service, "memnon-reflections")


def _ensure_user_output_folders(service, uid: str, user_data: dict) -> tuple[str, str, str]:
    notes_id = user_data.get("notes_folder_id")
    if not notes_id:
        notes_id = _find_or_create_folder(service, "memnon-notes")
        _get_db().collection("users").document(uid).update({"notes_folder_id": notes_id})

    recordings_id = user_data.get("recordings_folder_id")
    if not recordings_id:
        recordings_id = _find_or_create_recordings_folder(service)
        _get_db().collection("users").document(uid).update({"recordings_folder_id": recordings_id})

    reflections_id = user_data.get("reflections_folder_id")
    if not reflections_id:
        reflections_id = _find_or_create_reflections_folder(service)
        _get_db().collection("users").document(uid).update({"reflections_folder_id": reflections_id})

    return notes_id, recordings_id, reflections_id


def _is_audio(f: dict) -> bool:
    return (f.get("mimeType") in AUDIO_MIME_TYPES or
            any(f.get("name", "").lower().endswith(ext) for ext in AUDIO_EXTENSIONS))


def _normalize_narration_voice(raw: str | None) -> str:
    if raw in NARRATION_VOICES:
        return raw  # type: ignore[return-value]
    return DEFAULT_NARRATION_VOICE


def _normalize_reflection_style(raw: str | None) -> str:
    if raw in {"practical", "grounded", "complete"}:
        return raw  # type: ignore[return-value]
    return "complete"


def _coerce_bool(raw, default: bool = True) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in {"0", "false", "off", "no", ""}


def _safe_timezone_name(raw: str | None) -> str:
    candidate = _safe_string(raw) or DEFAULT_DAILY_FEED_TIMEZONE
    try:
        ZoneInfo(candidate)
        return candidate
    except Exception:
        return DEFAULT_DAILY_FEED_TIMEZONE


def _daily_feed_local_now(user_data: dict, now_utc: datetime | None = None) -> datetime:
    base = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    tz_name = _safe_timezone_name(user_data.get("daily_feed_timezone"))
    return base.astimezone(ZoneInfo(tz_name))


def _daily_feed_date_key(user_data: dict, now_utc: datetime | None = None) -> str:
    return _daily_feed_local_now(user_data, now_utc).date().isoformat()


def _daily_feed_publish_hour(user_data: dict) -> int:
    hour = _safe_int(user_data.get("daily_feed_publish_hour_local"), DEFAULT_DAILY_FEED_PUBLISH_HOUR)
    return min(23, max(0, hour))


def _generate_daily_feed_token() -> str:
    return secrets.token_urlsafe(24)


def _daily_feed_url_for_token(token: str) -> str:
    return f"{DAILY_FEED_ROUTE_BASE}/{urllib.parse.quote(token)}.xml"


def _daily_feed_audio_url(token: str, episode_id: str) -> str:
    return f"{DAILY_FEED_ROUTE_BASE}/{urllib.parse.quote(token)}/{urllib.parse.quote(episode_id)}.mp3"


def _ensure_daily_feed_config(uid: str, user_data: dict, enable: bool | None = None) -> dict:
    user_data = dict(user_data or {})
    updates = {}

    token = _safe_string(user_data.get("daily_feed_token"))
    if not token:
        token = _generate_daily_feed_token()
        updates["daily_feed_token"] = token

    tz_name = _safe_timezone_name(user_data.get("daily_feed_timezone"))
    if _safe_string(user_data.get("daily_feed_timezone")) != tz_name:
        updates["daily_feed_timezone"] = tz_name

    publish_hour = user_data.get("daily_feed_publish_hour_local")
    if publish_hour is None or _safe_int(publish_hour, -1) not in range(24):
        updates["daily_feed_publish_hour_local"] = DEFAULT_DAILY_FEED_PUBLISH_HOUR

    if enable is True and not _coerce_bool(user_data.get("daily_feed_enabled"), False):
        updates["daily_feed_enabled"] = True
    elif enable is False and _coerce_bool(user_data.get("daily_feed_enabled"), False):
        updates["daily_feed_enabled"] = False

    if updates:
        _get_db().collection("users").document(uid).set(updates, merge=True)
        user_data.update(updates)

    return user_data


def _daily_feed_episode_ref(uid: str, episode_id: str):
    return _get_db().collection("users").document(uid).collection("daily_feed_episodes").document(episode_id)


def _load_latest_daily_feed_episode(uid: str) -> dict | None:
    docs = list(
        _get_db()
        .collection("users")
        .document(uid)
        .collection("daily_feed_episodes")
        .order_by("published_at", direction=firestore.Query.DESCENDING)
        .limit(1)
        .stream()
    )
    if not docs:
        return None
    snap = docs[0]
    payload = snap.to_dict() or {}
    payload["id"] = snap.id
    return payload


def _build_daily_feed_status(uid: str, user_data: dict, now_utc: datetime | None = None) -> dict:
    enabled = _coerce_bool((user_data or {}).get("daily_feed_enabled"), False)
    timezone_name = _safe_timezone_name((user_data or {}).get("daily_feed_timezone"))
    publish_hour = _daily_feed_publish_hour(user_data or {})
    local_now = _daily_feed_local_now(user_data or {}, now_utc)
    today_key = _daily_feed_date_key(user_data or {}, now_utc)
    publish_at_local = local_now.replace(hour=publish_hour, minute=0, second=0, microsecond=0)

    latest = _load_latest_daily_feed_episode(uid)
    latest_episode = None
    latest_date_key = ""
    if latest:
        latest_date_key = _safe_string(latest.get("date_key"))
        latest_episode = {
            "id": _safe_string(latest.get("id") or latest.get("date_key")),
            "title": _safe_string(latest.get("title")),
            "description": _safe_string(latest.get("description")),
            "episode_type": _safe_string(latest.get("episode_type")),
            "date_key": latest_date_key,
            "published_at": _serialize_firestore_value(latest.get("published_at")),
        }

    state = "disabled"
    if enabled:
        if latest_episode and latest_date_key == today_key:
            state = "ready"
        elif local_now < publish_at_local:
            state = "scheduled"
        elif local_now < publish_at_local + timedelta(minutes=90):
            state = "preparing"
        else:
            state = "missing"

    return {
        "enabled": enabled,
        "state": state,
        "timezone": timezone_name,
        "publish_hour_local": publish_hour,
        "today_key": today_key,
        "scheduled_for_local": publish_at_local.isoformat(),
        "latest_episode": latest_episode,
        "last_generated_at": _serialize_firestore_value((user_data or {}).get("daily_feed_last_generated_at")),
        "last_attempted_at": _serialize_firestore_value((user_data or {}).get("daily_feed_last_attempted_at")),
        "last_error": _safe_string((user_data or {}).get("daily_feed_last_error")),
        "last_error_at": _serialize_firestore_value((user_data or {}).get("daily_feed_last_error_at")),
    }


def _daily_feed_audio_storage_path(uid: str, episode_id: str) -> str:
    return f"{DAILY_FEED_AUDIO_PREFIX}/{uid}/{episode_id}.mp3"


def _get_storage_bucket():
    _get_db()
    return storage.bucket()


def _upload_daily_feed_audio(uid: str, episode_id: str, audio_bytes: bytes) -> str:
    path = _daily_feed_audio_storage_path(uid, episode_id)
    blob = _get_storage_bucket().blob(path)
    blob.upload_from_string(audio_bytes, content_type="audio/mpeg")
    return path


def _download_daily_feed_audio(storage_path: str) -> bytes:
    blob = _get_storage_bucket().blob(storage_path)
    return blob.download_as_bytes()


WORKFLOW_VOICE_AUDIO_PREFIX = "workflow-voice-audio"


def _workflow_voice_audio_storage_path(uid: str, capture_id: str, filename: str) -> str:
    suffix = Path(filename or "").suffix.lower() or ".webm"
    return f"{WORKFLOW_VOICE_AUDIO_PREFIX}/{uid}/{capture_id}{suffix}"


def _upload_workflow_voice_audio(
    uid: str,
    capture_id: str,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
) -> str:
    path = _workflow_voice_audio_storage_path(uid, capture_id, filename)
    blob = _get_storage_bucket().blob(path)
    blob.upload_from_string(audio_bytes, content_type=content_type or "audio/webm")
    return path


def _download_workflow_voice_audio(storage_path: str) -> bytes:
    blob = _get_storage_bucket().blob(storage_path)
    return blob.download_as_bytes()


def _maybe_archive_workflow_voice_audio(
    uid: str,
    capture_id: str,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
) -> dict[str, object] | None:
    try:
        storage_path = _upload_workflow_voice_audio(
            uid,
            capture_id,
            audio_bytes,
            filename,
            content_type,
        )
    except Exception as exc:
        print(f"[{uid}] Warning: workflow voice audio archive failed: {exc}")
        return None
    return {
        "source_audio_storage_path": storage_path,
        "source_audio_content_type": content_type,
        "source_audio_filename": filename,
        "source_audio_size_bytes": len(audio_bytes),
    }


def _estimate_audio_duration_seconds(text: str) -> int:
    words = len(re.findall(r"\b\S+\b", text or ""))
    if words <= 0:
        return 0
    return max(10, int(round(words / 2.5)))


def _daily_feed_note_identity_keys(note: dict) -> set[str]:
    keys: set[str] = set()
    bridge_capture_id = _safe_string(note.get("bridge_capture_id"))
    if bridge_capture_id:
        keys.add(f"bridge:{bridge_capture_id}")

    date_text = _safe_string(note.get("date"))
    title = _safe_string(note.get("title")).lower()
    anchor = _safe_string(note.get("insight") or note.get("summary")).lower()
    if date_text and title and anchor:
        keys.add(f"content:{date_text}|{title}|{anchor}")
    return keys


def _load_recent_feed_notes(uid: str, limit: int = DEFAULT_DAILY_FEED_RECENT_NOTE_LIMIT) -> list[dict]:
    user_ref = _get_db().collection("users").document(uid)
    docs = []
    seen_keys: set[tuple[str, str]] = set()
    seen_identity_keys: set[str] = set()
    for collection_name in (DAILY_FEED_NOTES_COLLECTION, "notes"):
        notes_ref = user_ref.collection(collection_name)
        for field_name in ("created_at", "date"):
            try:
                field_docs = list(
                    notes_ref.order_by(field_name, direction=firestore.Query.DESCENDING).limit(limit).stream()
                )
            except Exception:
                continue
            for doc in field_docs:
                doc_key = (collection_name, doc.id)
                if not doc.exists or doc_key in seen_keys:
                    continue
                seen_keys.add(doc_key)
                payload = doc.to_dict() or {}
                payload["_id"] = doc.id
                identity_keys = _daily_feed_note_identity_keys(payload)
                if identity_keys and seen_identity_keys.intersection(identity_keys):
                    continue
                seen_identity_keys.update(identity_keys)
                docs.append(payload)
    docs.sort(key=lambda item: _sort_key_with_fallback(item, "created_at", "date"), reverse=True)
    return docs[:limit]


def _daily_feed_has_recent_reflection(notes: list[dict], now_local: datetime) -> bool:
    cutoff = now_local.date() - timedelta(days=DEFAULT_DAILY_FEED_STANDARD_LOOKBACK_DAYS)
    for note in notes:
        note_dt = _coerce_datetime(note.get("created_at") or note.get("date"))
        if note_dt and note_dt.date() >= cutoff:
            return True
    return False


def _build_feed_note_brief(note: dict) -> str:
    pieces = []
    title = _safe_string(note.get("title"))
    if title:
        pieces.append(f"Title: {title}")
    date_text = _safe_string(note.get("date"))
    if date_text:
        pieces.append(f"Date: {date_text}")
    insight = _safe_string(note.get("insight"))
    if insight:
        pieces.append(f"Insight: {insight}")
    summary = _safe_string(note.get("summary"))
    if summary:
        pieces.append(f"Summary: {summary}")
    themes = _safe_string_list(note.get("themes"), limit=5)
    if themes:
        pieces.append("Themes: " + ", ".join(themes))
    replies = int(note.get("participant_response_count") or 0)
    if replies:
        pieces.append(f"Replies: {replies}")
    response_summary = _safe_string(note.get("participant_response_summary") or note.get("participant_response_excerpt"))
    if response_summary:
        pieces.append(f"Teacher follow-up: {response_summary}")
    voices = _safe_string_list(note.get("voice_labels"), limit=3)
    if voices:
        pieces.append("Voices: " + ", ".join(voices))
    return "\n".join(pieces)


def _daily_feed_continuity_anchor(notes: list[dict]) -> str:
    for note in notes:
        for key in ("insight", "summary", "title"):
            text = _safe_string(note.get(key))
            if text:
                return text[:220]
    return ""


def _daily_feed_present_day_anchor(local_now: datetime) -> str:
    return f"Today is {local_now.strftime('%A, %B')} {_ordinal_day(local_now.day)}."


def _build_daily_feed_prompt(
    user_data: dict,
    notes: list[dict],
    episode_type: str,
    local_now: datetime,
    weather_context: dict | None = None,
) -> str:
    preferred_name = (_safe_string(user_data.get("preferred_name")) or _safe_string(user_data.get("name")) or "teacher").strip()
    spoken_name = (_safe_string(user_data.get("spoken_name")) or preferred_name).strip()
    style = _normalize_reflection_style(user_data.get("reflection_style"))
    subjects = _safe_string(user_data.get("subjects"))
    grades = ", ".join(_safe_string_list(user_data.get("grade_levels"), limit=6))
    context_parts = []
    if subjects:
        context_parts.append(f"subjects: {subjects}")
    if grades:
        context_parts.append(f"grades: {grades}")
    school_name = _safe_string(user_data.get("school_name"))
    if school_name:
        context_parts.append(f"school: {school_name}")
    context_line = "; ".join(context_parts) or "teacher context not specified"
    notes_block = "\n\n---\n\n".join(_build_feed_note_brief(note) for note in notes[:DEFAULT_DAILY_FEED_RECENT_NOTE_LIMIT]) or "No recent reflections available."
    day_anchor = _daily_feed_present_day_anchor(local_now)
    continuity_anchor = _daily_feed_continuity_anchor(notes)
    weather_block = ""
    weather_is_speakable = bool(weather_context) and (
        "should_surface" not in weather_context
        or _coerce_bool(weather_context.get("should_surface"), False)
    )
    if weather_is_speakable:
        weather_block = (
            "--- WEATHER CONTEXT ---\n"
            f"Day type: {_safe_string(weather_context.get('day_type'))}\n"
            f"Temperature: {_safe_string(weather_context.get('temperature_summary'))}\n"
            f"Precipitation: {_safe_string(weather_context.get('precipitation_summary'))}\n"
            f"Orientation cue: {_safe_string(weather_context.get('orientation_cue'))}\n\n"
        )

    segment_requirements = (
        '"opening": "15 to 25 seconds, name the day and orient the listener",\n'
        '"practical_briefing": "60 to 120 seconds, name what matters most and why",\n'
        '"calendar_today": "20 to 45 seconds, leave empty string for Phase 1 unless true calendar context exists",\n'
        '"reflective_grounding": "60 to 120 seconds, connect today to a real pattern across time",\n'
        '"meditative_close": "20 to 45 seconds, brief closing line that can later seed a meditative-only feed"'
    )

    return f"""You are writing a private daily audio briefing for {preferred_name}.

This is for spoken audio. The tone should be warm, grounded, specific, and restrained.

Episode type: {episode_type}
Reflection style: {style}
Person context: display name {preferred_name}; spoken name "{spoken_name}"; {context_line}
Present-day anchor available: {day_anchor}
Continuity anchor candidate: {continuity_anchor or "none"}

{weather_block}Recent reflection context:
{notes_block}

Return strict JSON only with this schema:
{{
  "title": "Natural date plus concise theme, e.g. June 22 — Protect the morning",
  "description": "One sentence describing what shaped today's briefing.",
  "time_anchor": "The present-day anchor actually used.",
  "continuity_anchor": "The cross-time continuity anchor actually used.",
  "segments": {{
    {segment_requirements}
  }}
}}

Rules:
- Return JSON only.
- Write for the ear, not the page.
- Use the spoken name "{spoken_name}" only if natural.
- Keep each segment within its soft duration budget.
- Do not produce generic encouragement.
- The opening must contain a real present-day anchor.
- The reflective grounding must contain a real continuity anchor drawn from reflection history.
- For Phase 1, set calendar_today to an empty string unless true calendar context is provided.
- Do not create a weather segment.
- If weather is relevant, treat it only as a short "Outside context" micro-cue near the opening.
- Do not weave weather into practical_briefing, reflective_grounding, or meditative_close.
- Keep meditative_close concise; it is the seed of a future optional meditative-only feed.
- If there is too little specific material, be sparse rather than repetitive.
"""


def _build_opening_weather_rewrite_prompt(
    user_data: dict,
    opening: str,
    weather_context: dict,
) -> str:
    preferred_name = (_safe_string(user_data.get("preferred_name")) or _safe_string(user_data.get("name")) or "teacher").strip()
    spoken_name = (_safe_string(user_data.get("spoken_name")) or preferred_name).strip()
    return f"""You are revising exactly one segment of a private daily audio briefing for {preferred_name}.

Revise only the opening segment below. Keep the date anchor and tone intact. Add at most one short weather micro-cue near the end of the opening.

Existing opening:
{opening}

Weather micro-cue label: {_safe_string(weather_context.get("micro_cue_label")) or "Outside context"}
Weather micro-cue text: {_safe_string(weather_context.get("micro_cue_text"))}

Return strict JSON only with this schema:
{{
  "opening": "Rewritten opening text only"
}}

Rules:
- Rewrite only opening.
- Preserve the existing date anchor.
- Prefer the label "Outside context".
- Keep the weather line short and spoken naturally.
- Do not turn this into a forecast readout.
- Do not mention calendar, tasks, classrooms, or students unless they were already present.
- Use the spoken name "{spoken_name}" only if natural.
"""


def _rewrite_opening_with_weather_cue(
    user_data: dict,
    opening: str,
    weather_context: dict | None,
    api_key: str,
) -> tuple[str, bool]:
    original_text = _safe_string(opening)
    if not original_text or not weather_context or not _coerce_bool(weather_context.get("should_surface"), False):
        return original_text, False
    prompt = _build_opening_weather_rewrite_prompt(user_data, original_text, weather_context)
    result = _summarize(
        prompt,
        api_key,
        json_mode=True,
        timeout_seconds=60,
        max_output_tokens=180,
    )
    rewritten = _safe_string((result or {}).get("opening"))
    if not rewritten:
        return original_text, False
    return rewritten, rewritten != original_text


def _build_daily_feed_generation_meta(
    *,
    recent_notes_available: bool,
    generation_mode: str,
    weather_context: dict | None,
    weather_diagnostics: dict,
    weather_applied: bool,
) -> dict:
    weather_context = weather_context or {}
    weather_diagnostics = weather_diagnostics or {}
    return {
        "recent_notes_available": recent_notes_available,
        "generation_mode": generation_mode,
        "calendar_context_available": False,
        "weather": {
            "available": bool(weather_context) or _coerce_bool(weather_diagnostics.get("available"), False),
            "applied": weather_applied,
            "placement": "opening_micro_cue" if weather_applied else "",
            "label": _safe_string(weather_context.get("micro_cue_label")) if weather_applied else "",
            "source": _safe_string(weather_diagnostics.get("source")),
            "day_type": _safe_string(weather_context.get("day_type")),
            "omission_reason": "" if weather_applied else _safe_string(weather_context.get("omission_reason")),
            "unavailable_reason": _safe_string(weather_diagnostics.get("unavailable_reason")),
        },
    }

def _build_deterministic_daily_feed_result(
    user_data: dict,
    notes: list[dict],
    local_now: datetime,
    episode_type: str = "fallback",
    weather_context: dict | None = None,
) -> dict:
    latest_note = notes[0] if notes else {}
    preferred_name = (_safe_string(user_data.get("preferred_name")) or _safe_string(user_data.get("name")) or "listener").strip()
    latest_title = _safe_string(latest_note.get("title")) or "recent reflection"
    latest_summary = _safe_string(latest_note.get("summary"))
    latest_insight = _safe_string(latest_note.get("insight"))
    continuity_anchor = _daily_feed_continuity_anchor(notes) or latest_title
    time_anchor = _daily_feed_present_day_anchor(local_now)
    weather_micro_cue = ""
    if weather_context and _coerce_bool(weather_context.get("should_surface"), False):
        weather_micro_cue = _safe_string(weather_context.get("micro_cue_text"))

    description = f"Grounded in {latest_title or 'your recent reflections'} and oriented to today."

    opening_parts = [f"{time_anchor} {preferred_name}, here is your Memnon briefing for the day."]
    if weather_micro_cue:
        opening_parts.append(weather_micro_cue)
    opening = " ".join(part for part in opening_parts if part).strip()

    stance_seed = latest_insight or continuity_anchor or latest_summary
    if stance_seed:
        stance_text = stance_seed[:220].strip()
        if not re.search(r"[.!?]$", stance_text):
            stance_text += "."
        practical_briefing = f"Let the day stay organized around one restrained stance: {stance_text}"
    else:
        practical_briefing = "Let the day stay organized around one restrained stance: protect one important thread and keep the pace simple."

    reflective_parts = [
        f"In {latest_title}, you named a pattern that still matters today."
    ]
    if continuity_anchor:
        reflective_parts.append(f"That pattern can be named simply: {continuity_anchor}")
    response_summary = _safe_string(latest_note.get("participant_response_summary") or latest_note.get("participant_response_excerpt"))
    if response_summary:
        reflective_parts.append(f"You also answered back to the reflection in this way: {response_summary[:220].strip()}")
    reflective_grounding = " ".join(reflective_parts)

    meditative_close = "Carry one clear intention into the day: stay close to what feels true, and let one next step be enough."

    return {
        "title": f"{_format_natural_date(local_now.date().isoformat())} — {latest_title[:64]}",
        "description": description[:500],
        "time_anchor": time_anchor,
        "continuity_anchor": continuity_anchor[:240],
        "_weather_applied": bool(weather_micro_cue),
        "segments": {
            "opening": opening,
            "practical_briefing": practical_briefing,
            "calendar_today": "",
            "reflective_grounding": reflective_grounding,
            "meditative_close": meditative_close,
        },
    }


def _assemble_daily_feed_script(segments: dict) -> tuple[str, list[str]]:
    ordered_ids = ["opening", "practical_briefing", "calendar_today", "reflective_grounding", "meditative_close"]
    parts = []
    used = []
    for segment_id in ordered_ids:
        text = _safe_string((segments or {}).get(segment_id))
        if not text:
            continue
        parts.append(text)
        used.append(segment_id)
    return "\n\n".join(parts).strip(), used


def _daily_feed_section_text(segments: dict, segment_ids: list[str]) -> str:
    parts = []
    for segment_id in segment_ids:
        text = _safe_string((segments or {}).get(segment_id))
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _build_daily_feed_episode(uid: str, user_data: dict, now_utc: datetime | None = None, force: bool = False) -> dict:
    now_utc = now_utc if now_utc is not None else datetime.now(timezone.utc)
    local_now = _daily_feed_local_now(user_data, now_utc)
    episode_id = _daily_feed_date_key(user_data, now_utc)
    existing = _daily_feed_episode_ref(uid, episode_id).get()
    if existing.exists and not force:
        payload = existing.to_dict() or {}
        payload["id"] = existing.id
        return payload

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    notes = _load_recent_feed_notes(uid)
    if not notes:
        raise RuntimeError("No reflections available to generate a daily feed episode")

    weather_context = None
    weather_diagnostics = {
        "available": False,
        "source": "",
        "unavailable_reason": "",
    }
    try:
        weather_context, weather_cache_updates, weather_diagnostics = load_weather_context(user_data)
        if weather_cache_updates:
            _get_db().collection("users").document(uid).set(weather_cache_updates, merge=True)
            user_data = {**user_data, **weather_cache_updates}
    except Exception as exc:
        print(f"[{uid}] Weather enrichment unavailable: {exc}")
        weather_context = None
        weather_diagnostics = {
            "available": False,
            "source": "",
            "unavailable_reason": "unexpected_weather_exception",
        }

    recent_notes_available = _daily_feed_has_recent_reflection(notes, local_now)
    episode_type = "standard" if recent_notes_available else "fallback"

    def _save_episode_from_result(result: dict, selected_type: str, weather_applied: bool = False) -> dict:
        time_anchor = _safe_string(result.get("time_anchor"))
        continuity_anchor = _safe_string(result.get("continuity_anchor"))
        if not time_anchor:
            time_anchor = _daily_feed_present_day_anchor(local_now)
        if not continuity_anchor:
            continuity_anchor = _daily_feed_continuity_anchor(notes)
        if selected_type == "fallback" and (not time_anchor or not continuity_anchor):
            raise RuntimeError("Fallback episode missing required anchors")
        segments = result.get("segments") if isinstance(result.get("segments"), dict) else {}
        script_text, segments_used = _assemble_daily_feed_script(segments)
        if not script_text:
            raise RuntimeError("Daily feed script is empty")
        title = _safe_string(result.get("title")) or f"{_format_natural_date(episode_id)} — Daily Memnon briefing"
        description = _safe_string(result.get("description")) or "A grounded daily briefing from Memnon."
        voice = _normalize_narration_voice(user_data.get("narration_voice"))
        professional_text = _daily_feed_section_text(segments, ["opening", "practical_briefing", "calendar_today"])
        reflective_text = _daily_feed_section_text(segments, ["reflective_grounding", "meditative_close"])
        audio_mix_meta = {
            "professional_music_track": "",
            "reflective_music_track": "",
            "used_music_beds": False,
            "mix_fallback_reason": "",
        }
        try:
            audio_bytes, mixed_meta = synthesize_daily_brief_bytes(
                professional_text=professional_text,
                reflective_text=reflective_text,
                voice=voice,
            )
            audio_mix_meta.update(mixed_meta or {})
        except Exception as exc:
            print(f"[{uid}] Daily brief music mix unavailable, falling back to spoken audio only: {exc}")
            audio_mix_meta["mix_fallback_reason"] = str(exc)[:240]
            audio_bytes = synthesize_reflection_bytes(script_text, voice=voice)
        storage_path = _upload_daily_feed_audio(uid, episode_id, audio_bytes)
        duration_seconds = _estimate_audio_duration_seconds(script_text)
        episode_payload = {
            "date_key": episode_id,
            "published_at": firestore.SERVER_TIMESTAMP,
            "episode_type": selected_type,
            "title": title[:120],
            "description": description[:500],
            "audio_storage_path": storage_path,
            "audio_size_bytes": len(audio_bytes),
            "duration_seconds": duration_seconds,
            "segments_used": segments_used,
            "context_sources_used": ["reflection_history"] + (["weather"] if weather_applied else []),
            "script_text": script_text,
            "script_segments": {key: _safe_string(value) for key, value in (segments or {}).items() if _safe_string(value)},
            "audio_mix_meta": audio_mix_meta,
            "script_meta": {
                "has_time_anchor": bool(time_anchor),
                "has_continuity_anchor": bool(continuity_anchor),
                "time_anchor": time_anchor[:240],
                "continuity_anchor": continuity_anchor[:240],
            },
            "generation_meta": _build_daily_feed_generation_meta(
                recent_notes_available=recent_notes_available,
                generation_mode=selected_type,
                weather_context=weather_context,
                weather_diagnostics=weather_diagnostics,
                weather_applied=weather_applied,
            ),
        }
        _daily_feed_episode_ref(uid, episode_id).set(episode_payload, merge=True)
        _get_db().collection("users").document(uid).set({
            "daily_feed_last_generated_for": episode_id,
            "daily_feed_last_generated_at": firestore.SERVER_TIMESTAMP,
            "daily_feed_last_published_episode_id": episode_id,
            "daily_feed_last_error": firestore.DELETE_FIELD,
            "daily_feed_last_error_at": firestore.DELETE_FIELD,
        }, merge=True)
        _log_usage_event(uid, "generated_daily_feed_episode", {
            "episode_type": selected_type,
            "date_key": episode_id,
            "segments_used": segments_used,
            "forced": force,
        })
        saved = _daily_feed_episode_ref(uid, episode_id).get().to_dict() or episode_payload
        saved["id"] = episode_id
        return saved

    def _generate_deterministic_fallback() -> dict:
        result = _build_deterministic_daily_feed_result(
            user_data,
            notes,
            local_now,
            episode_type="fallback",
            weather_context=weather_context,
        )
        return _save_episode_from_result(
            result,
            "fallback",
            weather_applied=_coerce_bool(result.get("_weather_applied"), False),
        )

    def _generate_for_type(selected_type: str) -> dict:
        if selected_type == "fallback":
            return _generate_deterministic_fallback()
        prompt = _build_daily_feed_prompt(
            user_data,
            notes,
            selected_type,
            local_now,
            weather_context=weather_context,
        )
        result = _summarize(
            prompt,
            api_key,
            json_mode=True,
            timeout_seconds=120,
            max_output_tokens=700,
        )
        segments = result.get("segments") if isinstance(result.get("segments"), dict) else {}
        original_opening = _safe_string(segments.get("opening"))
        weather_applied = False
        if weather_context and _coerce_bool(weather_context.get("should_surface"), False) and original_opening:
            try:
                rewritten_opening, weather_applied = _rewrite_opening_with_weather_cue(
                    user_data,
                    original_opening,
                    weather_context,
                    api_key,
                )
                segments["opening"] = rewritten_opening
                result["segments"] = segments
            except Exception as exc:
                print(f"[{uid}] Opening weather cue unavailable: {exc}")
                if not _safe_string(weather_context.get("omission_reason")):
                    weather_context["omission_reason"] = "rewrite_failed"
        result["_weather_applied"] = weather_applied
        return _save_episode_from_result(result, selected_type, weather_applied=weather_applied)

    try:
        return _generate_for_type(episode_type)
    except Exception as exc:
        print(f"[{uid}] Daily feed {episode_type} generation failed: {exc}")
        if episode_type != "fallback":
            fallback_result = _generate_for_type("fallback")
            _log_usage_event(uid, "generated_daily_feed_fallback_after_failure", {
                "date_key": episode_id,
                "original_episode_type": episode_type,
            })
            return fallback_result
        raise


def _render_daily_feed_rss(user_data: dict, token: str, episodes: list[dict]) -> str:
    preferred_name = _safe_string(user_data.get("preferred_name")) or "Memnon listener"
    show_title = xml_escape(f"Memnon Daily — {preferred_name}")
    show_description = xml_escape("A private daily grounded briefing generated by Memnon.")
    feed_link = xml_escape(_daily_feed_url_for_token(token))
    items = []
    for episode in episodes:
        episode_id = _safe_string(episode.get("id") or episode.get("date_key"))
        if not episode_id:
            continue
        title = xml_escape(_safe_string(episode.get("title")) or episode_id)
        description = xml_escape(_safe_string(episode.get("description")) or "Daily briefing")
        enclosure_url = xml_escape(_daily_feed_audio_url(token, episode_id))
        pub_dt = _coerce_datetime(episode.get("published_at")) or _coerce_datetime(episode.get("date_key")) or datetime.utcnow()
        pub_rfc2822 = pub_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        length = _safe_int(episode.get("audio_size_bytes"), 0)
        guid = xml_escape(f"{token}:{episode_id}")
        items.append(
            f"<item><title>{title}</title><description>{description}</description>"
            f"<enclosure url=\"{enclosure_url}\" length=\"{length}\" type=\"audio/mpeg\" />"
            f"<guid isPermaLink=\"false\">{guid}</guid><pubDate>{pub_rfc2822}</pubDate></item>"
        )
    item_block = "".join(items)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<rss version=\"2.0\"><channel>"
        f"<title>{show_title}</title>"
        f"<description>{show_description}</description>"
        f"<link>{feed_link}</link>"
        f"<lastBuildDate>{datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')}</lastBuildDate>"
        f"{item_block}"
        "</channel></rss>"
    )


def _is_teacher_profession(user_data: dict) -> bool:
    profession = (user_data.get("profession") or "").lower().strip()
    return (
        not profession
        or "teach" in profession
        or "educat" in profession
        or "instructor" in profession
        or "professor" in profession
    )


def _reflection_user_data(user_data: dict, include_teaching_context: bool = True) -> dict:
    reflection_data = dict(user_data or {})
    reflection_data["include_teaching_context"] = include_teaching_context
    if include_teaching_context:
        return reflection_data

    reflection_data["lane"] = "reflect"
    reflection_data["profession"] = "personal reflection"
    reflection_data["grade_levels"] = []
    reflection_data["subjects"] = ""
    reflection_data["school_state"] = ""
    reflection_data["school_name"] = ""
    reflection_data["state_standards"] = []
    return reflection_data


def _build_complete_reflection_prompt(
    transcript: str,
    user_data: dict,
    practical_result: dict,
    grounded_result: dict,
    sources_used: list[dict],
) -> str:
    preferred_name = (user_data.get("preferred_name") or user_data.get("name") or "the teacher").strip()
    tasks_context_summary = (user_data.get("tasks_context_summary") or "").strip()
    history_context_summary = (user_data.get("history_context_summary") or "").strip()
    tasks_block = (
        f"\nRelevant current obligations:\n{tasks_context_summary}\n"
        if tasks_context_summary else ""
    )
    history_block = (
        f"\nRelevant prior reflection context:\n{history_context_summary}\n"
        if history_context_summary else ""
    )
    sources_block = "\n\n".join(
        f'{source.get("author", "")} ({source.get("ref", "")}): "{source.get("excerpt", "")}"'
        for source in sources_used
    ) if sources_used else "(no guiding voices selected)"

    payload = json.dumps({
        "practical": practical_result,
        "grounded": grounded_result,
    }, ensure_ascii=False)

    return _append_research_prompt_guidance(f"""You are integrating multiple perspectives into one grounded reflection for {preferred_name}.

The goal is not to flatten the perspectives. Hold them in conversation and produce one coherent return that helps {preferred_name} feel both supported and grounded.

Original transcript:
---
{transcript}
---
{tasks_block}
{history_block}
Perspective outputs:
{payload}

Guiding voice sources referenced in the grounded perspective:
{sources_block}

Respond with strict JSON only:
{{
  "title": "short specific title (5–8 words)",
  "summary": "3–5 sentences. Integrate the practical and grounded perspectives into one coherent reflection.",
  "insight": "One concise line naming the deepest tension, reframe, or pattern worth carrying forward.",
  "action_items": [
    "One practical next step",
    "Optional second step if it clearly matters"
  ],
  "suggested_tags": ["up to 5 lowercase tags"],
  "influenced_by": [
    {{
      "source_id": "exact source_id from the grounded perspective sources above",
      "because": "one sentence explaining why this source still matters in the integrated reflection"
    }}
  ]
}}

Rules:
- Return JSON only
- Preserve productive tension between perspectives when it matters
- If the current reflection revises, resists, or deepens an earlier framing, name that clearly
- If prior and current perspectives come into meaningful agreement, note that without flattening difference
- Do not introduce new facts
- The practical perspective should remain concrete
- The grounded perspective should deepen, not overwrite, the practical one
- If current obligations are included, use them lightly
- If no guiding voice truly matters, return an empty influenced_by array
""", "complete")


# ── pipeline ──────────────────────────────────────────────────────────────────

def _transcribe(audio_bytes: bytes, filename: str, api_key: str) -> str:
    return transcribe_audio_bytes(audio_bytes, filename, api_key)


def _summarize(
    prompt: str,
    api_key: str,
    json_mode: bool = False,
    timeout_seconds: int = 60,
    max_output_tokens: int | None = None,
) -> dict:
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    if max_output_tokens is not None:
        body["max_tokens"] = max(64, int(max_output_tokens))
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        raw = json.loads(resp.read())["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


NOTE_TEMPLATE = """\
---
title: {title}
date: {date}
lane: {lane}
tags: [{tags}]
{influenced_by_yaml}---

## Summary

{summary}

{extra}
"""


def _render_influenced_by_yaml(sources_used: list) -> str:
    """Render sources_used as YAML frontmatter block."""
    if not sources_used:
        return ""
    lines = ["influenced_by:"]
    for s in sources_used:
        lines.append(f'  - source_id: {s.get("source_id", "")}')
        lines.append(f'    author: "{s.get("author", "")}"')
        lines.append(f'    work: "{s.get("work", "")}"')
        lines.append(f'    ref: "{s.get("ref", "")}"')
        because = s.get("because", "")
        if because:
            lines.append(f'    because: "{because}"')
    return "\n".join(lines) + "\n"


def _build_history_source_text(
    ai: dict,
    sources_used: list[dict],
    participant_response_summary: str = "",
) -> str:
    parts = [
        (ai.get("title") or "").strip(),
        (ai.get("summary") or "").strip(),
        (ai.get("insight") or "").strip(),
    ]
    action_items = [str(item).strip() for item in (ai.get("action_items") or []) if str(item).strip()]
    if action_items:
        parts.append("Action items: " + "; ".join(action_items[:3]))
    voices = [item.get("author", "").strip() for item in (sources_used or []) if item.get("author")]
    if voices:
        parts.append("Voices: " + ", ".join(voices[:5]))
    if participant_response_summary:
        parts.append("Teacher follow-up: " + participant_response_summary.strip())
    return "\n".join(part for part in parts if part)


def _render_note(lane: str, ai: dict, transcript: str, filename: str,
                 sources_used: list | None = None) -> str:
    extra = []
    if ai.get("insight"):
        extra += ["## Insight", "", ai["insight"], ""]
    if ai.get("concerns") and ai["concerns"] not in (None, "null"):
        extra += ["## Note", "", ai["concerns"], ""]
    if ai.get("best_practice"):
        extra += ["## Best Practice", "", ai["best_practice"], ""]
    if ai.get("action_items"):
        extra += ["## Action Items", ""] + [f"- {i}" for i in ai["action_items"]] + [""]

    # Merge model-generated "because" into sources_used
    merged_sources = []
    if sources_used:
        model_influenced = {
            item.get("source_id"): item.get("because", "")
            for item in ai.get("influenced_by", [])
        }
        for s in sources_used:
            sid = s.get("source_id", "")
            merged_sources.append({**s, "because": model_influenced.get(sid, "")})

    return NOTE_TEMPLATE.format(
        title=ai.get("title", Path(filename).stem),
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        lane=lane,
        tags=", ".join(ai.get("suggested_tags", [])),
        summary=ai.get("summary", ""),
        extra="\n".join(extra) + ("\n" if extra else ""),
        transcript=transcript,
        influenced_by_yaml=_render_influenced_by_yaml(merged_sources),
    )


def _store_note_metadata(
    uid: str,
    ai: dict,
    sources_used: list,
    note_name: str,
    transcript: str,
    reflection_style: str,
    include_teaching_context: bool = True,
    embedding_v1: list[float] | None = None,
    embedding_meta: dict | None = None,
    history_source_text: str = "",
) -> None:
    """Store recent note metadata in Firestore for dashboard display (keep last 10)."""
    try:
        note_meta = {
            "title":        ai.get("title", note_name),
            "summary":      ai.get("summary", "")[:300],
            "date":         datetime.now().strftime("%Y-%m-%d"),
            "created_at":   firestore.SERVER_TIMESTAMP,
            "note_name":    note_name,
            "influenced_by": sources_used or [],
            "reflection_style": reflection_style,
            "include_teaching_context": include_teaching_context,
            "insight": ai.get("insight", "")[:240],
            "action_items": (ai.get("action_items") or [])[:3],
            "suggested_tags": (ai.get("suggested_tags") or [])[:8],
            "themes": sorted(list(extract_themes(transcript)))[:8],
            "voice_labels": [item.get("author", "") for item in (sources_used or []) if item.get("author")][:5],
            "history_source_text": history_source_text or _build_history_source_text(ai, sources_used),
        }
        if embedding_v1:
            note_meta["embedding_v1"] = embedding_v1
            note_meta["embedding_model"] = (embedding_meta or {}).get("model") or EMBEDDING_MODEL
            note_meta["embedding_provider"] = (embedding_meta or {}).get("provider") or EMBEDDING_PROVIDER
            note_meta["embedding_dim"] = int((embedding_meta or {}).get("dimensions") or len(embedding_v1))
            note_meta["embedding_version"] = "v1"
            note_meta["embedding_created_at"] = firestore.SERVER_TIMESTAMP
        # Use a subcollection for notes — one doc per note
        _get_db().collection("users").document(uid)\
                 .collection("notes").add(note_meta)
    except Exception as exc:
        print(f"[{uid}] Warning: could not store note metadata: {exc}")


CALLBACK_CUE_PHRASES = (
    "last time",
    "previously",
    "before",
    "earlier",
    "you said",
    "you mentioned",
    "i don't agree",
    "i disagree",
    "i still think",
    "you framed it as",
    "that perspective",
    "that voice",
)


def _tokenize_history_text(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z]{3,}", (text or "").lower())
        if token not in {"that", "this", "with", "from", "they", "have", "been", "were", "them", "their", "about"}
    }


def _has_callback_cue(transcript: str) -> bool:
    lowered = (transcript or "").lower()
    return any(phrase in lowered for phrase in CALLBACK_CUE_PHRASES)


def _note_history_terms(note: dict) -> set[str]:
    terms = set()
    for field in ("title", "summary", "insight", "participant_response_summary", "participant_response_excerpt"):
        terms.update(_tokenize_history_text(note.get(field, "")))
    for item in note.get("themes", []) or []:
        terms.update(_tokenize_history_text(item))
    for item in note.get("voice_labels", []) or []:
        terms.update(_tokenize_history_text(item))
    return terms


def _build_history_note_line(note: dict) -> str:
    parts = []
    date = (note.get("date") or "").strip()
    style = _normalize_reflection_style(note.get("reflection_style"))
    title = (note.get("title") or "Untitled").strip()
    if date:
        parts.append(date)
    parts.append(style.title())
    header = " | ".join(parts) + f" | {title}"
    lines = [header]
    summary = (note.get("summary") or "").strip()
    if summary:
        lines.append(f"summary: {summary}")
    insight = (note.get("insight") or "").strip()
    if insight:
        lines.append(f"insight: {insight}")
    voices = note.get("voice_labels") or []
    if voices:
        lines.append(f"voices: {', '.join(voices[:3])}")
    themes = note.get("themes") or []
    if themes:
        lines.append(f"themes: {', '.join(themes[:5])}")
    response_count = int(note.get("participant_response_count") or 0)
    if response_count:
        lines.append(f"user replies: {response_count}")
    response_summary = (note.get("participant_response_summary") or note.get("participant_response_excerpt") or "").strip()
    if response_summary:
        lines.append(f"latest reply summary: {response_summary}")
    return "\n".join(lines)


def _summarize_participant_response(response_text: str, api_key: str) -> str:
    cleaned_response = re.sub(r"\s+", " ", (response_text or "")).strip()
    if not cleaned_response:
        return ""
    if not api_key:
        return "Teacher added follow-up context on this reflection."

    try:
        result = _summarize(
            f"""You are creating privacy-preserving memory for a teacher reflection app.

Paraphrase the teacher's follow-up reply as a short summary for future context.
Return strict JSON only:
{{"summary":"one sentence, paraphrased, no quotes, no copied phrases longer than 3 words"}}

Rules:
- Do not quote the user verbatim.
- Avoid names or uniquely identifying details unless essential to meaning.
- Keep it under 180 characters.
- Preserve commitments, clarifications, or changed perspective when present.

Teacher reply:
---
{cleaned_response}
---
""",
            api_key,
        )
        summary = re.sub(r"\s+", " ", str(result.get("summary", ""))).strip()
        if summary:
            return summary[:180]
    except Exception:
        pass

    return "Teacher added follow-up context on this reflection."


def _note_has_embedding(note: dict) -> bool:
    embedding = note.get("embedding_v1") or []
    return isinstance(embedding, list) and bool(embedding)


def _summarize_note_embedding_coverage(uid: str, sample_limit: int = 5) -> dict:
    notes_ref = _get_db().collection("users").document(uid).collection("notes")
    try:
        docs = list(notes_ref.stream())
    except Exception as exc:
        return {
            "error": str(exc),
            "total_notes": 0,
            "embedded_notes": 0,
            "missing_embeddings": 0,
            "coverage_ratio": 0.0,
            "sample_missing": [],
        }

    total_notes = 0
    embedded_notes = 0
    missing_metadata_notes = 0
    sample_missing: list[dict] = []

    for doc in docs:
        if not doc.exists:
            continue
        total_notes += 1
        note = doc.to_dict() or {}
        has_embedding = _note_has_embedding(note)
        if has_embedding:
            embedded_notes += 1
        if has_embedding and not note.get("embedding_model"):
            missing_metadata_notes += 1
        if (not has_embedding or not note.get("embedding_model")) and len(sample_missing) < sample_limit:
            sample_missing.append({
                "id": doc.id,
                "title": note.get("title", "Untitled"),
                "date": note.get("date", ""),
                "has_embedding": has_embedding,
                "has_embedding_model": bool(note.get("embedding_model")),
            })

    missing_embeddings = max(total_notes - embedded_notes, 0)
    coverage_ratio = round((embedded_notes / total_notes), 4) if total_notes else 0.0
    return {
        "total_notes": total_notes,
        "embedded_notes": embedded_notes,
        "missing_embeddings": missing_embeddings,
        "missing_metadata_notes": missing_metadata_notes,
        "coverage_ratio": coverage_ratio,
        "sample_missing": sample_missing,
    }


def _load_relevant_reflection_history(
    uid: str,
    transcript: str,
    max_items: int = 3,
    hf_api_key: str = "",
) -> list[dict]:
    try:
        notes_ref = _get_db().collection("users").document(uid).collection("notes")
        docs = []
        seen_ids: set[str] = set()

        for field_name in ("created_at", "date"):
            try:
                field_docs = list(
                    notes_ref.order_by(field_name, direction=firestore.Query.DESCENDING).limit(8).stream()
                )
            except Exception:
                continue
            for doc in field_docs:
                if not doc.exists or doc.id in seen_ids:
                    continue
                seen_ids.add(doc.id)
                docs.append(doc)
    except Exception as exc:
        print(f"[{uid}] Warning: could not load reflection history: {exc}")
        return []

    entries = [doc.to_dict() for doc in docs if doc.exists]
    if not entries:
        return []

    current_terms = _tokenize_history_text(transcript)
    current_themes = extract_themes(transcript)
    callback = _has_callback_cue(transcript)
    query_embedding = embed_text(transcript, hf_api_key) if hf_api_key else []
    candidates: list[dict] = []

    for idx, note in enumerate(entries):
        note_terms = _note_history_terms(note)
        note_themes = set(note.get("themes") or [])
        base_score = float(max(0, 8 - idx))
        base_score += float(len(current_terms & note_terms))
        base_score += float(4 * len(current_themes & note_themes))
        if callback and note_terms:
            base_score += 3.0
        if note.get("voice_labels"):
            base_score += 1.0
        candidates.append({
            **note,
            "base_score": base_score,
            "embedding_v1": note.get("embedding_v1") or [],
        })

    ranked = rerank_candidates(query_embedding, candidates)
    chosen = [note for note in ranked if float(note.get("base_score", 0)) > 0][:max_items]
    if not chosen:
        chosen = entries[: min(2, len(entries))]
    return chosen


def _history_context_summary(uid: str, transcript: str, hf_api_key: str = "") -> tuple[list[dict], str]:
    notes = _load_relevant_reflection_history(uid, transcript, hf_api_key=hf_api_key)
    if not notes:
        return [], ""
    lines = [
        "The teacher may be continuing, revising, or disagreeing with earlier reflection framings."
    ]
    for note in notes:
        lines.append(f"- {_build_history_note_line(note)}")
    if _has_callback_cue(transcript):
        lines.append(
            "The current transcript explicitly seems to reference earlier reflections or prior voice framings."
        )
    return notes, "\n".join(lines)


def _record_source_usage(uid: str, sources_used: list) -> None:
    """Increment guide_usage and passage_usage counters in Firestore."""
    try:
        from google.cloud.firestore_v1 import Increment
        updates = {}
        for s in sources_used:
            sid = s.get("source_id", "")
            # Derive guide_id from source_id prefix (e.g. "ma_4_3" → "marcus_aurelius")
            # We store it directly in sources_used for reliability
            author = s.get("author", "unknown").lower().replace(" ", "_")
            if sid:
                updates[f"passage_usage.{sid}"] = Increment(1)
            updates[f"guide_usage.{author}"] = Increment(1)
        if updates:
            _get_db().collection("users").document(uid).update(updates)
    except Exception as exc:
        print(f"[{uid}] Warning: could not record source usage: {exc}")


def _build_grounded_reflection_script(
    transcript: str,
    ai: dict,
    sources_used: list[dict],
    user_data: dict,
    api_key: str,
) -> str:
    """Turn the grounded note into a spoken reflection script."""
    preferred_name = (user_data.get("preferred_name") or user_data.get("name") or "the teacher").strip()
    spoken_name = (user_data.get("spoken_name") or preferred_name).strip()
    preferred_pronouns = (user_data.get("preferred_pronouns") or "").strip()
    grades = ", ".join(user_data.get("grade_levels") or [])
    subjects = user_data.get("subjects") or ""
    context_bits = []
    if subjects:
        context_bits.append(f"subjects: {subjects}")
    if grades:
        context_bits.append(f"grades: {grades}")
    school_state = user_data.get("school_state") or ""
    if school_state:
        context_bits.append(f"state: {school_state}")
    if preferred_pronouns:
        context_bits.append(f"pronouns: {preferred_pronouns}")
    context_line = "; ".join(context_bits) if context_bits else "teaching context not specified"
    tasks_context_summary = (user_data.get("tasks_context_summary") or "").strip()
    tasks_context_block = (
        f"\nRelevant current obligations:\n{tasks_context_summary}\n"
        if tasks_context_summary else ""
    )

    source_lines = []
    for source in sources_used[:3]:
        excerpt = source.get("excerpt", "").strip()
        because = ""
        for item in ai.get("influenced_by", []):
            if item.get("source_id") == source.get("source_id"):
                because = item.get("because", "").strip()
                break
        detail = f'{source.get("author", "")} ({source.get("ref", "")}): "{excerpt}"'
        if because:
            detail += f"\nConnection: {because}"
        source_lines.append(detail)
    sources_block = "\n\n".join(source_lines) if source_lines else "(no guiding voices selected)"

    payload = json.dumps({
        "title": ai.get("title", ""),
        "summary": ai.get("summary", ""),
        "insight": ai.get("insight", ""),
        "best_practice": ai.get("best_practice", ""),
        "concerns": ai.get("concerns", ""),
        "action_items": ai.get("action_items", []),
    }, ensure_ascii=False)

    prompt = _append_research_prompt_guidance(f"""You are writing a spoken grounded reflection for {preferred_name}.

This script will be narrated back to {preferred_name} as audio.
It should feel thoughtful, warm, and concise, like a trusted instructional coach
helping them hear the day more clearly.

Person context: display name: {preferred_name}; spoken name for audio: {spoken_name}; {context_line}

Original transcript:
---
{transcript}
---

Structured reflection:
{payload}
{tasks_context_block}

Guiding voices actually selected for this reflection:
{sources_block}

Return strict JSON only:
{{
  "reflection_script": "A spoken script of 130 to 220 words. No bullet points. No greeting. No sign-off. Weave in the guiding voices naturally when they genuinely fit, but do not quote long passages."
}}

Rules:
- Sound natural when read aloud
- Begin with the lived classroom moment, then deepen it
- Use the guiding voices as grounding, not decoration
- If current obligations are included, use them only when they genuinely clarify what is weighing on {preferred_name}
- End with one clear line they can carry into tomorrow
- Refer to {preferred_name} by name where natural
- Respect these pronouns when needed: {preferred_pronouns or "use neutral phrasing if possible"}
- If the script uses their name, write it in the spoken form "{spoken_name}" so the narrator says it correctly
- Return JSON only
""", "script")

    result = _summarize(prompt, api_key)
    return (result.get("reflection_script") or "").strip()


def _generate_grounded_reflection_audio(script_text: str, stem: str, voice: str) -> bytes:
    """Synthesize the spoken reflection as MP3 bytes."""
    if not script_text.strip():
        raise ValueError("grounded reflection script is empty")
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem).strip("-") or "grounded-reflection"
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / f"{safe_stem}.mp3"
        synthesize_reflection_mp3(script_text, output_path, voice=voice)
        return output_path.read_bytes()


def _generate_reflection_result(
    transcript: str,
    user_data: dict,
    api_key: str,
) -> tuple[str, dict, list[dict]]:
    style = _normalize_reflection_style(user_data.get("reflection_style"))
    lane = user_data.get("lane", "professional")

    if style == "grounded":
        prompt, sources_used = reflect_prompt(transcript, user_data)
        return style, _summarize(_append_research_prompt_guidance(prompt, "grounded"), api_key), sources_used

    if style == "practical":
        if lane == "professional" and _is_teacher_profession(user_data):
            prompt = _append_research_prompt_guidance(teaching_practical_prompt(transcript, user_data), "teaching")
            return style, _summarize(prompt, api_key), []
        profession = (user_data.get("profession") or "professional").lower().strip() or "professional"
        prompt = _append_research_prompt_guidance(professional_prompt(
            transcript,
            profession,
            (user_data.get("tasks_context_summary") or "").strip(),
            (user_data.get("history_context_summary") or "").strip(),
        ), "practical")
        return style, _summarize(prompt, api_key), []

    practical_prompt = _append_research_prompt_guidance((
        teaching_practical_prompt(transcript, user_data)
        if lane == "professional" and _is_teacher_profession(user_data)
        else professional_prompt(
            transcript,
            ((user_data.get("profession") or "professional").lower().strip() or "professional"),
            (user_data.get("tasks_context_summary") or "").strip(),
            (user_data.get("history_context_summary") or "").strip(),
        )
    ), "teaching" if lane == "professional" and _is_teacher_profession(user_data) else "practical")
    practical_result = _summarize(practical_prompt, api_key)
    grounded_prompt, sources_used = reflect_prompt(transcript, user_data)
    grounded_result = _summarize(_append_research_prompt_guidance(grounded_prompt, "grounded"), api_key)
    integration_prompt = _build_complete_reflection_prompt(
        transcript,
        user_data,
        practical_result,
        grounded_result,
        sources_used,
    )
    integrated_result = _summarize(integration_prompt, api_key)
    return style, integrated_result, sources_used


def _process_reflection_entry(
    service,
    uid: str,
    user_data: dict,
    transcript: str,
    api_key: str,
    source_filename: str,
    include_teaching_context: bool = True,
    source_audio_bytes: bytes | None = None,
    source_mime_type: str | None = None,
) -> dict:
    reflection_user_data = _reflection_user_data(user_data, include_teaching_context=include_teaching_context)
    task_context_items = _derive_task_context(
        transcript,
        _fetch_open_tasks_for_user(uid, reflection_user_data),
    )
    reflection_user_data["tasks_context_items"] = task_context_items
    reflection_user_data["tasks_context_summary"] = "\n".join(f"- {item}" for item in task_context_items)
    history_context_items, history_context_summary = _history_context_summary(
        uid,
        transcript,
        hf_api_key=HUGGING_FACE_API_KEY,
    )
    reflection_user_data["history_context_items"] = history_context_items
    reflection_user_data["history_context_summary"] = history_context_summary

    lane = reflection_user_data.get("lane", "professional")
    try:
        style_key, ai_result, sources_used = _generate_reflection_result(transcript, reflection_user_data, api_key)
    except Exception as exc:
        print(f"[{uid}] AI error on entry processing: {exc}")
        style_key = _normalize_reflection_style(reflection_user_data.get("reflection_style"))
        ai_result = {
            "title": Path(source_filename).stem,
            "summary": transcript[:300],
            "action_items": [],
            "suggested_tags": [],
            "influenced_by": [],
        }
        sources_used = []

    notes_id, _recordings_id, reflections_id = _ensure_user_output_folders(service, uid, user_data)
    note_name = (
        datetime.now().strftime("%Y-%m-%d") + " — " +
        ai_result.get("title", Path(source_filename).stem)[:60] + ".md"
    )
    note_md = _render_note(lane, ai_result, transcript, source_filename, sources_used)
    history_source_text = _build_history_source_text(ai_result, sources_used)
    embedding_result = embed_text_details(history_source_text, HUGGING_FACE_API_KEY) if HUGGING_FACE_API_KEY else {"vector": []}
    history_embedding = embedding_result.get("vector") or []
    if HUGGING_FACE_API_KEY and not history_embedding:
        print(f"[{uid}] Warning: history embedding missing for {note_name}")
    if sources_used:
        _record_source_usage(uid, sources_used)
    _store_note_metadata(
        uid,
        ai_result,
        sources_used,
        note_name,
        transcript,
        style_key,
        include_teaching_context=include_teaching_context,
        embedding_v1=history_embedding,
        embedding_meta=embedding_result,
        history_source_text=history_source_text,
    )

    media = MediaInMemoryUpload(note_md.encode(), mimetype="text/plain", resumable=False)
    service.files().create(
        body={"name": note_name, "parents": [notes_id]},
        media_body=media,
        fields="id",
    ).execute()

    recording_name = None

    reflection_name = None
    try:
        narration_voice = _normalize_narration_voice(user_data.get("narration_voice"))
        reflection_script = _build_grounded_reflection_script(
            transcript,
            ai_result,
            sources_used,
            reflection_user_data,
            api_key,
        )
        if reflection_script:
            reflection_bytes = _generate_grounded_reflection_audio(
                reflection_script,
                ai_result.get("title", Path(source_filename).stem),
                narration_voice,
            )
            reflection_title = ai_result.get("title", Path(source_filename).stem)[:60].strip() or "Grounded Reflection"
            reflection_name = f"{datetime.now().strftime('%Y-%m-%d')} — {reflection_title}.mp3"
            reflection_media = MediaInMemoryUpload(
                reflection_bytes,
                mimetype="audio/mpeg",
                resumable=False,
            )
            service.files().create(
                body={"name": reflection_name, "parents": [reflections_id]},
                media_body=reflection_media,
                fields="id,name",
            ).execute()
    except Exception as exc:
        print(f"[{uid}] Grounded reflection audio failed: {exc}")

    return {
        "note_name": note_name,
        "recording_name": recording_name,
        "reflection_audio": reflection_name,
        "style_key": style_key,
        "ai_result": ai_result,
    }


def _process_file(service, uid: str, user_data: dict, f: dict, inbox_id: str, notes_id: str):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    filename = f["name"]
    print(f"[{uid}] Processing: {filename}")

    # Download audio
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=f["id"]),
                             chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
    audio_bytes = buf.getvalue()

    if len(audio_bytes) < 4096:
        print(f"[{uid}] File too small, skipping")
        return

    transcript = _transcribe(audio_bytes, filename, api_key)
    if len(transcript.split()) < 3:
        print(f"[{uid}] Transcript too short, skipping")
        return

    user_data = dict(user_data)
    task_context_items = _derive_task_context(
        transcript,
        _fetch_open_tasks_for_user(uid, user_data),
    )
    user_data["tasks_context_items"] = task_context_items
    user_data["tasks_context_summary"] = "\n".join(f"- {item}" for item in task_context_items)
    history_context_items, history_context_summary = _history_context_summary(
        uid,
        transcript,
        hf_api_key=HUGGING_FACE_API_KEY,
    )
    user_data["history_context_items"] = history_context_items
    user_data["history_context_summary"] = history_context_summary

    lane = user_data.get("lane", "professional")
    try:
        style_key, ai_result, sources_used = _generate_reflection_result(transcript, user_data, api_key)
    except Exception as exc:
        print(f"[{uid}] AI error: {exc} — using fallback")
        style_key = _normalize_reflection_style(user_data.get("reflection_style"))
        ai_result = {"title": Path(filename).stem, "summary": transcript[:300],
                     "action_items": [], "suggested_tags": [], "influenced_by": []}
        sources_used = []

    note_md = _render_note(lane, ai_result, transcript, filename, sources_used)
    note_name = (datetime.now().strftime("%Y-%m-%d") + " — " +
                 ai_result.get("title", Path(filename).stem)[:60] + ".md")
    history_source_text = _build_history_source_text(ai_result, sources_used)
    embedding_result = embed_text_details(history_source_text, HUGGING_FACE_API_KEY) if HUGGING_FACE_API_KEY else {"vector": []}
    history_embedding = embedding_result.get("vector") or []
    if HUGGING_FACE_API_KEY and not history_embedding:
        print(f"[{uid}] Warning: history embedding missing for {note_name}")

    media = MediaInMemoryUpload(note_md.encode(), mimetype="text/plain", resumable=False)
    service.files().create(
        body={"name": note_name, "parents": [notes_id]},
        media_body=media, fields="id",
    ).execute()
    print(f"[{uid}] Note saved: {note_name}")

    # Track guide and passage usage counts in Firestore
    if sources_used:
        _record_source_usage(uid, sources_used)

    # Store recent note metadata in Firestore for dashboard display
    _store_note_metadata(
        uid,
        ai_result,
        sources_used,
        note_name,
        transcript,
        style_key,
        embedding_v1=history_embedding,
        embedding_meta=embedding_result,
        history_source_text=history_source_text,
    )

    processed_id = _find_or_create_folder(service, "processed", inbox_id)
    service.files().update(
        fileId=f["id"],
        addParents=processed_id, removeParents=inbox_id, fields="id",
    ).execute()


def _sweep_user(uid: str, user_data: dict):
    """
    Ensure the user's Drive folders exist.

    With drive.file scope, we can only access files this app created.
    Live capture processing now happens through the active app endpoints,
    not by polling Drive.
    """
    creds = _drive_creds(uid)
    if not creds:
        return

    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    inbox_id = user_data.get("inbox_folder_id")
    notes_id = user_data.get("notes_folder_id")
    updates = {}
    if not inbox_id:
        inbox_id = _find_or_create_folder(service, "memnon-inbox")
        updates["inbox_folder_id"] = inbox_id
    if not notes_id:
        notes_id = _find_or_create_folder(service, "memnon-notes")
        updates["notes_folder_id"] = notes_id
    if updates:
        _get_db().collection("users").document(uid).update(updates)
        print(f"[{uid}] Drive folders ensured: inbox={inbox_id} notes={notes_id}")


# ── Flask routes ───────────────────────────────────────────────────────────────

def _client_config() -> dict:
    """Return the parsed client secrets dict."""
    raw = os.environ.get("GOOGLE_CLIENT_SECRETS", "")
    if not raw:
        raise RuntimeError("GOOGLE_CLIENT_SECRETS env var not set")
    return json.loads(raw if raw.strip().startswith("{") else Path(raw).read_text())


@flask_app.route("/auth/start")
def auth_start():
    """Redirect user to Google — requests profile + Drive in one consent screen."""
    frontend_return_to = _safe_frontend_return_url(request.args.get("return_to"))
    try:
        cfg = _client_config()["web"]
        # Generate PKCE code verifier + challenge
        code_verifier  = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()

        # Generate state token for CSRF protection
        state = secrets.token_urlsafe(32)

        # Store both in Flask session for callback verification
        from flask import session
        session["frontend_return_to"] = frontend_return_to
        session["oauth_state"] = state
        session["code_verifier"] = code_verifier

        include_tasks = request.args.get("include_tasks") == "1"
        params = {
            "client_id":              cfg["client_id"],
            "redirect_uri":           REDIRECT_URI,
            "response_type":          "code",
            "scope":                  " ".join(_requested_google_scopes(include_tasks)),
            "access_type":            "offline",
            "state":                  state,
            "code_challenge":         code_challenge,
            "code_challenge_method":  "S256",
        }
        if request.args.get("force_consent") == "1":
            params["prompt"] = "consent"
        params = urllib.parse.urlencode(params)
        return redirect(f"https://accounts.google.com/o/oauth2/auth?{params}")
    except Exception as exc:
        print(f"OAuth start error: {exc}")
        return redirect(_append_query_params(frontend_return_to, {"error": "oauth_start_failed"}))


@flask_app.route("/auth/callback")
def auth_callback():
    """Receive tokens, create/update Firebase user, mint custom token, redirect."""
    from flask import session
    frontend_return_to = _safe_frontend_return_url(session.get("frontend_return_to"))
    try:
        code  = request.args.get("code")
        state = request.args.get("state")

        if not code:
            return redirect(_append_query_params(frontend_return_to, {"error": "missing_code"}))

        # Verify state to prevent CSRF
        if not state or state != session.get("oauth_state"):
            return redirect(_append_query_params(frontend_return_to, {"error": "invalid_state"}))

        code_verifier = session.pop("code_verifier", None)
        session.pop("oauth_state", None)
        session.pop("frontend_return_to", None)

        # Exchange code for tokens with PKCE verifier
        cfg = _client_config()["web"]
        import requests as http_requests
        token_payload = {
            "code":          code,
            "client_id":     cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        }
        if code_verifier:
            token_payload["code_verifier"] = code_verifier

        token_resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data=token_payload,
        ).json()

        if "error" in token_resp:
            raise RuntimeError(token_resp.get("error_description") or token_resp["error"])

        creds = Credentials(
            token=token_resp.get("access_token"),
            refresh_token=token_resp.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            scopes=token_resp.get("scope", "").split(),
        )

        # Ensure Firebase Admin is initialized before any fb_auth calls
        _get_db()

        # Get user profile from Google
        people = build("oauth2", "v2", credentials=creds)
        info = people.userinfo().get().execute()
        email = info["email"]
        name = info.get("name", "")
        google_id = info["id"]

        # Create or fetch Firebase user
        try:
            fb_user = fb_auth.get_user_by_email(email)
        except fb_auth.UserNotFoundError:
            fb_user = fb_auth.create_user(
                email=email,
                display_name=name,
                uid=f"google_{google_id}",
            )

        uid = fb_user.uid

        existing_doc = _get_db().collection("users").document(uid).get()
        existing_user = existing_doc.to_dict() if existing_doc.exists else {}
        merged_token = _merge_google_token(
            existing_user.get("google_drive_token"),
            json.loads(creds.to_json()),
        )
        tasks_connected = TASKS_READONLY_SCOPE in _scopes_from_token_data(merged_token)

        # Persist Drive tokens + user info
        _get_db().collection("users").document(uid).set({
            "email": email,
            "name": name,
            "google_drive_token": merged_token,
            "drive_connected": True,
            "google_tasks_connected": tasks_connected,
            "active": True,
        }, merge=True)

        # Mint a short-lived Firebase custom token for the frontend
        custom_token = fb_auth.create_custom_token(uid).decode("utf-8")

    except Exception as exc:
        print(f"OAuth callback error: {exc}")
        return redirect(_append_query_params(frontend_return_to, {"error": "oauth_failed"}))

    return redirect(_append_query_params(frontend_return_to, {"token": custom_token}))


@flask_app.route("/setup", methods=["POST"])
def save_setup():
    """Save lane + profession/tradition for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    user_ref = _get_db().collection("users").document(uid)
    existing_doc = user_ref.get()
    existing_user = existing_doc.to_dict() if existing_doc.exists else {}

    school_name = data.get("school_name", "")
    school_state = data.get("school_state", "")
    updates = {
        "lane":           data.get("lane", "professional"),
        "profession":     data.get("profession", "teacher"),
        "reflection_style": _normalize_reflection_style(data.get("reflection_style")),
        "preferred_name": data.get("preferred_name", ""),
        "spoken_name": data.get("spoken_name", ""),
        "preferred_pronouns": data.get("preferred_pronouns", ""),
        "narration_voice": _normalize_narration_voice(data.get("narration_voice")),
        "tradition":      data.get("tradition", "secular"),
        # Teaching-specific fields
        "grade_levels":    data.get("grade_levels", []),
        "subjects":        data.get("subjects", ""),
        "school_state":    school_state,
        "state_standards": data.get("state_standards", []),
        "school_name":     school_name,
        "school_district": data.get("school_district", ""),
        "school_city":     data.get("school_city", ""),
        "allow_anonymized_research": _coerce_bool(data.get("allow_anonymized_research"), False),
        # Reflect lane voices config
        "reflect_config":  data.get("reflect_config", {}),
        "dashboard_image": data.get("dashboard_image", {"kind": "preset", "preset": "lattice"}),
        "google_tasks_list_id": (data.get("google_tasks_list_id") or "").strip(),
        "google_tasks_list_name": (data.get("google_tasks_list_name") or "").strip(),
        "active": True,
    }
    if (
        str(existing_user.get("school_name") or "").strip() != str(school_name).strip()
        or str(existing_user.get("school_state") or "").strip() != str(school_state).strip()
    ):
        updates.update(clear_weather_cache_fields())
    user_ref.set(updates, merge=True)
    return jsonify({"ok": True})


@flask_app.route("/voice-preview", methods=["POST"])
def voice_preview():
    """Generate a short preview clip for a selected narration voice."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    voice = _normalize_narration_voice(data.get("voice"))
    text = (data.get("text") or VOICE_PREVIEW_TEXT).strip()
    if not text:
        text = VOICE_PREVIEW_TEXT

    try:
        audio_bytes = synthesize_reflection_bytes(text, voice=voice)
    except Exception as exc:
        print(f"[{uid}] Voice preview failed: {exc}")
        return jsonify({"error": "preview unavailable"}), 502

    return (
        audio_bytes,
        200,
        {
            "Content-Type": "audio/mpeg",
            "Cache-Control": "private, max-age=60",
        },
    )


@flask_app.route("/tasklists")
def list_tasklists():
    """Return available Google Tasks lists for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"error": "user not found"}), 404
    user_data = doc.to_dict()
    if not _user_tasks_connected(user_data):
        return jsonify({"error": "Tasks not connected", "needs_consent": True}), 403

    service = _tasks_service_for_user(uid)
    if not service:
        return jsonify({"error": "Google account not connected"}), 403

    try:
        result = service.tasklists().list(maxResults=100).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list task lists: {exc}")
        return jsonify({"error": "Could not load task lists"}), 502

    items = [
        {"id": item.get("id", ""), "title": item.get("title", "Untitled")}
        for item in result.get("items", []) if item.get("id")
    ]
    return jsonify({"items": items})


@flask_app.route("/tasks")
def list_tasks():
    """Return open tasks from the user's selected Google Tasks list."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"error": "user not found"}), 404
    user_data = doc.to_dict()
    if not _user_tasks_connected(user_data):
        return jsonify({"error": "Tasks not connected", "needs_consent": True}), 403

    tasklist_id = (user_data.get("google_tasks_list_id") or "").strip()
    if not tasklist_id:
        return jsonify({"items": [], "tasklist_configured": False, "tasks_web_url": GOOGLE_TASKS_WEB_URL})

    service = _tasks_service_for_user(uid)
    if not service:
        return jsonify({"error": "Google account not connected"}), 403

    max_results = request.args.get("limit", "5")
    try:
        limit_value = max(1, min(int(max_results), 20))
    except ValueError:
        limit_value = 5

    try:
        result = service.tasks().list(
            tasklist=tasklist_id,
            maxResults=limit_value,
            showCompleted=False,
            showHidden=False,
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list tasks: {exc}")
        return jsonify({"error": "Could not load tasks"}), 502

    items = []
    for item in result.get("items", []):
        if item.get("status") == "completed":
            continue
        items.append({
            "id": item.get("id", ""),
            "title": item.get("title", "").strip(),
            "due": item.get("due"),
            "notes": item.get("notes", "").strip(),
            "status": item.get("status", ""),
            "updated": item.get("updated"),
        })

    return jsonify({
        "items": items,
        "tasklist_configured": True,
        "tasklist_name": user_data.get("google_tasks_list_name", ""),
        "tasks_web_url": GOOGLE_TASKS_WEB_URL,
    })


@flask_app.route("/profile-images")
def list_profile_images():
    """Return recent Drive images accessible to the app for this user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    try:
        result = service.files().list(
            q="mimeType contains 'image/' and trashed=false",
            pageSize=24,
            orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,parents)",
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list profile images: {exc}")
        return jsonify({"error": "Could not load Drive images"}), 502

    return jsonify({
        "files": result.get("files", []),
    })


@flask_app.route("/profile-image/upload", methods=["POST"])
def upload_profile_image():
    """Upload a custom dashboard image to Drive and return its file metadata."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    if "image" not in request.files:
        return jsonify({"error": "missing image"}), 400

    f = request.files["image"]
    image_bytes = f.read()
    mime_type = (f.mimetype or "").strip().lower()
    filename = f.filename or f"dashboard-image-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"

    if not mime_type.startswith(IMAGE_MIME_PREFIX):
        return jsonify({"error": "unsupported image type"}), 400
    if not image_bytes:
        return jsonify({"error": "empty image"}), 400
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return jsonify({"error": "image too large"}), 400

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    folder_id = _find_or_create_media_folder(service)
    media = MediaInMemoryUpload(image_bytes, mimetype=mime_type, resumable=False)
    meta = {
        "name": filename,
        "parents": [folder_id],
    }
    try:
        created = service.files().create(
            body=meta,
            media_body=media,
            fields="id,name,mimeType,modifiedTime",
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not upload profile image: {exc}")
        return jsonify({"error": "image upload failed"}), 502

    return jsonify({"file": created})


@flask_app.route("/profile-image/<file_id>")
def get_profile_image(file_id: str):
    """Stream a Drive-backed dashboard image for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    try:
        meta = service.files().get(fileId=file_id, fields="id,name,mimeType").execute()
        mime_type = meta.get("mimeType", "")
        if not mime_type.startswith(IMAGE_MIME_PREFIX):
            return jsonify({"error": "not an image"}), 400

        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id), chunksize=4 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
    except Exception as exc:
        print(f"[{uid}] Could not fetch profile image {file_id}: {exc}")
        return jsonify({"error": "image unavailable"}), 404

    return (
        buf.getvalue(),
        200,
        {
            "Content-Type": mime_type,
            "Cache-Control": "private, max-age=300",
        },
    )


@flask_app.route("/recordings")
def list_recordings():
    """Return recent recordings saved by the app for this user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"files": []})
    user_data = doc.to_dict()

    recordings_id = user_data.get("recordings_folder_id")
    if not recordings_id:
        return jsonify({"files": []})

    try:
        result = service.files().list(
            q=f"'{recordings_id}' in parents and trashed=false",
            pageSize=8,
            orderBy="createdTime desc",
            fields="files(id,name,mimeType,createdTime,modifiedTime,size)",
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list recordings: {exc}")
        return jsonify({"error": "Could not load recordings"}), 502

    files = [f for f in result.get("files", []) if _is_audio(f)]
    return jsonify({"files": files})


@flask_app.route("/recording/<file_id>")
def get_recording(file_id: str):
    """Stream a Drive-backed recording for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    try:
        meta = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,parents",
        ).execute()
        if not _is_audio(meta):
            return jsonify({"error": "not a recording"}), 400

        buf = io.BytesIO()
        dl = MediaIoBaseDownload(
            buf,
            service.files().get_media(fileId=file_id),
            chunksize=4 * 1024 * 1024,
        )
        done = False
        while not done:
            _, done = dl.next_chunk()
    except Exception as exc:
        print(f"[{uid}] Could not fetch recording {file_id}: {exc}")
        return jsonify({"error": "recording unavailable"}), 404

    return (
        buf.getvalue(),
        200,
        {
            "Content-Type": meta.get("mimeType", "audio/webm"),
            "Cache-Control": "private, max-age=300",
        },
    )


@flask_app.route("/reflections")
def list_reflections():
    """Return recent grounded reflection audio files saved by the app for this user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"files": []})
    user_data = doc.to_dict()

    reflections_id = user_data.get("reflections_folder_id")
    if not reflections_id:
        return jsonify({"files": []})

    try:
        result = service.files().list(
            q=f"'{reflections_id}' in parents and trashed=false",
            pageSize=8,
            orderBy="createdTime desc",
            fields="files(id,name,mimeType,createdTime,modifiedTime,size)",
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list reflections: {exc}")
        return jsonify({"error": "Could not load reflections"}), 502

    files = [f for f in result.get("files", []) if _is_audio(f)]
    return jsonify({"files": files})


@flask_app.route("/reflection/<file_id>")
def get_reflection(file_id: str):
    """Stream a Drive-backed grounded reflection audio file for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    try:
        meta = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,parents",
        ).execute()
        if not _is_audio(meta):
            return jsonify({"error": "not a reflection"}), 400

        buf = io.BytesIO()
        dl = MediaIoBaseDownload(
            buf,
            service.files().get_media(fileId=file_id),
            chunksize=4 * 1024 * 1024,
        )
        done = False
        while not done:
            _, done = dl.next_chunk()
    except Exception as exc:
        print(f"[{uid}] Could not fetch reflection {file_id}: {exc}")
        return jsonify({"error": "reflection unavailable"}), 404

    return (
        buf.getvalue(),
        200,
        {
            "Content-Type": meta.get("mimeType", "audio/mpeg"),
            "Cache-Control": "private, max-age=300",
        },
    )


@flask_app.route("/upload", methods=["POST"])
def upload_audio():
    """Compatibility adapter into the workflows capture pipeline for audio uploads."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    if "file" in request.files:
        f = request.files["file"]
        audio_bytes = f.read()
        filename = f.filename or f"upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}.webm"
        upload_mime_type = f.mimetype or "audio/webm"
    else:
        audio_bytes = request.get_data()
        filename = request.headers.get("X-Filename",
                   f"upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}.webm")
        upload_mime_type = request.headers.get("Content-Type", "audio/webm")

    if not audio_bytes:
        return jsonify({"error": "audio file is empty"}), 400

    api_key = load_openai_api_key()
    if not api_key:
        return jsonify({"error": "server misconfigured"}), 500

    include_teaching_context = _coerce_bool(request.form.get("include_teaching_context"), True)

    try:
        transcript = transcribe_audio_bytes(audio_bytes, filename, api_key).strip()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return jsonify({"error": "transcription service authentication failed"}), 502
        return jsonify({"error": f"transcription failed ({exc.code})"}), 502
    except Exception as exc:
        print(f"[{uid}] Transcription error on upload: {exc}")
        return jsonify({"error": "transcription failed"}), 502

    if len(transcript.split()) < 3:
        return jsonify({"error": "transcript too short"}), 400

    capture_id = f"cap-{secrets.token_hex(6)}"
    source_metadata = _maybe_archive_workflow_voice_audio(
        uid,
        capture_id,
        audio_bytes,
        filename,
        upload_mime_type,
    )
    record = _workflow_service().create_text_capture(
        uid=uid,
        capture_id=capture_id,
        source_text=transcript,
        context_hint="",
        input_type="voice",
        include_teaching_context=include_teaching_context,
        source_metadata=source_metadata,
    )

    _log_usage_event(uid, "captured_reflection_audio", {
        "include_teaching_context": include_teaching_context,
        "input_type": "voice",
        "source": "record",
        "upload_mime_type": upload_mime_type,
    })
    return _workflow_capture_compat_response(record)


@flask_app.route("/reflection-response", methods=["POST"])
def save_reflection_response():
    """Attach a user's reply to an existing reflection note for future context."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    note_id = (data.get("note_id") or "").strip()
    response_text = re.sub(r"\s+", " ", (data.get("text") or "")).strip()

    if not note_id:
        return jsonify({"error": "missing note_id"}), 400
    if len(response_text) < 6:
        return jsonify({"error": "response too short"}), 400
    if len(response_text) > 2000:
        return jsonify({"error": "response too long"}), 400

    note_ref = _get_db().collection("users").document(uid).collection("notes").document(note_id)
    snap = note_ref.get()
    if not snap.exists:
        return jsonify({"error": "note not found"}), 404

    note_data = snap.to_dict() or {}
    api_key = os.environ.get("OPENAI_API_KEY", "")
    response_summary = _summarize_participant_response(response_text, api_key)

    existing_summaries = note_data.get("participant_response_summaries") or []
    trimmed_summaries = existing_summaries[-5:] if isinstance(existing_summaries, list) else []
    trimmed_summaries.append({
        "summary": response_summary,
        "created_at": firestore.SERVER_TIMESTAMP,
    })

    updated_history_source_text = _build_history_source_text(
        {
            "title": note_data.get("title", ""),
            "summary": note_data.get("summary", ""),
            "insight": note_data.get("insight", ""),
            "action_items": note_data.get("action_items", []),
        },
        note_data.get("influenced_by") or [],
        participant_response_summary=response_summary,
    )
    embedding_result = embed_text_details(updated_history_source_text, HUGGING_FACE_API_KEY) if HUGGING_FACE_API_KEY else {"vector": []}
    embedding_v1 = embedding_result.get("vector") or []

    update_payload = {
        "participant_response_summaries": trimmed_summaries,
        "participant_response_count": int(note_data.get("participant_response_count") or 0) + 1,
        "participant_response_summary": response_summary,
        "participant_response_updated_at": firestore.SERVER_TIMESTAMP,
        "history_source_text": updated_history_source_text,
    }
    if embedding_v1:
        update_payload["embedding_v1"] = embedding_v1
        update_payload["embedding_model"] = embedding_result.get("model") or EMBEDDING_MODEL
        update_payload["embedding_provider"] = embedding_result.get("provider") or EMBEDDING_PROVIDER
        update_payload["embedding_dim"] = int(embedding_result.get("dimensions") or len(embedding_v1))
        update_payload["embedding_version"] = "v1"
        update_payload["embedding_created_at"] = firestore.SERVER_TIMESTAMP

    note_ref.update(update_payload)
    _log_usage_event(uid, "saved_reflection_reply", {
        "note_id": note_id,
        "response_length": len(response_text),
    })
    return jsonify({
        "ok": True,
        "participant_response_count": update_payload["participant_response_count"],
        "participant_response_summary": update_payload["participant_response_summary"],
    })


@flask_app.route("/text-reflection", methods=["POST"])
def create_text_reflection():
    """Compatibility adapter into the workflows capture pipeline for text capture."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    raw_text = (data.get("text") or "").strip()
    include_teaching_context = _coerce_bool(data.get("include_teaching_context"), True)
    transcript = re.sub(r"\n{3,}", "\n\n", raw_text)
    if len(transcript.split()) < 3:
        return jsonify({"error": "text too short"}), 400

    record = _workflow_service().create_text_capture(
        uid=uid,
        source_text=transcript,
        context_hint="",
        input_type="text",
        include_teaching_context=include_teaching_context,
    )

    _log_usage_event(uid, "captured_reflection_text", {
        "include_teaching_context": include_teaching_context,
        "input_type": "text",
        "word_count": len(transcript.split()),
    })
    return _workflow_capture_compat_response(record)


@flask_app.route("/usage-event", methods=["POST"])
def create_usage_event():
    """Store a lightweight product usage event for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    event_name = (data.get("event_name") or "").strip()
    if not event_name:
        return jsonify({"error": "missing event_name"}), 400

    metadata = data.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return jsonify({"error": "metadata must be an object"}), 400

    _log_usage_event(uid, event_name, metadata or {})
    return jsonify({"ok": True})


@flask_app.route("/research-summary")
def get_research_summary():
    """Return a compact research summary for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    if not _user_is_founder(uid):
        return jsonify({"error": "forbidden"}), 403
    try:
        return jsonify(_summarize_research(uid))
    except Exception:
        traceback.print_exc()
        requester_doc = _get_db().collection("users").document(uid).get()
        requester_data = requester_doc.to_dict() if requester_doc.exists else {}
        return jsonify({
            **_empty_research_summary(requester_data),
            "error": "Research summary is temporarily unavailable.",
        }), 500


@flask_app.route("/research-notes")
def list_research_notes():
    """Return saved teacher interview and research notes for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    if not _user_is_founder(uid):
        return jsonify({"error": "forbidden"}), 403

    items = []
    for user_snap in _get_db().collection("users").stream():
        user_data = user_snap.to_dict() or {}
        docs = _get_db().collection("users").document(user_snap.id).collection("research_notes").stream()
        for snap in docs:
            payload = snap.to_dict() or {}
            coded_research = {
                "problem_themes": _safe_string_list(payload.get("problem_themes")),
                "objection_themes": _safe_string_list(payload.get("objection_themes")),
                "workflow_stages": _safe_string_list(payload.get("workflow_stages")),
                "desired_outcomes": _safe_string_list(payload.get("desired_outcomes")),
                "segment": _safe_string(payload.get("segment")),
                "fit_score": max(0, _safe_int(payload.get("fit_score"), 0)),
            }
            if not any([
                coded_research["problem_themes"],
                coded_research["objection_themes"],
                coded_research["workflow_stages"],
                coded_research["desired_outcomes"],
                coded_research["segment"],
                coded_research["fit_score"],
            ]):
                coded_research = _code_research_note({
                    "top_problem": payload.get("top_problem") or "",
                    "current_workaround": payload.get("current_workaround") or "",
                    "strongest_reaction": payload.get("strongest_reaction") or "",
                    "confusions": payload.get("confusions") or "",
                    "quote": payload.get("quote") or "",
                    "next_step": payload.get("next_step") or "",
                    "role": payload.get("role") or "",
                    "school_context": payload.get("school_context") or "",
                    "would_use_weekly": payload.get("would_use_weekly") or "",
                    "tags": _safe_string_list(payload.get("tags")),
                })
            items.append({
                "id": snap.id,
                "user_id": user_snap.id,
                "owner_label": (user_data.get("preferred_name") or user_data.get("name") or user_data.get("email") or "Unknown").strip(),
                "teacher_name": _safe_string(payload.get("teacher_name")),
                "role": _safe_string(payload.get("role")),
                "school_context": _safe_string(payload.get("school_context")),
                "top_problem": _safe_string(payload.get("top_problem")),
                "current_workaround": _safe_string(payload.get("current_workaround")),
                "strongest_reaction": _safe_string(payload.get("strongest_reaction")),
                "confusions": _safe_string(payload.get("confusions")),
                "would_use_weekly": _safe_string(payload.get("would_use_weekly")),
                "quote": _safe_string(payload.get("quote")),
                "next_step": _safe_string(payload.get("next_step")),
                "apps_discussed": _safe_string_list(payload.get("apps_discussed")),
                "tags": _safe_string_list(payload.get("tags")),
                "problem_themes": coded_research["problem_themes"],
                "objection_themes": coded_research["objection_themes"],
                "workflow_stages": coded_research["workflow_stages"],
                "desired_outcomes": coded_research["desired_outcomes"],
                "segment": coded_research["segment"],
                "fit_score": _safe_int(coded_research["fit_score"], 0),
                "created_at": _serialize_firestore_value(payload.get("created_at")),
                "_sort_at": _coerce_datetime(payload.get("created_at")) or datetime.min,
            })

    items.sort(key=lambda item: item.get("_sort_at") or datetime.min, reverse=True)
    for item in items:
        item.pop("_sort_at", None)
    return jsonify({"items": items})


@flask_app.route("/research-notes", methods=["POST"])
def create_research_note():
    """Save a structured teacher conversation note for product research."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    if not _user_is_founder(uid):
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}

    def clean_text(key: str, limit_value: int = 800) -> str:
        return re.sub(r"\s+", " ", str(data.get(key) or "")).strip()[:limit_value]

    def clean_list(key: str, limit_items: int = 8) -> list[str]:
        raw = data.get(key) or []
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(",")]
        if not isinstance(raw, list):
            return []
        cleaned = []
        for item in raw[:limit_items]:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if text:
                cleaned.append(text[:120])
        return cleaned

    top_problem = clean_text("top_problem", 1200)
    if len(top_problem) < 8:
        return jsonify({"error": "top_problem is too short"}), 400

    payload = {
        "teacher_name": clean_text("teacher_name", 120),
        "role": clean_text("role", 160),
        "school_context": clean_text("school_context", 240),
        "top_problem": top_problem,
        "current_workaround": clean_text("current_workaround", 1200),
        "strongest_reaction": clean_text("strongest_reaction", 1200),
        "confusions": clean_text("confusions", 1200),
        "would_use_weekly": clean_text("would_use_weekly", 80),
        "quote": clean_text("quote", 1200),
        "next_step": clean_text("next_step", 600),
        "apps_discussed": clean_list("apps_discussed"),
        "tags": clean_list("tags"),
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    payload.update(_code_research_note(payload))

    note_ref = _get_db().collection("users").document(uid).collection("research_notes").document()
    note_ref.set(payload)
    try:
        _recompute_research_signals()
    except Exception as exc:
        print(f"[{uid}] Warning: could not refresh research signals: {exc}")
    _log_usage_event(uid, "saved_research_note", {
        "apps_discussed_count": len(payload["apps_discussed"]),
        "tags_count": len(payload["tags"]),
        "would_use_weekly": payload["would_use_weekly"] or "unspecified",
        "problem_themes": payload["problem_themes"],
        "objection_themes": payload["objection_themes"],
        "segment": payload["segment"],
        "fit_score": payload["fit_score"],
    })
    return jsonify({"ok": True, "id": note_ref.id})


@flask_app.route("/daily-feed/setup", methods=["POST"])
def setup_daily_feed():
    """Enable or inspect the caller's private daily feed configuration."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    enable = None
    if "enabled" in data:
        enable = _coerce_bool(data.get("enabled"), False)
    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"error": "user not found"}), 404
    user_data = _ensure_daily_feed_config(uid, doc.to_dict() or {}, enable=enable)
    token = _safe_string(user_data.get("daily_feed_token"))

    if enable is True:
        _log_usage_event(uid, "enabled_daily_feed", {"publish_hour": _daily_feed_publish_hour(user_data)})

    return jsonify({
        "ok": True,
        "enabled": _coerce_bool(user_data.get("daily_feed_enabled"), False),
        "feed_url": _daily_feed_url_for_token(token),
        "daily_feed_timezone": _safe_timezone_name(user_data.get("daily_feed_timezone")),
        "daily_feed_publish_hour_local": _daily_feed_publish_hour(user_data),
        "daily_feed_status": _build_daily_feed_status(uid, user_data),
        "daily_feed_can_regenerate": _email_is_founder(user_data.get("email")),
    })


@flask_app.route("/daily-feed/generate-today", methods=["POST"])
def generate_daily_feed_today():
    """Generate today's daily feed episode for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    if not _user_is_founder(uid):
        return jsonify({"error": "forbidden"}), 403

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"error": "user not found"}), 404
    user_data = _ensure_daily_feed_config(uid, doc.to_dict() or {}, enable=True)
    _get_db().collection("users").document(uid).set({
        "daily_feed_last_attempted_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)

    try:
        episode = _build_daily_feed_episode(uid, user_data, force=True)
    except Exception as exc:
        print(f"[{uid}] Could not generate daily feed episode: {exc}")
        _get_db().collection("users").document(uid).set({
            "daily_feed_last_error": str(exc)[:500],
            "daily_feed_last_error_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        return jsonify({"error": str(exc)}), 502

    token = _safe_string(user_data.get("daily_feed_token"))
    refreshed = _get_db().collection("users").document(uid).get()
    refreshed_data = refreshed.to_dict() if refreshed.exists else user_data
    return jsonify({
        "ok": True,
        "feed_url": _daily_feed_url_for_token(token),
        "daily_feed_status": _build_daily_feed_status(uid, refreshed_data),
        "episode": {
            "id": episode.get("id") or episode.get("date_key"),
            "title": episode.get("title", ""),
            "description": episode.get("description", ""),
            "episode_type": episode.get("episode_type", ""),
            "duration_seconds": episode.get("duration_seconds", 0),
            "audio_url": _daily_feed_audio_url(token, _safe_string(episode.get("id") or episode.get("date_key"))),
        },
    })


@flask_app.route("/feed/<token>.xml")
def daily_feed_rss(token: str):
    """Return the private RSS feed for a user's latest Memnon audio."""
    token = _safe_string(token)
    if not token:
        return ("not found", 404)

    matches = list(_get_db().collection("users").where("daily_feed_token", "==", token).limit(1).stream())
    if not matches:
        return ("not found", 404)
    user_doc = matches[0]
    user_data = user_doc.to_dict() or {}

    latest_episode = _load_latest_daily_feed_episode(user_doc.id)
    episodes = [latest_episode] if latest_episode else []

    xml_body = _render_daily_feed_rss(user_data, token, episodes)
    return (
        xml_body,
        200,
        {
            "Content-Type": "application/rss+xml; charset=utf-8",
            "Cache-Control": "private, max-age=300",
        },
    )


@flask_app.route("/feed/<token>/<episode_id>.mp3")
def daily_feed_audio(token: str, episode_id: str):
    """Stream only the latest private daily feed episode for a token."""
    token = _safe_string(token)
    episode_id = _safe_string(episode_id)
    if not token or not episode_id:
        return ("not found", 404)

    matches = list(_get_db().collection("users").where("daily_feed_token", "==", token).limit(1).stream())
    if not matches:
        return ("not found", 404)
    user_doc = matches[0]
    latest_episode = _load_latest_daily_feed_episode(user_doc.id)
    if not latest_episode or _safe_string(latest_episode.get("id")) != episode_id:
        return ("not found", 404)
    storage_path = _safe_string(latest_episode.get("audio_storage_path"))
    if not storage_path:
        return ("not found", 404)

    try:
        audio_bytes = _download_daily_feed_audio(storage_path)
    except Exception as exc:
        print(f"[{user_doc.id}] Could not read daily feed audio {episode_id}: {exc}")
        return ("unavailable", 502)

    _log_usage_event(user_doc.id, "fetched_daily_feed_audio", {"episode_id": episode_id})
    return (
        audio_bytes,
        200,
        {
            "Content-Type": "audio/mpeg",
            "Cache-Control": "private, max-age=3600",
        },
    )


@flask_app.route("/me")
def get_me():
    """Return user config (no tokens) for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    doc = _get_db().collection("users").document(uid).get()
    data = doc.to_dict() if doc.exists else {}
    data.pop("google_drive_token", None)   # never send tokens to frontend
    data["google_tasks_connected"] = _user_tasks_connected(data)
    data["research_recommendations"] = (_load_research_signals().get("recommendations") or {})
    if data.get("daily_feed_token"):
        data["daily_feed_url"] = _daily_feed_url_for_token(_safe_string(data.get("daily_feed_token")))
    data["daily_feed_status"] = _build_daily_feed_status(uid, data)
    data["daily_feed_can_regenerate"] = _email_is_founder(data.get("email"))
    return jsonify(data)


@flask_app.route("/hf-status")
def get_hf_status():
    """Return Hugging Face runtime health plus embedding coverage for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"error": "user not found"}), 404

    user_data = doc.to_dict() or {}
    return jsonify({
        "hugging_face_configured": bool(HUGGING_FACE_API_KEY),
        "runtime": embedding_runtime_status(),
        "user": {
            "uid": uid,
            "email": user_data.get("email", ""),
        },
        "coverage": _summarize_note_embedding_coverage(uid),
    })


# ── Cloud Functions ────────────────────────────────────────────────────────────

@https_fn.on_request(
    region="us-central1",
    memory=options.MemoryOption.MB_512,
    timeout_sec=60,
    secrets=["OPENAI_API_KEY", "GOOGLE_CLIENT_SECRETS", "FLASK_SECRET", "HUGGING_FACE_API_KEY"],
)
def api(req: https_fn.Request) -> https_fn.Response:
    environ = dict(req.environ)
    path = environ.get("PATH_INFO", "") or ""
    if path == "/api":
        environ["PATH_INFO"] = "/"
    elif path.startswith("/api/"):
        environ["PATH_INFO"] = path[4:] or "/"
    with flask_app.request_context(environ):
        return flask_app.full_dispatch_request()


@scheduler_fn.on_schedule(
    schedule="every 1 minutes",
    region="us-central1",
    memory=options.MemoryOption.MB_512,
    timeout_sec=540,
    secrets=["OPENAI_API_KEY", "GOOGLE_CLIENT_SECRETS", "HUGGING_FACE_API_KEY"],
)
def worker(event: scheduler_fn.ScheduledEvent) -> None:
    users = _get_db().collection("users").where("active", "==", True).stream()
    for doc in users:
        uid = doc.id
        user_data = doc.to_dict()
        if user_data.get("drive_connected"):
            try:
                _sweep_user(uid, user_data)
            except Exception as exc:
                print(f"Sweep error [{uid}]: {exc}")


@scheduler_fn.on_schedule(
    schedule="every 15 minutes",
    region="us-central1",
    memory=options.MemoryOption.MB_512,
    timeout_sec=540,
    secrets=["OPENAI_API_KEY", "GOOGLE_CLIENT_SECRETS", "HUGGING_FACE_API_KEY"],
)
def daily_feed_worker(event: scheduler_fn.ScheduledEvent) -> None:
    now_utc = datetime.now(timezone.utc)
    users = _get_db().collection("users").where("daily_feed_enabled", "==", True).stream()
    for doc in users:
        uid = doc.id
        user_data = _ensure_daily_feed_config(uid, doc.to_dict() or {})
        try:
            local_now = _daily_feed_local_now(user_data, now_utc)
            publish_hour = _daily_feed_publish_hour(user_data)
            if local_now.hour < publish_hour:
                continue

            date_key = _daily_feed_date_key(user_data, now_utc)
            if _safe_string(user_data.get("daily_feed_last_generated_for")) == date_key:
                continue

            _get_db().collection("users").document(uid).set({
                "daily_feed_last_attempted_at": firestore.SERVER_TIMESTAMP,
            }, merge=True)
            _build_daily_feed_episode(uid, user_data, now_utc=now_utc)
        except Exception as exc:
            print(f"Daily feed error [{uid}]: {exc}")
            _get_db().collection("users").document(uid).set({
                "daily_feed_last_error": str(exc)[:500],
                "daily_feed_last_error_at": firestore.SERVER_TIMESTAMP,
            }, merge=True)
