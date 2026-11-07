# One-press capture from the iPhone Action Button

A per-user **capture token** lets an iOS Shortcut (or a native app) post
recordings to Memnon without a Firebase sign-in, which expires after an hour.

## The token

- Create, regenerate or revoke it on **Setup → One-press capture**
  (`GET/POST/DELETE /api/capture-token`, Firebase sign-in required).
- It looks like `mcap_` followed by 43 characters (32 random bytes). It is
  shown once. Memnon stores only its SHA-256 digest
  (`capture_tokens/{digest}` plus the same digest on the user document).
- It works on exactly one route: `POST /api/workflows/captures`. Every other
  route treats it as no credentials (enforced in `_verify_firebase_token` and
  covered by `tests/test_capture_tokens.py`).
- One token per user. Regenerating or revoking stops the old token at once.
- Each use records `last_used_at`, shown on Setup, so a leaked token is
  noticeable.
- The capture route's existing checks apply: audio must be `audio/x-m4a`,
  `audio/mp4`, `audio/webm`, `audio/ogg` or `video/mp4`, and at most 5 MB
  (roughly five minutes of iPhone voice audio). Transcripts under three words
  are refused.

## The Shortcut

1. Shortcuts → **+** → name it "Memnon capture".
2. Add **Record Audio**. Audio Quality: Normal. Start Recording:
   Immediately. Finish Recording: On Tap.
3. Add **Get Contents of URL**:
   - URL: `https://memnon.app/api/workflows/captures`
   - Method: `POST`
   - Headers: `Authorization` = `Bearer mcap_...` (the word Bearer, a space,
     then your token)
   - Request Body: **Form**. Add a **File** field named `file` and set it to
     **Recorded Audio**.
4. Optional: add **Get Dictionary Value** for `capture_id` and
   **Show Notification** with it, so you know the capture landed.
5. Settings → **Action Button** → **Shortcut** → choose "Memnon capture".

The capture appears on Today like any other voice capture.

Not tested on an iPhone: the server side was tested with `curl` sending the
same multipart request. If the Shortcut gets `400 unsupported audio format`,
the recording's content type is not one of the types listed above; report it
so it can be added.

## curl equivalent

```bash
curl -sS -X POST https://memnon.app/api/workflows/captures \
  -H "Authorization: Bearer $MEMNON_CAPTURE_TOKEN" \
  -F "file=@note.m4a;type=audio/x-m4a"
```
