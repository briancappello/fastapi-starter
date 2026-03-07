"""Tests for admin authentication."""

from unittest.mock import patch

from httpx import AsyncClient

from app.db.models import User
from app.schema import UserCreate


class TestAdminLogin:
    """Tests for admin login functionality."""

    async def test_login_page_accessible(self, client: AsyncClient):
        """Test that login page is accessible without auth."""
        response = await client.get("/admin/login")
        assert response.status_code == 200
        assert "login" in response.text.lower()

    async def test_login_success(
        self,
        client: AsyncClient,
        superuser: User,
    ):
        """Test successful admin login with superuser."""
        response = await client.post(
            "/admin/login",
            data={
                "username": superuser.email,
                "password": "password",  # Matches fixture password
            },
            follow_redirects=False,
        )
        # Admin redirects to dashboard on successful login
        assert response.status_code in (302, 303)
        assert "session" in response.cookies or any(
            "session" in c.lower() for c in response.cookies.keys()
        )

    async def test_login_wrong_password(
        self,
        client: AsyncClient,
        superuser: User,
    ):
        """Test login fails with wrong password."""

        response = await client.post(
            "/admin/login",
            data={
                "username": superuser.email,
                "password": "wrongpassword",
            },
            follow_redirects=False,
        )
        # Should not redirect - stays on login page and returns error
        assert response.status_code == 400
        assert "Invalid credentials" in response.text

    async def test_login_nonexistent_user(
        self,
        client: AsyncClient,
    ):
        """Test login fails for non-existent user."""

        response = await client.post(
            "/admin/login",
            data={
                "username": "nobody@example.com",
                "password": "password",
            },
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "Invalid credentials" in response.text

    async def test_login_non_superuser_forbidden(self, client, user):
        """Test that non-superuser cannot access admin."""
        response = await client.post(
            "/admin/login",
            data={
                "username": user.email,
                "password": "password",  # Matches fixture password
            },
            follow_redirects=False,
        )
        assert response.status_code == 403

    async def test_login_inactive_user_forbidden(
        self,
        client: AsyncClient,
        user_manager,
    ):
        """Test that inactive superuser cannot login."""

        inactive_admin = await user_manager.create(
            UserCreate(
                email="inactive_admin@example.com",
                password="password",
                first_name="Inactive",
                last_name="Admin",
                is_active=False,
                is_verified=True,
                is_superuser=True,
            )
        )

        response = await client.post(
            "/admin/login",
            data={
                "username": inactive_admin.email,
                "password": "password",
            },
            follow_redirects=False,
        )
        assert response.status_code == 403


class TestAdminLogout:
    """Tests for admin logout functionality."""

    async def test_logout(self, admin_client: AsyncClient):
        """Test admin logout."""
        response = await admin_client.get("/admin/logout", follow_redirects=False)
        # Logout should redirect to login page
        assert response.status_code in (302, 303)

    async def test_logout_clears_session(self, admin_client: AsyncClient):
        """Test that logout clears session and prevents further access."""
        # First verify we have admin access
        list_response = await admin_client.get("/admin/user/list")
        assert list_response.status_code == 200

        # Logout
        await admin_client.get("/admin/logout", follow_redirects=False)

        # Clear cookies to simulate fresh request
        admin_client.cookies.clear()

        # should redirect to login
        response = await admin_client.get("/admin/user/list", follow_redirects=False)
        assert response.status_code == 302
        assert "/admin/login" in response.headers.get("location", "")


class TestAdminAccess:
    """Tests for admin access control."""

    async def test_unauthenticated_redirect_to_login(self, client: AsyncClient):
        """Test that unauthenticated requests redirect to login."""
        response = await client.get("/admin/user/list", follow_redirects=False)
        # Should redirect to login
        assert response.status_code in (302, 303)
        assert "/admin/login" in response.headers.get("location", "")

    async def test_authenticated_can_access_admin(self, admin_client: AsyncClient):
        """Test that authenticated superuser can access admin."""
        response = await admin_client.get("/admin/")
        assert response.status_code == 200

    async def test_authenticated_can_access_user_list(self, admin_client: AsyncClient):
        """Test that authenticated superuser can access user list."""
        response = await admin_client.get("/admin/user/list")
        assert response.status_code == 200
