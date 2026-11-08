"""Store uploaded audio under generated names inside the local media directory."""

import os
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile

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
    if (os.getenv("MEDIA_STORAGE") or "local").strip() != "local":
        raise HTTPException(status_code=503, detail="Local media storage is required")
    content_type = (upload.content_type or "").split(";", 1)[0].lower().strip()
    suffix = CONTENT_TYPES.get(content_type)
    if suffix is None:
        raise HTTPException(status_code=415, detail="Unsupported audio content type")

    limit = int(os.getenv("MAX_UPLOAD_MB") or "100") * 1024 * 1024
    key = f"{meeting_id}/{uuid4().hex}{suffix}"
    destination = media_path(key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with destination.open("xb") as output:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="Audio exceeds MAX_UPLOAD_MB")
                output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Audio file is empty")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return key
