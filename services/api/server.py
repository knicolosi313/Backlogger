from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from services.db import get_session

app = FastAPI(title="Backlogger")
SessionDep = Annotated[Session, Depends(get_session)]


@app.get("/health/db")
def database_health(session: SessionDep):
    try:
        session.exec(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ok"}

