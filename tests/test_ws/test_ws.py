"""Tests for the WebSocket server module."""

from __future__ import annotations

import asyncio
import time

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pydantic import BaseModel

from app.ws.auth import (
    HttpBasicAuth,
    NoAuth,
    QueryTokenAuth,
    create_authenticator,
)
from app.ws.events import (
    WebSocketInbound,
    WebSocketOutbound,
    WsClientConnected,
    WsClientDisconnected,
)
from app.ws.manager import ConnectionManager, ConnectionState
from app.ws.messages import (
    MessageContext,
    MessageRouter,
    ws_message_handler,
)


# -----------------------------------------------------------------------
# ConnectionManager tests
# -----------------------------------------------------------------------


class TestConnectionManager:
    """Tests for ConnectionManager."""

    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self, manager, mock_ws):
        state = await manager.connect("device-1", mock_ws)
        assert state.client_id == "device-1"
        assert state.ws is mock_ws
        assert manager.connection_count == 1
        assert manager.client_count == 1
        assert "device-1" in manager.client_ids

        await manager.disconnect(state)
        assert manager.connection_count == 0
        assert manager.client_count == 0

    @pytest.mark.asyncio
    async def test_multiple_connections_same_client(self, manager):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        state1 = await manager.connect("device-1", ws1)
        state2 = await manager.connect("device-1", ws2)

        assert manager.connection_count == 2
        assert manager.client_count == 1

        await manager.disconnect(state1)
        assert manager.connection_count == 1
        assert manager.client_count == 1

        await manager.disconnect(state2)
        assert manager.connection_count == 0
        assert manager.client_count == 0

    @pytest.mark.asyncio
    async def test_multiple_clients(self, manager):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.connect("device-1", ws1)
        await manager.connect("device-2", ws2)

        assert manager.connection_count == 2
        assert manager.client_count == 2
        assert manager.client_ids == {"device-1", "device-2"}

    @pytest.mark.asyncio
    async def test_send_to_client(self, manager, mock_ws):
        await manager.connect("device-1", mock_ws)
        sent = await manager.send_to_client("device-1", {"type": "test"})
        assert sent == 1
        mock_ws.send_json.assert_called_once_with({"type": "test"})

    @pytest.mark.asyncio
    async def test_send_to_unknown_client(self, manager):
        sent = await manager.send_to_client("unknown", {"type": "test"})
        assert sent == 0

    @pytest.mark.asyncio
    async def test_send_to_multiple_connections(self, manager):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.connect("device-1", ws1)
        await manager.connect("device-1", ws2)

        sent = await manager.send_to_client("device-1", {"type": "test"})
        assert sent == 2
        ws1.send_json.assert_called_once()
        ws2.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_removes_stale_connections(self, manager):
        ws_good = AsyncMock()
        ws_stale = AsyncMock()
        ws_stale.send_json.side_effect = Exception("Connection closed")

        await manager.connect("device-1", ws_good)
        await manager.connect("device-1", ws_stale)
        assert manager.connection_count == 2

        sent = await manager.send_to_client("device-1", {"type": "test"})
        assert sent == 1
        assert manager.connection_count == 1

    @pytest.mark.asyncio
    async def test_broadcast(self, manager):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.connect("device-1", ws1)
        await manager.connect("device-2", ws2)

        sent = await manager.broadcast({"type": "broadcast"})
        assert sent == 2

    @pytest.mark.asyncio
    async def test_send_to_clients(self, manager):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()
        await manager.connect("device-1", ws1)
        await manager.connect("device-2", ws2)
        await manager.connect("device-3", ws3)

        sent = await manager.send_to_clients(
            ["device-1", "device-3"], {"type": "targeted"}
        )
        assert sent == 2
        ws1.send_json.assert_called_once()
        ws2.send_json.assert_not_called()
        ws3.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self, manager, mock_ws):
        state = await manager.connect("device-1", mock_ws)
        await manager.disconnect(state)
        # Second disconnect should not raise
        await manager.disconnect(state)
        assert manager.connection_count == 0


class TestConnectionState:
    """Tests for ConnectionState."""

    def test_touch_updates_last_seen(self):
        ws = AsyncMock()
        state = ConnectionState(client_id="device-1", ws=ws)
        original = state.last_seen
        time.sleep(0.01)
        state.touch()
        assert state.last_seen > original

    def test_default_values(self):
        ws = AsyncMock()
        state = ConnectionState(client_id="device-1", ws=ws)
        assert state.subscriptions == set()
        assert state.authenticated is False
        assert state.metadata == {}


