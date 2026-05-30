"""
Memnon Google Drive capture poller.

Watches a Google Drive folder for new audio files, downloads them into
raw_audio_dir, then moves the originals into a 'processed' subfolder in
Drive so they aren't re-downloaded.

This is a pure capture layer — it feeds files into the same raw_audio_dir
that voice_pipeline.py watches, so transcription and note-creation work
exactly as normal regardless of capture source.

── Setup ────────────────────────────────────────────────────────────────────
1. Go to https://console.cloud.google.com/
2. New project → Enable the Google Drive API
3. APIs & Services → Credentials → Create credentials → OAuth client ID
   → Application type: Desktop app → Download JSON
4. Save the downloaded file as  google_client_secrets.json  next to config.json
5. Add to config.json:
     "google_drive": {
       "enabled": true,
       "client_secrets_path": "./google_client_secrets.json",
       "watch_folder_id": "YOUR_FOLDER_ID",
       "poll_seconds": 60
     }
   (Folder ID is the last segment of the folder's Drive URL)
6. Run once to authorize:  python src/drive_poller.py --config config.json
   → A browser tab opens; sign in and grant access.
   → Token saved to runtime/google_token.json for future runs.

── Usage ────────────────────────────────────────────────────────────────────
  python src/drive_poller.py --config config.json          # runs forever
  python src/drive_poller.py --config config.json --once   # single poll
  python src/drive_poller.py --config config.json --auth   # (re-)authorize only

── iPhone capture ───────────────────────────────────────────────────────────
  Record in Voice Memos → share → Save to Files → Google Drive / memnon-inbox
  Or use the iOS Shortcuts app to auto-upload on recording completion.
"""

import argparse
import io
import json
import sys
import time
from pathlib import Path

# ── dependency check ─────────────────────────────────────────────────────────

def _check_deps():
    missing = []
    for pkg in ("google.oauth2.credentials", "google_auth_oauthlib.flow",
                "googleapiclient.discovery"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg.split(".")[0].replace("_", "-"))
    if missing:
        pkgs = " ".join(dict.fromkeys(
            p.replace("google-oauth2", "google-auth")
             .replace("google-auth-oauthlib", "google-auth-oauthlib")
             .replace("googleapiclient", "google-api-python-client")
            for p in missing
        ))
        print("Missing dependencies. Install with:")
        print(f"  pip install google-api-python-client google-auth-oauthlib")
        sys.exit(1)

_check_deps()

from google.oauth2.credentials import Credentials          # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow     # noqa: E402
from google.auth.transport.requests import Request         # noqa: E402
from googleapiclient.discovery import build                # noqa: E402
from googleapiclient.http import MediaIoBaseDownload       # noqa: E402

# ── constants ────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/drive"]

# MIME types that Google assigns to audio/video uploads
AUDIO_MIME_TYPES = {
    "audio/mp4", "audio/x-m4a", "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/x-wav", "audio/aac", "audio/flac",
    "audio/webm", "audio/ogg", "video/mp4",   # iPhone .m4a → video/mp4
}

AUDIO_EXTENSIONS = {
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".mp4", ".webm", ".ogg",
    ".mpeg", ".mpga",
}

# ── auth ─────────────────────────────────────────────────────────────────────

def _get_credentials(token_path: Path, client_secrets_path: Path) -> Credentials:
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secrets_path.exists():
                print(f"\nClient secrets file not found: {client_secrets_path}")
                print("Download it from Google Cloud Console → APIs & Services → Credentials")
                print("and save it as google_client_secrets.json next to config.json.\n")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_path), SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
        print(f"Token saved → {token_path}")
    return creds

# ── Drive helpers ─────────────────────────────────────────────────────────────

