"""Tests for admin user CRUD operations."""

import pytest

from httpx import AsyncClient

from app.db.models import User


class TestUserList:
    """Tests for user list view."""

    async def test_list_users(self, admin_client: AsyncClient, superuser: User,):
        """Test listing users shows the superuser."""
        response = await admin_client.get("/admin/user/list")
        assert response.status_code == 200
        # Should show the admin user in the list
        assert superuser.email in response.text

    async def test_list_shows_multiple_users(
        self, admin_client: AsyncClient, superuser: User, user: User,
    ):
        """Test listing users shows multiple users."""
        response = await admin_client.get("/admin/user/list")
        assert response.status_code == 200
        assert superuser.email in response.text
        assert user.email in response.text

    async def test_list_shows_user_status_columns(
        self, admin_client: AsyncClient, superuser: User,
    ):
        """Test that list view shows status columns."""
        response = await admin_client.get("/admin/user/list")
        assert response.status_code == 200
        # Check for column headers (these may vary by sqladmin version)
        html = response.text.lower()
        assert "email" in html
        # Status columns should be present
        assert "verified" in html or "is_verified" in html
        assert "active" in html or "is_active" in html


class TestUserDetail:
    """Tests for user detail view."""

    async def test_view_user_details(self, admin_client: AsyncClient, superuser: User):
        """Test viewing user details."""
        response = await admin_client.get(f"/admin/user/details/{superuser.id}")
        assert response.status_code == 200
        assert superuser.email in response.text
        assert superuser.first_name in response.text
        assert superuser.last_name in response.text

    async def test_view_nonexistent_user(self, admin_client: AsyncClient):
        """Test viewing non-existent user returns 404."""
        response = await admin_client.get("/admin/user/details/99999")
        assert response.status_code == 404


class TestUserCreate:
    """Tests for user creation via admin."""

    async def test_create_page_accessible(self, admin_client: AsyncClient):
        """Test that create user page is accessible."""
        response = await admin_client.get("/admin/user/create")
        assert response.status_code == 200
        # Should have a form
        assert "<form" in response.text.lower()

    async def test_create_user(self, admin_client: AsyncClient, session):
        """Test creating a new user via admin."""
        response = await admin_client.post(
            "/admin/user/create",
            data={
                "email": "newadminuser@example.com",
                "hashed_password": "somehash",  # Admin sets hashed password directly
                "first_name": "New",
                "last_name": "User",
                "is_active": "on",
                "is_verified": "on",
                "is_superuser": "",
            },
            follow_redirects=False,
        )
        # Should redirect to list or detail page on success
        assert response.status_code in (302, 303)

        # Verify user was created
        from sqlalchemy import select

        from app.db.models import User

        result = await session.execute(
            select(User).where(User.email == "newadminuser@example.com")
        )
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.first_name == "New"
        assert user.last_name == "User"
        assert user.is_active is True
        assert user.is_verified is True
        assert user.is_superuser is False

    async def test_create_user_duplicate_email_fails(
        self, admin_client: AsyncClient, superuser: User,
    ):
        """Test creating user with duplicate email fails."""
        response = await admin_client.post(
            "/admin/user/create",
            data={
                "email": superuser.email,  # Already exists
                "hashed_password": "somehash",
                "first_name": "Duplicate",
                "last_name": "User",
                "is_active": "on",
            },
            follow_redirects=False,
        )
        # Should fail - either stay on form with error or return error status
        # SQLAdmin may show form with error message (200) or return 400
        assert response.status_code in (200, 400, 500)


class TestUserEdit:
    """Tests for user edit functionality."""

    async def test_edit_page_accessible(self, admin_client: AsyncClient, user: User,):
        """Test that edit user page is accessible."""
        response = await admin_client.get(f"/admin/user/edit/{user.id}")
        assert response.status_code == 200
        # Should show current values
        assert user.email in response.text

    async def test_edit_user_name(self, admin_client: AsyncClient, user: User, session,):
        """Test editing user's name."""
        response = await admin_client.post(
            f"/admin/user/edit/{user.id}",
            data={
                "email": user.email,
                "hashed_password": user.hashed_password,
                "first_name": "Updated",
                "last_name": "Name",
                "is_active": "on" if user.is_active else "",
                "is_verified": "on" if user.is_verified else "",
                "is_superuser": "on" if user.is_superuser else "",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        # Refresh and verify
        await session.refresh(user)
        assert user.first_name == "Updated"
        assert user.last_name == "Name"

    async def test_edit_user_status(self, admin_client: AsyncClient, user: User, session,):
        """Test editing user's active status."""
        # Deactivate the user
        response = await admin_client.post(
            f"/admin/user/edit/{user.id}",
            data={
                "email": user.email,
                "hashed_password": user.hashed_password,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "is_active": "",  # Unchecked = inactive
                "is_verified": "on" if user.is_verified else "",
                "is_superuser": "on" if user.is_superuser else "",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        # Refresh and verify
        await session.refresh(user)
        assert user.is_active is False

    async def test_edit_nonexistent_user(self, admin_client: AsyncClient):
        """Test editing non-existent user returns 404."""
        response = await admin_client.get("/admin/user/edit/99999")
        assert response.status_code == 404


class TestUserDelete:
    """Tests for user deletion."""

    async def test_delete_user(self, admin_client: AsyncClient, user_manager, session):
        """Test deleting a user."""
        from app.schema import UserCreate

        # Create a user to delete
        to_delete = await user_manager.create(
            UserCreate(
                email="todelete@example.com",
                password="password",
                first_name="To",
                last_name="Delete",
            )
        )
        user_id = to_delete.id

        # Flush to ensure user is visible to other queries within the session
        await session.flush()

        # SQLAdmin uses DELETE method with query params: DELETE /admin/user/delete?pks=<id>
        response = await admin_client.delete(
            f"/admin/user/delete?pks={user_id}",
            follow_redirects=False,
        )
        # Should return 200 OK on successful deletion
        assert response.status_code == 200, f"Delete failed with {response.status_code}"

        # Verify user was deleted
        from sqlalchemy import select

        from app.db.models import User

        # Expire the session cache to see the deletion
        session.expire_all()
        result = await session.execute(select(User).where(User.id == user_id))
        assert result.scalar_one_or_none() is None

    async def test_delete_nonexistent_user(self, admin_client: AsyncClient):
        """Test deleting non-existent user returns 404."""
        response = await admin_client.delete(
            "/admin/user/delete?pks=99999",
            follow_redirects=False,
        )
        assert response.status_code == 404

    async def test_delete_wrong_method(self, admin_client: AsyncClient, user: User):
        """Test that non-DELETE request to delete endpoint returns 405."""
        response = await admin_client.get(
            f"/admin/user/delete?pks={user.id}",
            follow_redirects=False,
        )
        assert response.status_code == 405
