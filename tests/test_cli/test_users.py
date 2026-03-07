"""Tests for CLI user management commands."""

import json

import pytest

from click.testing import CliRunner
from sqlalchemy.ext.asyncio import AsyncSession

# Import the commands to register them with the users group
import app.cli.users  # noqa: F401

from app.cli.groups import main
from app.db.managers import UserModelManager
from app.db.models import User


@pytest.fixture
def patch_session_factory(monkeypatch, session: AsyncSession):
    """Patch async_session_factory to use the test session."""
    import sys

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def patched_session_factory():
        yield session

    # Patch where async_session_factory is imported in the CLI users module
    # The module is stored in sys.modules with its full path
    users_module = sys.modules.get("app.cli.users")
    if users_module:
        monkeypatch.setattr(
            users_module, "async_session_factory", patched_session_factory
        )
    yield


@pytest.fixture
def cli_runner():
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def cli(patch_session_factory, cli_runner):
    """CLI runner with patched session factory."""
    return cli_runner


class TestUsersGroup:
    """Tests for the users command group."""

    def test_users_help(self, cli_runner):
        """Test that users --help works."""
        result = cli_runner.invoke(main, ["users", "--help"])
        assert result.exit_code == 0
        assert "Users management" in result.output
        assert "create" in result.output
        assert "list" in result.output
        assert "activate" in result.output
        assert "deactivate" in result.output
        assert "verify" in result.output
        assert "delete" in result.output

    def test_users_help_short_flag(self, cli_runner):
        """Test that users -h works."""
        result = cli_runner.invoke(main, ["-h"])
        assert result.exit_code == 0
        assert "users" in result.output


