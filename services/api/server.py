from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import boards, meetings, stories
from .dependencies import SessionDep

app = FastAPI(title="Backlogger")
app.include_router(boards.router)
app.include_router(meetings.router)
app.include_router(stories.router)


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

