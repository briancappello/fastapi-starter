"""Admin test fixtures."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import admin as admin_instance
from app.db.models import User
from app.schema import UserCreate

from ..client import TestClient


class MockSessionMaker:
    """
    Mock async_sessionmaker that returns the test session.

    This mimics the interface of async_sessionmaker so SQLAdmin
    can use it transparently.
    """

    # SQLAdmin checks this to determine if session maker is async
    class_ = AsyncSession

    def __init__(self, session: AsyncSession):
        self._session = session

    def __call__(self, **kwargs):
        # SQLAdmin calls session_maker(expire_on_commit=False)
        # We ignore kwargs since we're using an existing session
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        # Don't close the session - it's managed by the test fixture
        pass


@pytest.fixture(autouse=True)
def patch_admin_session_maker(session: AsyncSession):
    """
    Patch the admin's session_maker to use our test session.

    This runs automatically for all admin tests.
    SQLAdmin copies session_maker to each ModelView, so we need to patch all of them.
    """
    mock_session_maker = MockSessionMaker(session)

    # Store originals
    original_admin_session_maker = admin_instance.session_maker
    original_view_session_makers = {}

    # Patch admin
    admin_instance.session_maker = mock_session_maker

    # Patch all registered view instances
    for view in admin_instance.views:
        original_view_session_makers[view] = view.session_maker
        view.session_maker = mock_session_maker

    yield

    # Restore originals
    admin_instance.session_maker = original_admin_session_maker
    for view, original in original_view_session_makers.items():
        view.session_maker = original


@pytest.fixture(autouse=True)
async def patch_admin_session(session):

    @asynccontextmanager
    async def _mock() -> AsyncGenerator[AsyncSession, None]:
        yield session

    with patch("app.admin.auth.async_session_factory", _mock):
        yield


@pytest.fixture
async def admin_client(
    client: TestClient, superuser: User
) -> AsyncGenerator[TestClient, None]:
    """
    Client logged into admin panel.

    Patches get_session for admin auth and logs in as admin.
    """
    response = await client.post(
        "/admin/login",
        data={
            "username": superuser.email,
            "password": "password",  # From main conftest admin fixture
        },
        follow_redirects=False,
    )
    assert response.status_code in (
        302,
        303,
    ), f"Admin login failed: {response.text}"

    client.cookies.update(response.cookies)

    # Keep patch active for subsequent requests
    yield client
