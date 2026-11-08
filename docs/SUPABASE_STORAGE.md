# Supabase audio storage

The backend can store new recordings in a private Supabase Storage bucket while
keeping meeting and story records in the existing TigerData PostgreSQL database.
The frontend continues to upload audio to FastAPI; it never receives Supabase
credentials or a public recording URL.

Set these values in the root `.env` and restart the backend:

```dotenv
MEDIA_STORAGE=supabase
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_STORAGE_BUCKET=meeting-audio
SUPABASE_SECRET_KEY=YOUR_BACKEND_SECRET_KEY
```

For self-hosted Supabase, use the base API gateway URL as `SUPABASE_URL`.
An existing legacy service-role JWT can instead be set as
`SUPABASE_SERVICE_ROLE_KEY`, leaving `SUPABASE_SECRET_KEY` empty. New `sb_secret_`
keys are sent only in `apikey`; legacy JWT keys also use bearer authentication,
following Supabase's [API key guidance](https://supabase.com/docs/guides/getting-started/api-keys).
Do not use an anon/publishable key or put a backend secret in extension settings.

Create the bucket in Supabase Storage and keep **Public bucket** disabled.
Ensure the bucket's file-size limit accommodates `MAX_UPLOAD_MB` and its allowed
MIME types include the audio formats you want to upload (`audio/webm` for browser
recordings). The backend accepts the same audio types as local storage. See
[private bucket access](https://supabase.com/docs/guides/storage/buckets/fundamentals).
The privileged backend key accesses the bucket; browser RLS upload policies and
public URLs are not required by this integration.

Verify the bucket from the repository root:

```powershell
./.venv/Scripts/python.exe -m infra.storage.check_supabase
./.venv/Scripts/python.exe -m infra.storage.check_supabase --round-trip
```

The first command checks bucket access and privacy. The second additionally
uploads a tiny generated WAV, verifies the downloaded bytes, and deletes it.
Allow `audio/wav` for this check if you restrict the bucket's MIME types.
No existing files are modified.

## Upload and processing flow

1. FastAPI validates the audio type, nonempty content, and `MAX_UPLOAD_MB` limit.
2. A temporary file buffers the upload, then the backend writes it using a
   generated meeting UUID/object UUID path. Supabase overwrites are disabled.
3. `Meeting.media_key` stores `supabase://BUCKET/MEETING_UUID/OBJECT_UUID.webm`.
   This is an internal storage reference, not a browser URL.
4. Processing checks the object exists and downloads a temporary local file for
   the existing Gemini upload call. The temporary copy is removed after the
   pipeline finishes or fails. The Supabase original remains available.
5. Replacing audio updates the database first, then removes the previous file.
   If saving the database change fails, the new upload is removed when possible.
   Cleanup failures are logged without provider error details.

Existing local media keys continue to resolve using `MEDIA_LOCAL_PATH` even
after switching to Supabase. Existing recordings are not automatically migrated.
Supabase references retain their bucket if the configured upload bucket changes;
keep access to the same project. Switching back to `MEDIA_STORAGE=local` changes
new uploads only; Supabase keys still require the Supabase connection settings.

The backend uses the existing `requests` dependency and streaming downloads;
no database migration or Supabase SDK installation is needed. Large-file
resumable uploads are not implemented. Standard uploads use a 120-second read
timeout, so deployment proxy/upload limits still need to suit your recordings.
For larger recordings, Supabase recommends [resumable uploads](https://supabase.com/docs/guides/storage/uploads/standard-uploads).

Database writes and object storage do not share a transaction. Cleanup is best
effort; a network interruption or process crash can leave an unreferenced object.
Automatic retention and moving old local files are not implemented. Workspace
data remains shared as in the existing app.

## Playback in the extension

The Record tab shows the five most recent meetings for the selected board,
newest first. Click a meeting to play its audio inside the extension from the
beginning. Only one recent-meeting player is open at a time. Switching meetings,
closing audio, or switching boards stops playback and releases its temporary
browser URL. Meetings without an uploaded recording show **No audio uploaded**.

Click **Jump to moment** under a story's evidence to open the meeting audio
player inside the extension. It seeks to that evidence timestamp and attempts
playback. If Chrome blocks autoplay, use the player's Play control. Repeated
jumps reuse the loaded recording. Leaving the story stops playback, cancels
pending downloads, and releases the browser's temporary audio URL.

`GET /api/meetings/{meeting_id}/audio` streams local or private Supabase audio
through the backend with `Cache-Control: private, no-store`. Missing meetings or
recordings return 404. Storage credentials and public bucket URLs are never sent
to the extension. The extension fetches the complete recording on the first jump
before playing it from a temporary browser Blob URL; this supports seeking
without forwarding HTTP range requests to Supabase. Long recordings may take
time to load and use memory up to the recording's size. Playback continues past
the evidence segment, with normal pause and seek controls.

This endpoint follows the current shared-workspace API: it does not add login
or per-user authorization. A private bucket protects direct Supabase access;
the backend audio endpoint itself is not user-isolated.
