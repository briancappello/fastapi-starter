"""Hello module — canonical example of the web server event emission pattern.

Demonstrates:
1. Defining a typed event
2. Emitting an event from an API route (same transaction as any domain write)
3. Handling the event with an @event_handler

This is the "Phase 5" pattern: direct DB write + outbox event in one
transaction. If the route creates/updates domain objects AND emits an
event, both succeed or fail atomically.
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.events import Event, emit_event
from app.events.handlers import event_handler


hello = APIRouter(tags=["hello"])


# -- Event definition --


class HelloPayload(BaseModel):
    name: str
    message: str


class HelloEvent(Event):
    """Fired when someone is greeted via the /hello endpoint."""

    event_type: Literal["hello.greeted"] = "hello.greeted"
    name: str
    message: str


# -- API route --


@hello.post("/hello")
async def hello_world(
    name: str,
    message: str = "Hello!",
    session: AsyncSession = Depends(async_session),
):
    """Greet someone and emit an event.

    This demonstrates the web server event emission pattern:
    - Build the event from request data
    - Call emit_event() with the current session
    - Commit the session (domain write + outbox row, atomically)
    - Return the response

    If this route also created/updated a domain model (e.g., a Greeting
    record), that write and the event emission would share the same
    transaction — guaranteeing consistency.
    """
    event = HelloEvent(
        source="rest:hello",
        name=name,
        message=f"{message}, {name}!",
    )
    await emit_event(event, session)
    await session.commit()

    return {
        "event_id": str(event.event_id),
        "message": event.message,
    }


# -- Event handler --


@event_handler("hello.greeted", group="hello")
async def on_hello_greeted(event: HelloEvent, session: AsyncSession):
    """React to a greeting event.

    In a real app, this might send a notification, update analytics,
    or trigger a follow-up workflow. Here we just log it.
    """
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"[hello] {event.name} was greeted: {event.message} (event_id={event.event_id})"
    )
