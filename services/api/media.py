"""Store audio locally or in Supabase; materialize remote audio only for processing."""

import os
import re
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
import requests

from adapters.supabase.storage import SupabaseStorage, validate_bucket

CONTENT_TYPES = {
    "audio/webm": ".webm",
    "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg", "audio/flac": ".flac", "audio/x-flac": ".flac",
}


def media_root() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    configured_path = os.getenv("MEDIA_LOCAL_PATH") or "media"
    return (project_root / configured_path).resolve()


def media_path(key: str) -> Path:
    root = media_root()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise HTTPException(status_code=400, detail="Invalid media key")
    return path


def save_audio(meeting_id: UUID, upload: UploadFile) -> str:
    storage = (os.getenv("MEDIA_STORAGE") or "local").strip()
    if storage not in {"local", "supabase"}:
        raise HTTPException(status_code=503, detail="MEDIA_STORAGE must be local or supabase")
    content_type = (upload.content_type or "").split(";", 1)[0].lower().strip()
    suffix = CONTENT_TYPES.get(content_type)
    if suffix is None:
        raise HTTPException(status_code=415, detail="Unsupported audio content type")

    key = f"{meeting_id}/{uuid4().hex}{suffix}"
    if storage == "supabase":
        client = SupabaseStorage()
        # Validate the complete upload before writing anything to the bucket.
        with TemporaryFile(mode="w+b") as temporary:
            copy_audio(upload.file, temporary)
            temporary.seek(0)
            client.upload(key, temporary, content_type)
        return f"supabase://{client.bucket}/{key}"

    destination = media_path(key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as output:
            copy_audio(upload.file, output)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return key


def copy_audio(source, output) -> None:
    limit = int(os.getenv("MAX_UPLOAD_MB") or "100") * 1024 * 1024
    size = 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "Audio exceeds MAX_UPLOAD_MB")
        output.write(chunk)
    if size == 0:
        raise HTTPException(400, "Audio file is empty")


def remote_location(key: str) -> tuple[str, str] | None:
    if not key.startswith("supabase://"):
        return None
    bucket, separator, object_key = key[len("supabase://"):].partition("/")
    # Only generated recording paths are accepted, never URLs or arbitrary paths.
    if not separator or not re.fullmatch(r"[0-9a-f-]{36}/[0-9a-f]{32}\.(webm|wav|mp3|m4a|ogg|flac)", object_key):
        raise HTTPException(400, "Invalid media key")
    validate_bucket(bucket)
    return bucket, object_key


def media_exists(key: str) -> bool:
    location = remote_location(key)
    if location is None:
        return media_path(key).is_file()
    bucket, object_key = location
    return SupabaseStorage(bucket).exists(object_key)


def delete_media(key: str) -> None:
    location = remote_location(key)
    if location is None:
        media_path(key).unlink(missing_ok=True)
    else:
        bucket, object_key = location
        SupabaseStorage(bucket).delete(object_key)


@contextmanager
def processing_audio(key: str):
    """Keep old local keys working after switching providers; clean downloads on exit."""
    location = remote_location(key)
    if location is None:
        yield media_path(key)
        return
    bucket, object_key = location
    with TemporaryDirectory(prefix="backlogger-audio-") as directory:
        path = Path(directory) / Path(object_key).name
        response = SupabaseStorage(bucket).download(object_key)
        try:
            with response, path.open("xb") as output:
                limit = int(os.getenv("MAX_UPLOAD_MB") or "100") * 1024 * 1024
                size = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, "Stored audio exceeds MAX_UPLOAD_MB")
                    output.write(chunk)
                if size == 0:
                    raise HTTPException(502, "Supabase returned empty audio")
        except requests.RequestException:
            raise HTTPException(502, "Could not download audio from Supabase Storage") from None
        yield path