# -----------------------------------------------------------------------
# MessageRouter tests
# -----------------------------------------------------------------------


class TestMessageRouter:
    """Tests for MessageRouter."""

    @pytest.fixture
    def router(self):
        return MessageRouter()

    @pytest.fixture
    def context(self):
        ws = AsyncMock()
        conn = ConnectionState(client_id="test-device", ws=ws)
        return MessageContext(
            conn=conn,
            manager=MagicMock(),
            session_factory=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_register_and_dispatch(self, router, context):
        handler = AsyncMock()
        router.register("test.msg", handler)

        result = await router.dispatch({"type": "test.msg", "data": "hello"}, context)
        assert result is True
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_unknown_type(self, router, context):
        result = await router.dispatch({"type": "unknown"}, context)
        assert result is False

    @pytest.mark.asyncio
    async def test_dispatch_missing_type(self, router, context):
        result = await router.dispatch({"data": "no type"}, context)
        assert result is False

    @pytest.mark.asyncio
    async def test_dispatch_with_model_validation(self, router, context):
        class TestPayload(BaseModel):
            type: str
            value: int

        handler = AsyncMock()
        router.register("typed.msg", handler, model=TestPayload)

        result = await router.dispatch({"type": "typed.msg", "value": 42}, context)
        assert result is True
        payload_arg = handler.call_args[0][0]
        assert isinstance(payload_arg, TestPayload)
        assert payload_arg.value == 42

    @pytest.mark.asyncio
    async def test_dispatch_with_model_validation_failure(self, router, context):
        class StrictPayload(BaseModel):
            type: str
            required_field: int

        handler = AsyncMock()
        router.register("strict.msg", handler, model=StrictPayload)

        result = await router.dispatch(
            {"type": "strict.msg"},
            context,  # missing required_field
        )
        assert result is True  # handled as error
        handler.assert_not_called()
        # Should have sent error back
        context.conn.ws.send_json.assert_called_once()
        error_msg = context.conn.ws.send_json.call_args[0][0]
        assert error_msg["type"] == "error"
        assert error_msg["error"] == "validation_error"

    @pytest.mark.asyncio
    async def test_dispatch_handler_exception(self, router, context):
        handler = AsyncMock(side_effect=ValueError("boom"))
        router.register("fail.msg", handler)

        result = await router.dispatch({"type": "fail.msg"}, context)
        assert result is True
        # Should have sent error back
        context.conn.ws.send_json.assert_called_once()
        error_msg = context.conn.ws.send_json.call_args[0][0]
        assert error_msg["type"] == "error"
        assert error_msg["error"] == "internal_error"

    def test_duplicate_registration_raises(self, router):
        handler1 = AsyncMock()
        handler2 = AsyncMock()
        router.register("dup.msg", handler1)
        with pytest.raises(ValueError, match="Duplicate"):
            router.register("dup.msg", handler2)

    def test_message_types_property(self, router):
        router.register("a", AsyncMock())
        router.register("b", AsyncMock())
        assert router.message_types == {"a", "b"}

    def test_len(self, router):
        assert len(router) == 0
        router.register("a", AsyncMock())
        assert len(router) == 1


class TestWsMessageHandlerDecorator:
    """Tests for the @ws_message_handler decorator."""

    def test_decorator_registers_handler(self):
        # The built-in "ping" handler should already be registered
        # (imported via app.ws.handlers)
        # Force import to trigger registration
        import app.ws.handlers  # noqa: F401

        from app.ws.messages import message_router

        assert message_router.get_handler("ping") is not None

    def test_decorator_preserves_function(self):
        router = MessageRouter()

        # Manually test decorator behavior
        async def my_handler(payload, ctx):
            pass

        # Register manually since the decorator uses the global singleton
        router.register("test.decorator", my_handler)
        reg = router.get_handler("test.decorator")
        assert reg is not None
        assert reg.handler is my_handler


# -----------------------------------------------------------------------
# Auth strategy tests
# -----------------------------------------------------------------------


class TestAuthStrategies:
    """Tests for WS auth strategy classes."""

    @pytest.mark.asyncio
    async def test_no_auth_returns_none(self):
        auth = NoAuth()
        ws = AsyncMock()
        result = await auth.authenticate(ws, client_id="device-1")
        assert result is None

    def test_create_authenticator_none(self):
        auth = create_authenticator("none")
        assert isinstance(auth, NoAuth)

    def test_create_authenticator_basic(self):
        auth = create_authenticator("basic")
        assert isinstance(auth, HttpBasicAuth)

    def test_create_authenticator_token(self):
        auth = create_authenticator("token")
        assert isinstance(auth, QueryTokenAuth)

    def test_create_authenticator_invalid(self):
        with pytest.raises(ValueError, match="Unknown WS_AUTH_METHOD"):
            create_authenticator("invalid")

    @pytest.mark.asyncio
    async def test_basic_auth_missing_header(self):
        auth = HttpBasicAuth()
        ws = AsyncMock()
        ws.headers = {}
        result = await auth.authenticate(ws, client_id="device-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_basic_auth_malformed_header(self):
        auth = HttpBasicAuth()
        ws = AsyncMock()
        ws.headers = {"authorization": "Basic !!!invalid!!!"}
        result = await auth.authenticate(ws, client_id="device-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_token_auth_missing_param(self):
        auth = QueryTokenAuth()
        ws = AsyncMock()
        ws.query_params = {}
        result = await auth.authenticate(ws, client_id="device-1")
        assert result is None


# -----------------------------------------------------------------------
# Event type tests
# -----------------------------------------------------------------------


class TestWsEvents:
    """Tests for WebSocket event types."""

    def test_ws_inbound_has_client_id(self):
        class TestInbound(WebSocketInbound):
            event_type = "ws.test.inbound"
            data: str

        event = TestInbound(client_id="device-1", data="hello", source="ws")
        assert event.client_id == "device-1"
        assert event.event_type == "ws.test.inbound"

    def test_ws_outbound_has_target_client_ids(self):
        class TestOutbound(WebSocketOutbound):
            event_type = "ws.test.outbound"
            payload: str

        event = TestOutbound(
            target_client_ids=["device-1", "device-2"],
            payload="hello",
            source="system",
        )
        assert event.target_client_ids == ["device-1", "device-2"]

    def test_ws_outbound_default_empty_targets(self):
        class TestOutbound(WebSocketOutbound):
            event_type = "ws.test.outbound2"

        event = TestOutbound(source="system")
        assert event.target_client_ids == []

    def test_ws_client_connected_event(self):
        event = WsClientConnected(client_id="device-1", source="ws")
        assert event.event_type == "ws.client.connected"

    def test_ws_client_disconnected_event(self):
        event = WsClientDisconnected(
            client_id="device-1", source="ws", duration_seconds=42.5
        )
        assert event.event_type == "ws.client.disconnected"
        assert event.duration_seconds == 42.5


# -----------------------------------------------------------------------
# Heartbeat tests
# -----------------------------------------------------------------------


class TestHeartbeat:
    """Tests for HeartbeatManager."""

    @pytest.mark.asyncio
    async def test_reap_stale_connections(self):
        from app.ws.heartbeat import HeartbeatManager

        manager = ConnectionManager()
        ws_stale = AsyncMock()
        ws_stale.close = AsyncMock()
        ws_fresh = AsyncMock()
        ws_fresh.close = AsyncMock()

        state_stale = await manager.connect("stale-device", ws_stale)
        state_fresh = await manager.connect("fresh-device", ws_fresh)

        # Make the stale connection look old
        state_stale.last_seen = time.monotonic() - 100

        heartbeat = HeartbeatManager(manager, interval=1, timeout=10)
        await heartbeat._reap_stale()

        assert manager.connection_count == 1
        assert "stale-device" not in manager.client_ids
        assert "fresh-device" in manager.client_ids
        ws_stale.close.assert_called_once()
        ws_fresh.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        from app.ws.heartbeat import HeartbeatManager

        manager = ConnectionManager()
        heartbeat = HeartbeatManager(manager, interval=0.1, timeout=1)

        await heartbeat.start()
        assert heartbeat._task is not None
        assert not heartbeat._task.done()

        await heartbeat.stop()
        assert heartbeat._task is None


# -----------------------------------------------------------------------
# Endpoint integration tests (via Starlette sync TestClient)
# -----------------------------------------------------------------------


class TestWebSocketEndpoint:
    """Integration tests for the /ws/{client_id} endpoint.

    Uses Starlette's sync TestClient which supports websocket_connect().
    The endpoint's emit_event calls are mocked out to avoid DB dependencies.
    """

    @pytest.fixture
    def ws_app(self):
        """Create a minimal FastAPI app with the WS endpoint and
        mocked dependencies, avoiding the full app lifespan."""
        from fastapi import FastAPI

        # Ensure built-in handlers (e.g. "ping") are registered in the global
        # message_router. In normal operation this happens inside init_ws(),
        # but that lifespan hook is not called by this lightweight test fixture.
        import app.ws.handlers  # noqa: F401

        from app.ws.endpoint import websocket_endpoint
        from app.ws.manager import ConnectionManager

        # Create a fresh manager for each test
        manager = ConnectionManager()

        # Create a minimal app with just the WS route
        test_app = FastAPI()
        test_app.add_api_websocket_route("/ws/{client_id}", websocket_endpoint)

        return test_app, manager

    @pytest.fixture
    def starlette_client(self, ws_app):
        """Starlette sync test client for WS testing."""
        from starlette.testclient import TestClient as StarletteTestClient

        test_app, manager = ws_app

        # Patch the WS module globals so the endpoint can find the manager,
        # and mock out emit_event to avoid DB calls.
        # The endpoint uses deferred imports inside the function body, so
        # we patch the source modules.
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        @asynccontextmanager
        async def mock_session_factory():
            yield mock_session

        with (
            patch("app.ws.get_manager", return_value=manager),
            patch("app.events.outbox.emit_event", new_callable=AsyncMock),
            patch("app.db.async_session_factory", mock_session_factory),
            patch("app.config.Config.WS_AUTH_METHOD", "none"),
        ):
            yield StarletteTestClient(test_app), manager

    def test_connect_and_receive_welcome(self, starlette_client):
        """Test basic WS connection with no auth."""
        client, manager = starlette_client
        with client.websocket_connect("/ws/test-device-001") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            assert data["client_id"] == "test-device-001"
            assert data["authenticated"] is False

    def test_ping_pong(self, starlette_client):
        """Test the built-in ping/pong heartbeat handler."""
        client, manager = starlette_client
        with client.websocket_connect("/ws/test-device-002") as ws:
            ws.receive_json()  # welcome
            ws.send_json({"type": "ping"})
            data = ws.receive_json()
            assert data["type"] == "pong"

    def test_unknown_message_type(self, starlette_client):
        """Test that unknown message types get an error response."""
        client, manager = starlette_client
        with client.websocket_connect("/ws/test-device-003") as ws:
            ws.receive_json()  # welcome
            ws.send_json({"type": "nonexistent"})
            data = ws.receive_json()
            assert data["type"] == "error"
            assert data["error"] == "unknown_type"

    def test_message_without_type(self, starlette_client):
        """Test that messages without 'type' get an error response."""
        client, manager = starlette_client
        with client.websocket_connect("/ws/test-device-004") as ws:
            ws.receive_json()  # welcome
            ws.send_json({"data": "no type field"})
            data = ws.receive_json()
            assert data["type"] == "error"
            assert data["error"] == "unknown_type"

    def test_manager_tracks_connection(self, starlette_client):
        """Test that the ConnectionManager tracks active connections."""
        client, manager = starlette_client
        with client.websocket_connect("/ws/tracked-device") as ws:
            ws.receive_json()  # welcome
            assert manager.connection_count >= 1
            assert "tracked-device" in manager.client_ids

        # After disconnect, should be cleaned up
        assert manager.connection_count == 0
        assert "tracked-device" not in manager.client_ids

    def test_manager_not_initialized(self):
        """Test endpoint returns 4503 when manager is None."""
        from fastapi import FastAPI
        from starlette.testclient import TestClient as StarletteTestClient

        from app.ws.endpoint import websocket_endpoint

        test_app = FastAPI()
        test_app.add_api_websocket_route("/ws/{client_id}", websocket_endpoint)

        with patch("app.ws.get_manager", return_value=None):
            sc = StarletteTestClient(test_app)
            with pytest.raises(Exception):
                # The server closes with code 4503 which raises an exception
                with sc.websocket_connect("/ws/test-device") as ws:
                    pass
