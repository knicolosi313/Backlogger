import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import boards, integrations, meetings, stories
from .dependencies import SessionDep

app = FastAPI(title="Backlogger")
app.include_router(boards.router)
app.include_router(meetings.router)
app.include_router(stories.router)
app.include_router(integrations.router)


@app.exception_handler(requests.RequestException)
def trello_unavailable(request: Request, error: requests.RequestException):
    # Never include exception text: Requests URLs may contain the Trello token.
    status = 502
    detail = "Trello request failed"
    if isinstance(error, requests.Timeout):
        status, detail = 504, "Trello request timed out"
    elif error.response is not None:
        if error.response.status_code == 404:
            status, detail = 404, "Trello resource not found or inaccessible"
        elif error.response.status_code in {401, 403}:
            detail = "Trello credentials were rejected or access was denied"
        elif error.response.status_code == 429:
            status, detail = 503, "Trello rate limit reached; try again later"
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