class TestCreateCommand:
    """Tests for the 'users create' command."""

    def test_create_help(self, cli_runner):
        """Test that users create --help works."""
        result = cli_runner.invoke(main, ["users", "create", "--help"])
        assert result.exit_code == 0
        assert "Create a new user" in result.output
        assert "--email" in result.output
        assert "--first-name" in result.output
        assert "--last-name" in result.output
        assert "--password" in result.output

    def test_create_user(self, cli, session):
        """Test creating a new user."""
        result = cli.invoke(
            main,
            [
                "users",
                "create",
                "--email",
                "newuser@example.com",
                "--first-name",
                "New",
                "--last-name",
                "User",
                "--password",
                "securepassword123",
            ],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "User created:" in result.output
        assert "newuser@example.com" in result.output

        # Parse the JSON output to verify user data
        json_start = result.output.find("{")
        json_str = result.output[json_start:]
        user_data = json.loads(json_str)
        assert user_data["email"] == "newuser@example.com"
        assert user_data["first_name"] == "New"
        assert user_data["last_name"] == "User"
        assert user_data["is_verified"] is True  # default
        assert user_data["is_superuser"] is False  # default

    def test_create_user_no_verify(self, cli, session):
        """Test creating a user with --no-verify flag."""
        result = cli.invoke(
            main,
            [
                "users",
                "create",
                "--email",
                "unverified@example.com",
                "--first-name",
                "Unverified",
                "--last-name",
                "User",
                "--password",
                "password123",
                "--no-verify",
            ],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"

        json_start = result.output.find("{")
        json_str = result.output[json_start:]
        user_data = json.loads(json_str)
        assert user_data["is_verified"] is False

    def test_create_superuser(self, cli, session):
        """Test creating a superuser."""
        result = cli.invoke(
            main,
            [
                "users",
                "create",
                "--email",
                "admin@example.com",
                "--first-name",
                "Admin",
                "--last-name",
                "User",
                "--password",
                "adminpass123",
                "--superuser",
            ],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"

        json_start = result.output.find("{")
        json_str = result.output[json_start:]
        user_data = json.loads(json_str)
        assert user_data["is_superuser"] is True

    def test_create_missing_required_fields(self, cli_runner):
        """Test that create fails when some required fields are missing."""
        import app.cli.users  # noqa: F401

        from app.cli.groups import main

        result = cli_runner.invoke(
            main,
            ["users", "create", "--email", "test@example.com"],
        )
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_create_no_args_shows_help(self, cli_runner):
        """Test that create with no arguments shows help instead of error."""
        import app.cli.users  # noqa: F401

        from app.cli.groups import main

        result = cli_runner.invoke(main, ["users", "create"])
        assert result.exit_code == 0
        assert "Create a new user" in result.output
        assert "--email" in result.output
        assert "--first-name" in result.output


class TestListCommand:
    """Tests for the 'users list' command."""

    def test_list_help(self, cli_runner):
        """Test that users list --help works."""
        result = cli_runner.invoke(main, ["users", "list", "--help"])
        assert result.exit_code == 0
        assert "List all users" in result.output

    def test_list_empty(self, cli, session):
        """Test listing users when none exist."""
        result = cli.invoke(main, ["users", "list"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        # Should show headers even with no data
        assert "Email" in result.output

    def test_list_with_users(self, cli, session, user):
        """Test listing users when users exist."""
        result = cli.invoke(main, ["users", "list"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert user.email in result.output
        assert user.first_name in result.output
        assert user.last_name in result.output

    def test_list_multiple_users(self, cli, session, user, superuser):
        """Test listing multiple users."""
        result = cli.invoke(main, ["users", "list"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert user.email in result.output
        assert superuser.email in result.output


class TestActivateCommand:
    """Tests for the 'users activate' command."""

    def test_activate_help(self, cli_runner):
        """Test that users activate --help works."""
        result = cli_runner.invoke(main, ["users", "activate", "--help"])
        assert result.exit_code == 0
        assert "Activate a user" in result.output
        assert "--email" in result.output

    async def test_activate_user(self, cli, session):
        """Test activating an inactive user."""
        # Create an inactive user directly in the database
        inactive_user = User(
            email="inactive@example.com",
            hashed_password="hashedpw",
            first_name="Inactive",
            last_name="User",
            is_active=False,
        )
        session.add(inactive_user)
        await session.commit()

        result = cli.invoke(
            main,
            ["users", "activate", "--email", "inactive@example.com"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "activated" in result.output

        # Verify user is now active
        await session.refresh(inactive_user)
        assert inactive_user.is_active is True


class TestDeactivateCommand:
    """Tests for the 'users deactivate' command."""

    def test_deactivate_help(self, cli_runner):
        """Test that users deactivate --help works."""
        result = cli_runner.invoke(main, ["users", "deactivate", "--help"])
        assert result.exit_code == 0
        assert "Deactivate a user" in result.output

    async def test_deactivate_user(self, cli, session, user):
        """Test deactivating an active user."""
        assert user.is_active is True

        result = cli.invoke(
            main,
            ["users", "deactivate", "--email", user.email],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "deactivated" in result.output

        # Verify user is now inactive
        await session.refresh(user)
        assert user.is_active is False


class TestVerifyCommand:
    """Tests for the 'users verify' command."""

    def test_verify_help(self, cli_runner):
        """Test that users verify --help works."""
        result = cli_runner.invoke(main, ["users", "verify", "--help"])
        assert result.exit_code == 0
        assert "Verify a user" in result.output

    async def test_verify_user(self, cli, session):
        """Test verifying an unverified user."""
        # Create an unverified user
        unverified_user = User(
            email="unverified@example.com",
            hashed_password="hashedpw",
            first_name="Unverified",
            last_name="User",
            is_verified=False,
        )
        session.add(unverified_user)
        await session.commit()

        result = cli.invoke(
            main,
            ["users", "verify", "--email", "unverified@example.com"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "verified" in result.output

        # Verify user is now verified
        await session.refresh(unverified_user)
        assert unverified_user.is_verified is True


class TestSetPasswordCommand:
    """Tests for the 'users set-password' command."""

    def test_set_password_help(self, cli_runner):
        """Test that users set-password --help works."""
        result = cli_runner.invoke(main, ["users", "set-password", "--help"])
        assert result.exit_code == 0
        assert "--email" in result.output
        assert "--password" in result.output

    async def test_set_password(self, cli, session, user):
        """Test setting a user's password."""
        old_password_hash = user.hashed_password

        result = cli.invoke(
            main,
            [
                "users",
                "set-password",
                "--email",
                user.email,
                "--password",
                "newpassword123",
            ],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"

        # Verify password was changed
        await session.refresh(user)
        assert user.hashed_password != old_password_hash


class TestDeleteCommand:
    """Tests for the 'users delete' command."""

    def test_delete_help(self, cli_runner):
        """Test that users delete --help works."""
        result = cli_runner.invoke(main, ["users", "delete", "--help"])
        assert result.exit_code == 0
        assert "Delete a user" in result.output

    async def test_delete_user(self, cli, session):
        """Test deleting a user."""
        # Create a user to delete
        user_to_delete = User(
            email="todelete@example.com",
            hashed_password="hashedpw",
            first_name="Delete",
            last_name="Me",
        )
        session.add(user_to_delete)
        await session.commit()
        user_id = user_to_delete.id

        result = cli.invoke(
            main,
            ["users", "delete", "--email", "todelete@example.com"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "deleted" in result.output

        # Expire the session cache so we get fresh data from DB
        session.expire_all()

        # Verify user no longer exists
        manager = UserModelManager(session)
        deleted_user = await manager.get(user_id)
        assert deleted_user is None
