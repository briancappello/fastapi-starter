from typing import ClassVar

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import async_session
from app.events import Event, emit_event
from app.events.handlers import event_handler
hello = APIRouter()

class HelloPayload(BaseModel):
    name: str
    message: str


class HelloEvent(Event):
    event_type: ClassVar[str] = "hello"
    payload: HelloPayload



@hello.post("/hello")
async def hello_world(name: str, message: str, session: AsyncSession = Depends(async_session)):
    event = HelloEvent(
        source="rest:hello_world",
        payload=HelloPayload(name=name, message=message),
    )
    await emit_event(event, session)
    await session.commit()

    return {"message": f"Hello, {name}!"}


# FIXME: better fix for users forgetting to make their handlers async
@event_handler("hello", group="hg")
async def handle_it(event: Event, session: AsyncSession = Depends(async_session)):
    print(event)
    print(type(session), session)
