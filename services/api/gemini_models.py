"""The Gemini models a client may choose for processing a meeting."""

import os

from fastapi import APIRouter

from .schemas import ModelChoices

router = APIRouter(prefix="/api/models", tags=["Models"])

FALLBACK_DEFAULT = "gemini-3.1-flash-lite"
# The only models offered. Both accept audio and return schema-shaped JSON.
SUPPORTED_MODELS = (FALLBACK_DEFAULT, "gemini-3.5-flash-lite")


def default_model() -> str:
    return os.getenv("GEMINI_MODEL") or FALLBACK_DEFAULT


def available_models() -> list[str]:
    """The default first, then the supported models.

    Clients can only pick from this list, so a request can't make the server call an arbitrary model.
    GEMINI_MODEL is always included, so setting it to something else keeps that choice usable.
    """
    default = default_model()
    return [default, *(name for name in SUPPORTED_MODELS if name != default)]


@router.get("", response_model=ModelChoices)
def list_models():
    return ModelChoices(default=default_model(), models=available_models())
