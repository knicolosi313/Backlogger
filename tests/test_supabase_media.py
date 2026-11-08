"""Private media storage with an in-memory fake Supabase REST service."""

from io import BytesIO
from uuid import uuid4

import pytest
import requests
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from adapters.supabase.storage import SupabaseStorage
from services.api import media, meetings


class StorageResponse:
    def __init__(self, status=200, chunks=()):
        self.status_code = status
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size):
        yield from self.chunks

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def supabase(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_STORAGE", "supabase")
    monkeypatch.setenv("MEDIA_LOCAL_PATH", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://storage.example.test")
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "meeting-audio")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_synthetic")
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    objects, calls = {}, []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert kwargs["allow_redirects"] is False
        assert kwargs["headers"]["apikey"] == "sb_secret_synthetic"
        assert "Authorization" not in kwargs["headers"]
        base = "https://storage.example.test/storage/v1/"
        path = url.removeprefix(base)
        if method == "POST":
            key = path.removeprefix("object/")
            assert key not in objects
            objects[key] = kwargs["data"].read()
            return StorageResponse()
        if method in {"GET", "HEAD"}:
            key = path.removeprefix("object/info/").removeprefix("object/authenticated/")
            return StorageResponse(200, [objects[key]]) if key in objects else StorageResponse(404)
        if method == "DELETE":
            bucket = path.removeprefix("object/")
            for key in kwargs["json"]["prefixes"]:
                objects.pop(f"{bucket}/{key}", None)
            return StorageResponse()
        pytest.fail("Unexpected storage method")

    monkeypatch.setattr(requests, "request", request)
    return objects, calls


def upload(data=b"synthetic audio"):
    return UploadFile(file=BytesIO(data), filename="../../bad.webm", headers=Headers({"content-type": "audio/webm;codecs=opus"}))


def test_private_upload_download_and_delete(supabase, tmp_path, monkeypatch):
    objects, calls = supabase
    key = media.save_audio(uuid4(), upload())
    assert key.startswith("supabase://meeting-audio/")
    assert media.media_exists(key)
    assert calls[0][2]["headers"]["Content-Type"] == "audio/webm"
    # A saved locator keeps its bucket even if the upload default changes.
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "different-bucket")
    with media.processing_audio(key) as path:
        assert path.suffix == ".webm"
        assert path.read_bytes() == b"synthetic audio"
        downloaded = path
    assert not downloaded.exists()
    assert len(objects) == 1
    media.delete_media(key)
    assert not objects
    assert media.media_exists(key) is False
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("data,status", [(b"", 400), (b"x" * (1024 * 1024 + 1), 413)])
def test_invalid_upload_never_reaches_supabase(supabase, data, status):
    with pytest.raises(HTTPException) as error:
        media.save_audio(uuid4(), upload(data))
    assert error.value.status_code == status
    assert supabase == ({}, [])


def test_download_is_cleaned_up_if_pipeline_fails(supabase):
    key = media.save_audio(uuid4(), upload())
    with pytest.raises(RuntimeError):
        with media.processing_audio(key) as path:
            downloaded = path
            raise RuntimeError("Pipeline failed")
    assert not downloaded.exists()
    assert len(supabase[0]) == 1  # Original remains available for retry.


def test_local_recordings_remain_readable_after_switching_storage(supabase, tmp_path):
    file = tmp_path / "existing.webm"
    file.write_bytes(b"local recording")
    assert media.media_exists("existing.webm")
    with media.processing_audio("existing.webm") as path:
        assert path == file
    assert file.exists()
    assert not supabase[1]


def test_playback_endpoint_closes_private_download_and_preserves_original(supabase, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    key = media.save_audio(uuid4(), upload())
    recording = SimpleNamespace(media_key=key)
    monkeypatch.setattr(meetings, "get_record", lambda *args: recording)
    from services.db import get_session
    app = FastAPI()
    app.include_router(meetings.router)
    app.dependency_overrides[get_session] = lambda: None
    original_download = SupabaseStorage.download
    responses = []

    def download(client, object_key):
        response = original_download(client, object_key)
        responses.append(response)
        return response

    monkeypatch.setattr(SupabaseStorage, "download", download)
    with TestClient(app) as client:
        response = client.get(f"/api/meetings/{uuid4()}/audio")
    assert response.status_code == 200
    assert response.content == b"synthetic audio"
    assert responses[0].closed
    assert len(supabase[0]) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("key", [
    "supabase://meeting-audio/../secret", "supabase://other/path?token=secret",
    "supabase://meeting-audio/https://example.test/audio.webm",
])
def test_invalid_remote_locator_never_sends_credentials(supabase, key):
    with pytest.raises(HTTPException):
        media.media_exists(key)
    assert not supabase[1]


def test_provider_errors_do_not_expose_secrets(supabase, monkeypatch):
    def fail(*args, **kwargs):
        raise requests.Timeout("apikey=private-secret")

    monkeypatch.setattr(requests, "request", fail)
    with pytest.raises(HTTPException) as error:
        media.save_audio(uuid4(), upload())
    assert error.value.status_code == 502
    assert "private-secret" not in error.value.detail


def test_legacy_service_role_uses_bearer_header(supabase, monkeypatch):
    monkeypatch.delenv("SUPABASE_SECRET_KEY")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "synthetic.jwt.key")
    assert SupabaseStorage().headers == {
        "apikey": "synthetic.jwt.key", "Authorization": "Bearer synthetic.jwt.key",
    }


def test_missing_object_head_400_is_handled_without_hiding_upload_errors(supabase, monkeypatch):
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: StorageResponse(400))
    assert SupabaseStorage().exists("missing.webm") is False
    with pytest.raises(HTTPException, match="HTTP 400"):
        SupabaseStorage().upload("new.webm", BytesIO(b"audio"), "audio/webm")


@pytest.mark.parametrize("setting", ["SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_STORAGE_BUCKET"])
def test_missing_configuration_is_reported_before_upload(supabase, monkeypatch, setting):
    monkeypatch.delenv(setting)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(HTTPException) as error:
        media.save_audio(uuid4(), upload())
    assert error.value.status_code == 503
    assert not supabase[1]


def test_download_size_limit_and_stream_errors_close_the_response(supabase, monkeypatch):
    key = media.save_audio(uuid4(), upload())
    response = StorageResponse(chunks=[b"x" * (1024 * 1024 + 1)])
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: response)
    with pytest.raises(HTTPException) as error:
        with media.processing_audio(key):
            pytest.fail("Oversized audio reached the pipeline")
    assert error.value.status_code == 413
    assert response.closed


def test_database_failure_cleans_up_new_upload_without_replacing_original(monkeypatch):
    from unittest.mock import Mock
    meeting = Mock(status="uploaded", media_key="old.webm")
    session = Mock()
    session.commit.side_effect = RuntimeError("Database failure")
    cleanup = Mock()
    monkeypatch.setattr(meetings, "get_record", lambda *args, **kwargs: meeting)
    monkeypatch.setattr(meetings, "save_audio", lambda *args: "new.webm")
    monkeypatch.setattr(meetings, "delete_media", cleanup)
    with pytest.raises(RuntimeError, match="Database failure"):
        meetings.upload_media(uuid4(), upload(), session)
    cleanup.assert_called_once_with("new.webm")
