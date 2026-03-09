# WebSocket Server

WebSocket endpoint at `/ws/{client_id}` with configurable auth, message routing,
application-level heartbeat, and integration with the event pipeline.

## Connection Lifecycle

```
  Client                                Server
    |                                     |
    |--- GET /ws/{client_id} ------------>|
    |<-- 101 Switching Protocols ---------|
    |                                     |
    |         [auth phase]                |
    |   none:  skip                       |
    |   basic: check Authorization header |
    |   token: check ?token= query param  |
    |   first_message:                    |
    |--- {"type":"auth","token":"xxx"} -->|  (within WS_AUTH_TIMEOUT)
    |                                     |
    |   if auth required and failed:      |
    |<-- {"type":"error","error":         |
    |     "auth_failed"} ----------------|
    |<-- close(4001) --------------------|
    |                                     |
    |   on success:                       |
    |   register with ConnectionManager   |
    |   emit WsClientConnected to outbox  |
    |                                     |
    |<-- {"type":"connected",             |
    |     "client_id":"...",              |
    |     "authenticated":true/false} ----|
    |                                     |
    |         [message loop]              |
    |--- {"type":"ping"} --------------->|
    |<-- {"type":"pong"} ----------------|
    |                                     |
    |--- {"type":"unknown"} ------------>|
    |<-- {"type":"error",                 |
    |     "error":"unknown_type"} -------|
    |                                     |
    |         [disconnect]                |
    |   emit WsClientDisconnected         |
    |   unregister from manager           |
```

## Identity Model

The `client_id` from the URL path is the primary identity -- an opaque string
representing a device, sensor, or hardware unit (not a DB user). Auth is an
optional layer on top that links a connection to a `User` record.

```
  /ws/device-ABC-123       client_id = "device-ABC-123", user = None (NoAuth)
  /ws/sensor-7?token=xxx   client_id = "sensor-7",       user = <User>
```

One `client_id` can have multiple simultaneous connections (multiple tabs, reconnects).
The `ConnectionManager` tracks them as a `set[ConnectionState]` per `client_id`.

## Message Routing

Register handlers with `@ws_message_handler`. Messages are freeform JSON with a
`type` field for dispatch.

```python
from app.ws.messages import ws_message_handler, MessageContext

@ws_message_handler("order.submit", model=OrderSubmitPayload)
async def handle_order(payload: OrderSubmitPayload, ctx: MessageContext):
    event = OrderSubmitted(client_id=ctx.conn.client_id, ...)
    async with ctx.session_factory() as session:
        await emit_event(event, session)
        await session.commit()

@ws_message_handler("subscribe")
async def handle_subscribe(payload: dict, ctx: MessageContext):
    topic = payload.get("topic")
    ctx.conn.subscriptions.add(topic)
```

The `model` parameter is optional. When provided, the raw JSON is validated through
the Pydantic model before being passed to the handler. Validation errors are sent
back to the client automatically.

## Outbound Push

Events with `target_client_ids` flow through the normal outbox pipeline and are
pushed to WebSocket clients by the `ws_push` handler group.

```
  Any event source          Outbox        Relay       RabbitMQ       ws_push handler
       |                      |             |            |                |
       |-- emit_event ------->|             |            |                |
       |   (target_client_    |             |            |                |
       |    ids=["dev-1"])    |-- relay --->|-- pub ---->|-- deliver ---->|
       |                      |             |            |                |
       |                      |             |            |         ConnectionManager
       |                      |             |            |         .send_to_clients()
       |                      |             |            |                |
       |                      |             |            |                v
       |                      |             |            |          WebSocket client
```

Broadcast (all clients): set `target_client_ids = []` (empty list).

### Multi-Instance Fan-Out

When running multiple WS server instances, each has its own `ConnectionManager`.
RabbitMQ delivers `ws_push` events to all instances (each binds `ws.#`), and each
instance pushes to its locally connected clients.

## Heartbeat

Application-level heartbeat via `HeartbeatManager`:

- Clients send `{"type": "ping"}`, server responds `{"type": "pong"}`
- Any client activity updates `last_seen` on the connection
- A background reaper closes connections idle longer than `WS_HEARTBEAT_TIMEOUT`
- This is separate from protocol-level WebSocket ping/pong (handled by uvicorn)

## Event Types

```python
# Base classes for custom WS events
class WebSocketInbound(Event):   # has client_id
    client_id: str

class WebSocketOutbound(Event):  # has target_client_ids
    target_client_ids: list[str] = []

# Built-in events (emitted automatically by the endpoint)
WsClientConnected     # event_type = "ws.client.connected"
WsClientDisconnected  # event_type = "ws.client.disconnected"
```

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Router, `init_ws()` / `shutdown_ws()` lifecycle, `get_manager()` |
| `manager.py` | `ConnectionManager`, `ConnectionState` (in-memory registry) |
| `auth.py` | `NoAuth`, `HttpBasicAuth`, `QueryTokenAuth`, `FirstMessageAuth`, `create_authenticator()` |
| `messages.py` | `MessageRouter`, `@ws_message_handler`, `MessageContext` |
| `heartbeat.py` | `HeartbeatManager` (background stale connection reaper) |
| `endpoint.py` | `websocket_endpoint()` -- full connection lifecycle handler |
| `events.py` | `WebSocketInbound`, `WebSocketOutbound`, `WsClientConnected`, `WsClientDisconnected` |
| `handlers.py` | Built-in `ping` handler + `ws_push` event handler |

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `WS_ENABLED` | `true` | Enable/disable WebSocket server |
| `WS_AUTH_METHOD` | `none` | `none`, `basic`, `token`, `first_message` |
| `WS_AUTH_TIMEOUT` | `5` | Seconds to wait for first-message auth |
| `WS_HEARTBEAT_INTERVAL` | `30` | Seconds between heartbeat reaper runs |
| `WS_HEARTBEAT_TIMEOUT` | `10` | Seconds of inactivity before connection reap |

## Testing

Endpoint integration tests use Starlette's sync `TestClient` (not httpx) since
WebSocket testing requires the ASGI transport to handle the protocol upgrade.
Dependencies (`emit_event`, `async_session_factory`, `get_manager`) are patched
at their source modules.

```python
from starlette.testclient import TestClient as StarletteTestClient

with client.websocket_connect("/ws/test-device") as ws:
    data = ws.receive_json()
    assert data["type"] == "connected"

    ws.send_json({"type": "ping"})
    data = ws.receive_json()
    assert data["type"] == "pong"
```
