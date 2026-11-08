import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import auto_import, board_context, boards, connections, integrations, jira, meetings, stories
from .dependencies import SessionDep
from .cors import configure_cors

app = FastAPI(title="Backlogger")
configure_cors(app)
app.include_router(boards.router)
app.include_router(board_context.router)
app.include_router(meetings.router)
app.include_router(auto_import.router)
app.include_router(stories.router)
app.include_router(integrations.router)
app.include_router(connections.router)
app.include_router(jira.router)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    if request.method == "POST" and request.url.path in {
        "/api/integrations/trello/connection", "/api/integrations/jira/connection",
    }:
        # Validation errors normally include the rejected input. Never echo a token.
        return JSONResponse(status_code=422, content={"detail": "Invalid connection request"})
    return await request_validation_exception_handler(request, error)


@app.exception_handler(requests.RequestException)
def trello_unavailable(request: Request, error: requests.RequestException):
    # Never include exception text: Requests URLs may contain the Trello token.
    provider = "Jira" if "/jira/" in request.url.path else "Trello"
    status = 502
    detail = f"{provider} request failed"
    if isinstance(error, requests.Timeout):
        status, detail = 504, f"{provider} request timed out"
    elif error.response is not None:
        if error.response.status_code == 404:
            status, detail = 404, f"{provider} resource not found or inaccessible"
        elif error.response.status_code in {401, 403}:
            detail = f"{provider} credentials were rejected or access was denied"
        elif error.response.status_code == 429:
            status, detail = 503, f"{provider} rate limit reached; try again later"
    return JSONResponse(status_code=status, content={"detail": detail})


@app.exception_handler(IntegrityError)
def database_conflict(request: Request, error: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "Record conflicts with existing data"})


@app.exception_handler(SQLAlchemyError)
def database_unavailable(request: Request, error: SQLAlchemyError):
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


@app.get("/health/db")
def database_health(session: SessionDep):
    try:
        session.exec(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ok"}

