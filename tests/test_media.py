"""Local upload limits and paths, using temporary files."""

from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from services.api.media import media_path, save_audio


@pytest.fixture(autouse=True)
def local_media(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_LOCAL_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIA_STORAGE", "local")
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")


def upload(data=b"synthetic audio", content_type="audio/webm"):
    return UploadFile(
        file=BytesIO(data), filename="../../untrusted.webm",
        headers=Headers({"content-type": content_type}),
    )


def test_upload_uses_generated_filename_and_accepts_codec_parameter(tmp_path):
    meeting_id = uuid4()
    key = save_audio(meeting_id, upload(content_type="audio/webm;codecs=opus"))
    assert key.startswith(f"{meeting_id}/")
    assert "untrusted" not in key
    assert media_path(key).read_bytes() == b"synthetic audio"
    assert media_path(key).is_relative_to(tmp_path)


@pytest.mark.parametrize("data,content_type,status", [
    (b"", "audio/wav", 400),
    (b"x" * (1024 * 1024 + 1), "audio/wav", 413),
    (b"not audio", "text/plain", 415),
])
def test_invalid_upload_is_rejected_and_leaves_no_file(tmp_path, data, content_type, status):
    with pytest.raises(HTTPException) as error:
        save_audio(uuid4(), upload(data, content_type))
    assert error.value.status_code == status
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_media_key_cannot_escape_storage_directory():
    with pytest.raises(HTTPException, match="Invalid media key"):
        media_path("../outside.wav")


def test_unsupported_storage_configuration_is_reported(monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE", "s3")
    with pytest.raises(HTTPException) as error:
        save_audio(uuid4(), upload())
    assert error.value.status_code == 503
