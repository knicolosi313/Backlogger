"""Shared request dependencies and record lookups."""

from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlmodel import Session, SQLModel, select

from services.db import get_session

SessionDep = Annotated[Session, Depends(get_session)]
RecordType = TypeVar("RecordType", bound=SQLModel)


def get_record(
    session: Session, model: type[RecordType], record_id: UUID, *, lock: bool = False,
) -> RecordType:
    query = select(model).where(model.id == record_id)
    if lock:
        query = query.with_for_update()
    record = session.exec(query).first()
    if record is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return record
