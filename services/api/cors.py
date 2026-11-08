"""Allow configured frontend origins to send browser session cookies."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .trello_auth import load_settings


def configure_cors(app: FastAPI) -> None:
    load_settings()
    origins = [origin.strip() for origin in (os.getenv("ALLOWED_ORIGINS") or "").split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("ALLOWED_ORIGINS must list explicit origins when using connection cookies")
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type"],
        )
