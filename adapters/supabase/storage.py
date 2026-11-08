"""Backend-only access to a private Supabase Storage bucket."""

import os
import re
from urllib.parse import quote, urlsplit

import requests
from fastapi import HTTPException


def validate_bucket(bucket: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", bucket):
        raise HTTPException(503, "Set a valid SUPABASE_STORAGE_BUCKET")
    return bucket


class SupabaseStorage:
    def __init__(self, bucket: str | None = None):
        self.bucket = validate_bucket(bucket or os.getenv("SUPABASE_STORAGE_BUCKET", ""))
        self.url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(503, "Set a valid SUPABASE_URL")
        secret = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not secret:
            raise HTTPException(503, "Set SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY")
        self.headers = {"apikey": secret}
        # New secret keys are API keys, not JWT bearer tokens.
        if not secret.startswith("sb_secret_"):
            self.headers["Authorization"] = f"Bearer {secret}"

    def request(self, method: str, path: str, *, missing_ok=False, **kwargs):
        try:
            response = requests.request(
                method, f"{self.url}/storage/v1/{path}",
                headers=self.headers | kwargs.pop("headers", {}),
                timeout=(10, 120), allow_redirects=False, **kwargs,
            )
        except requests.RequestException:
            # Provider exceptions may contain headers or URLs. Never propagate them.
            raise HTTPException(502, "Supabase Storage request failed") from None
        # Supabase's HEAD route can return 400 for a missing object (as handled
        # by storage-js.exists); other methods must still report bad requests.
        if missing_ok and (response.status_code == 404 or (method == "HEAD" and response.status_code == 400)):
            response.close()
            return None
        if not 200 <= response.status_code < 300:
            response.close()
            raise HTTPException(502, f"Supabase Storage request failed (HTTP {response.status_code})")
        return response

    def object_path(self, key: str) -> str:
        return f"{quote(self.bucket, safe='')}/{quote(key, safe='/')}"

    def upload(self, key: str, file, content_type: str) -> None:
        response = self.request(
            "POST", f"object/{self.object_path(key)}", data=file,
            headers={"Content-Type": content_type, "x-upsert": "false"},
        )
        response.close()

    def exists(self, key: str) -> bool:
        response = self.request("HEAD", f"object/authenticated/{self.object_path(key)}", missing_ok=True)
        if response is None:
            return False
        response.close()
        return True

    def download(self, key: str):
        return self.request("GET", f"object/authenticated/{self.object_path(key)}", stream=True)

    def delete(self, key: str) -> None:
        response = self.request(
            "DELETE", f"object/{quote(self.bucket, safe='')}",
            json={"prefixes": [key]}, missing_ok=True,
        )
        if response is not None:
            response.close()
