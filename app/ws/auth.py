"""WebSocket authentication strategies.

Supports four modes selected via ``WS_AUTH_METHOD``:

- ``"none"``          -- no authentication; all connections accepted
- ``"basic"``         -- HTTP Basic auth in the upgrade ``Authorization`` header
- ``"token"``         -- bearer token in query parameter ``?token=xxx``
- ``"first_message"`` -- client sends ``{"type": "auth", "token": "xxx"}``
                         as the first WebSocket frame

Each strategy implements :class:`WsAuthenticator`.  The factory
function :func:`create_authenticator` returns the right one based on
the active config.
"""

from __future__ import annotations

import asyncio
import base64
import logging

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.config import Config


if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from app.db import models

logger = logging.getLogger(__name__)


@runtime_checkable
class WsAuthenticator(Protocol):
    """Protocol for WebSocket authentication strategies.

    ``authenticate`` is called after the WebSocket is *accepted*.
    For ``first_message`` auth, it consumes the first frame.
    For ``basic`` and ``token``, it inspects headers/query params
    that are available on the already-accepted socket.

    Returns a :class:`~app.db.models.User` on success, or ``None``
    on failure (the caller is responsible for closing the socket).
    """

    async def authenticate(
        self,
        ws: WebSocket,
        *,
        client_id: str,
    ) -> models.User | None:
        """Authenticate the WebSocket connection.

        Args:
            ws: The accepted WebSocket connection.
            client_id: The client identifier from the URL path.

        Returns:
            The authenticated User, or None if auth failed.
        """
        ...


class NoAuth:
    """No authentication -- all connections are accepted.

    Returns ``None`` for the user (anonymous).  The ``client_id`` from
    the URL path is still the primary identity.
    """

    async def authenticate(
        self,
        ws: WebSocket,
        *,
        client_id: str,
    ) -> models.User | None:
        return None


class HttpBasicAuth:
    """HTTP Basic authentication during the WebSocket upgrade.

    Reads the ``Authorization: Basic <base64>`` header from the
    upgrade request and validates credentials against the user DB
    via :class:`~app.auth.user_manager.UserManager`.
    """

    async def authenticate(
        self,
        ws: WebSocket,
        *,
        client_id: str,
    ) -> models.User | None:
        from fastapi_users.password import PasswordHelper

        from app.auth.factories import user_db_factory
        from app.db import async_session

        auth_header = ws.headers.get("authorization", "")
        if not auth_header.lower().startswith("basic "):
            logger.warning(
                f"WS auth failed for client_id={client_id}: "
                f"missing or invalid Authorization header"
            )
            return None

        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            email, password = decoded.split(":", 1)
        except Exception:
            logger.warning(
                f"WS auth failed for client_id={client_id}: malformed Basic credentials"
            )
            return None

        async with async_session() as session:
            user_db = user_db_factory(session)
            user = await user_db.get_by_email(email)
            if user is None:
                logger.warning(
                    f"WS auth failed for client_id={client_id}: user not found"
                )
                return None

            password_helper = PasswordHelper()
            verified, _updated_hash = password_helper.verify_and_update(
                password, user.hashed_password
            )
            if not verified:
                logger.warning(
                    f"WS auth failed for client_id={client_id}: invalid password"
                )
                return None

            if not user.is_active:
                logger.warning(
                    f"WS auth failed for client_id={client_id}: user is inactive"
                )
                return None

            return user


class QueryTokenAuth:
    """Token-based auth via query parameter.

    Client connects to ``/ws/{client_id}?token=xxx``.  The token is
    validated against the ``AccessToken`` table via the
    :class:`~fastapi_users.authentication.strategy.DatabaseStrategy`.
    """

    async def authenticate(
        self,
        ws: WebSocket,
        *,
        client_id: str,
    ) -> models.User | None:
        from app.auth.factories import db_strategy_factory
        from app.db import async_session

        token = ws.query_params.get("token")
        if not token:
            logger.warning(
                f"WS auth failed for client_id={client_id}: missing token query parameter"
            )
            return None

        async with async_session() as session:
            strategy = db_strategy_factory(session)
            user = await strategy.read_token(token, None)  # type: ignore[arg-type]
            if user is None:
                logger.warning(
                    f"WS auth failed for client_id={client_id}: invalid or expired token"
                )
                return None

            if not user.is_active:
                logger.warning(
                    f"WS auth failed for client_id={client_id}: user is inactive"
                )
                return None

            return user


class FirstMessageAuth:
    """Authentication via the first WebSocket message.

    After the connection is accepted, the client must send a JSON frame
    of the form ``{"type": "auth", "token": "xxx"}`` within
    ``WS_AUTH_TIMEOUT`` seconds.  The token is validated the same way
    as :class:`QueryTokenAuth`.
    """

    def __init__(self, timeout: float | None = None) -> None:
        self._timeout = timeout if timeout is not None else Config.WS_AUTH_TIMEOUT

    async def authenticate(
        self,
        ws: WebSocket,
        *,
        client_id: str,
    ) -> models.User | None:
        from app.auth.factories import db_strategy_factory
        from app.db import async_session

        try:
            data = await asyncio.wait_for(
                ws.receive_json(),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"WS auth failed for client_id={client_id}: "
                f"auth message timeout ({self._timeout}s)"
            )
            return None
        except Exception:
            logger.warning(
                f"WS auth failed for client_id={client_id}: "
                f"failed to receive auth message"
            )
            return None

        if not isinstance(data, dict):
            logger.warning(
                f"WS auth failed for client_id={client_id}: "
                f"auth message is not a JSON object"
            )
            return None

        if data.get("type") != "auth":
            logger.warning(
                f"WS auth failed for client_id={client_id}: "
                f"first message type is '{data.get('type')}', expected 'auth'"
            )
            return None

        token = data.get("token")
        if not token or not isinstance(token, str):
            logger.warning(
                f"WS auth failed for client_id={client_id}: "
                f"missing or invalid token in auth message"
            )
            return None

        async with async_session() as session:
            strategy = db_strategy_factory(session)
            user = await strategy.read_token(token, None)  # type: ignore[arg-type]
            if user is None:
                logger.warning(
                    f"WS auth failed for client_id={client_id}: invalid or expired token"
                )
                return None

            if not user.is_active:
                logger.warning(
                    f"WS auth failed for client_id={client_id}: user is inactive"
                )
                return None

            return user


_AUTHENTICATORS: dict[str, type[WsAuthenticator]] = {
    "none": NoAuth,  # type: ignore[dict-item]
    "basic": HttpBasicAuth,  # type: ignore[dict-item]
    "token": QueryTokenAuth,  # type: ignore[dict-item]
    "first_message": FirstMessageAuth,  # type: ignore[dict-item]
}


def create_authenticator(method: str | None = None) -> WsAuthenticator:
    """Create the authenticator for the configured method.

    Args:
        method: Override the config. If ``None``, reads from
                ``Config.WS_AUTH_METHOD``.

    Raises:
        ValueError: If the method is unknown.
    """
    method = method or Config.WS_AUTH_METHOD
    cls = _AUTHENTICATORS.get(method)
    if cls is None:
        valid = ", ".join(sorted(_AUTHENTICATORS))
        raise ValueError(f"Unknown WS_AUTH_METHOD: '{method}'. Valid options: {valid}")
    return cls()  # type: ignore[return-value]
