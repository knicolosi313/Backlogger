"""Check the configured private bucket; optionally test and remove a tiny WAV file.

Run from the project root: python -m infra.storage.check_supabase --round-trip
"""

import argparse
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import wave

from dotenv import load_dotenv
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from adapters.supabase.storage import SupabaseStorage
from services.api.media import delete_media, media_exists, processing_audio, save_audio


def main():
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-trip", action="store_true")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    client = SupabaseStorage()
    with client.request("GET", f"bucket/{client.bucket}") as response:
        bucket = response.json()
    if bucket.get("public") is not False:
        raise RuntimeError("The configured media bucket must be private")
    print("Supabase private bucket is accessible.")
    print(f"Bucket audio types: {bucket.get('allowed_mime_types') or bucket.get('allowedMimeTypes') or 'unrestricted'}")
    if not args.round_trip:
        return

    buffer = BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 800)
    expected = buffer.getvalue()
    buffer.seek(0)
    os.environ["MEDIA_STORAGE"] = "supabase"
    upload = UploadFile(file=buffer, filename="storage-check.wav", headers=Headers({"content-type": "audio/wav"}))
    key = save_audio(uuid4(), upload)
    print("Test audio uploaded.")
    try:
        if not media_exists(key):
            raise RuntimeError("Uploaded test audio was not found")
        with processing_audio(key) as path:
            if path.read_bytes() != expected:
                raise RuntimeError("Downloaded test audio does not match the upload")
    finally:
        delete_media(key)
    if media_exists(key):
        raise RuntimeError("Test audio cleanup could not be verified")
    print("Audio upload, private download, and deletion succeeded.")


if __name__ == "__main__":
    try:
        main()
    except HTTPException as error:
        raise SystemExit(error.detail) from None