def _find_or_create_folder(service, name: str, parent_id: str) -> str:
    """Return the ID of a named subfolder, creating it if absent."""
    q = (
        f"name='{name}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents "
        f"and trashed=false"
    )
    results = service.files().list(q=q, fields="files(id)", pageSize=1).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def _is_audio(f: dict) -> bool:
    mime = f.get("mimeType", "")
    name = f.get("name", "").lower()
    return mime in AUDIO_MIME_TYPES or any(name.endswith(ext) for ext in AUDIO_EXTENSIONS)

# ── core poll ────────────────────────────────────────────────────────────────

def poll_once(service, config: dict, *, verbose: bool = True) -> int:
    """
    Download new audio files from the watched Drive folder into raw_audio_dir.
    Move downloaded originals into a 'processed' subfolder in Drive.
    Returns the number of files downloaded.
    """
    drive_cfg = config.get("google_drive", {})
    watch_folder_id: str = drive_cfg["watch_folder_id"]
    raw_dir = Path(config["raw_audio_dir"]).expanduser()
    raw_dir.mkdir(parents=True, exist_ok=True)

    processed_id = _find_or_create_folder(service, "processed", watch_folder_id)

    results = service.files().list(
        q=f"'{watch_folder_id}' in parents and trashed=false",
        fields="files(id, name, mimeType, size)",
        pageSize=100,
    ).execute()

    audio_files = [f for f in results.get("files", []) if _is_audio(f)]

    if verbose and not audio_files:
        print("  No new audio files.")

    downloaded = 0
    for f in audio_files:
        dest = raw_dir / f["name"]
        if dest.exists():
            if verbose:
                print(f"  Already downloaded, archiving in Drive: {f['name']}")
        else:
            size_str = f"{int(f.get('size', 0)) // 1024} KB" if f.get("size") else "?"
            if verbose:
                print(f"  ↓ {f['name']}  ({size_str})")
            request = service.files().get_media(fileId=f["id"])
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request, chunksize=4 * 1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            dest.write_bytes(buf.getvalue())
            if verbose:
                print(f"    → {dest}")
            downloaded += 1

        # Move original out of inbox in Drive
        service.files().update(
            fileId=f["id"],
            addParents=processed_id,
            removeParents=watch_folder_id,
            fields="id, parents",
        ).execute()

    return downloaded

# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Memnon Google Drive capture poller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default="config.json",
                        help="Path to config.json (default: ./config.json)")
    parser.add_argument("--once", action="store_true",
                        help="Poll once and exit")
    parser.add_argument("--auth", action="store_true",
                        help="(Re-)authorize with Google and exit")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-file output")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)
    config = json.loads(config_path.read_text())

    drive_cfg = config.get("google_drive", {})
    if not drive_cfg.get("enabled"):
        print("Google Drive capture is not enabled.")
        print('Set  "google_drive": { "enabled": true, ... }  in config.json')
        sys.exit(1)

    runtime_dir = Path(config.get("runtime_dir", "./runtime")).expanduser()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    token_path = runtime_dir / "google_token.json"

    client_secrets_path = Path(
        drive_cfg.get("client_secrets_path", "./google_client_secrets.json")
    ).expanduser()

    print("Authenticating with Google Drive...")
    creds = _get_credentials(token_path, client_secrets_path)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    print("Connected.\n")

    if args.auth:
        print("Authorization complete.")
        return

    if not drive_cfg.get("watch_folder_id"):
        print("No watch_folder_id set in google_drive config.")
        print("Copy the folder ID from the Drive URL: "
              "drive.google.com/drive/folders/<THIS_PART>")
        sys.exit(1)

    poll_interval = int(drive_cfg.get("poll_seconds", config.get("poll_seconds", 60)))

    if args.once:
        n = poll_once(service, config, verbose=not args.quiet)
        print(f"\nDone — downloaded {n} file(s).")
        return

    print(f"Polling every {poll_interval}s.  Ctrl-C to stop.\n")
    while True:
        try:
            n = poll_once(service, config, verbose=not args.quiet)
            if n and args.quiet:
                print(f"Downloaded {n} file(s).")
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as exc:
            print(f"Poll error: {exc}", file=sys.stderr)
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
