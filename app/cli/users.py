import click

from tabulate import tabulate

from app.auth import user_manager_factory
from app.db import async_session_factory
from app.db.managers import UserModelManager
from app.schema import UserCreate, UserRead, UserUpdate

from .groups import async_command, users


@users.command()
@click.option("-e", "--email", type=str, required=True)
@click.option("-f", "--first-name", type=str, required=True)
@click.option("-l", "--last-name", type=str, required=True)
@click.password_option("-p", "--password", type=str, prompt=True)
@click.option("--verify/--no-verify", default=True)
@click.option("--superuser/--no-superuser", default=False)
@click.option("--send-email", is_flag=True, default=False)
@async_command
async def create(
    email: str,
    first_name: str,
    last_name: str,
    password: str,
    verify: bool,
    superuser: bool,
    send_email: bool = False,
):
    """Create a new user."""
    async with async_session_factory() as session:
        user_manager = user_manager_factory(session, send_emails=send_email)
        user = await user_manager.create(
            UserCreate(
                email=email,
                first_name=first_name,
                last_name=last_name,
                password=password,
                is_verified=verify,
                is_superuser=superuser,
            )
        )
        click.echo("User created:")
        click.echo(UserRead.model_validate(user).model_dump_json(indent=2))


@users.command()
@click.option("-e", "--email", type=str, required=True)
@async_command
async def activate(
    email: str,
):
    """Activate a user."""
    async with async_session_factory() as session:
        user_manager = user_manager_factory(session, send_emails=False)
        user = await user_manager.get_by_email(email)
        user = await user_manager.update(
            UserUpdate(
                is_active=True,
            ),
            user,
        )
        click.echo(f"User {email} activated")


@users.command()
@click.option("-e", "--email", type=str, required=True)
@async_command
async def deactivate(
    email: str,
):
    """Deactivate a user."""
    async with async_session_factory() as session:
        user_manager = user_manager_factory(session, send_emails=False)
        user = await user_manager.get_by_email(email)
        user = await user_manager.update(
            UserUpdate(is_active=False),
            user,
        )
        click.echo(f"User {email} deactivated")


@users.command()
@click.option("-e", "--email", type=str, required=True)
@click.option("--send-email", is_flag=True, default=False)
@async_command
async def verify(
    email: str,
    send_email: bool = False,
):
    """Verify a user."""
    async with async_session_factory() as session:
        user_manager = user_manager_factory(session, send_emails=send_email)
        user = await user_manager.get_by_email(email)
        user = await user_manager.update(
            UserUpdate(is_verified=True),
            user,
        )
        await user_manager.on_after_verify(user)
        click.echo(f"User {email} verified")


@users.command()
@click.option("-e", "--email", type=str, required=True)
@click.password_option("-p", "--password", type=str, prompt=True)
@click.option("--send-email", is_flag=True, default=False)
@async_command
async def set_password(
    email: str,
    password: str,
    send_email: bool = False,
):
    """Activate a user."""
    async with async_session_factory() as session:
        user_manager = user_manager_factory(session, send_emails=send_email)
        user = await user_manager.get_by_email(email)
        user = await user_manager.update(
            UserUpdate(password=password),
            user,
        )
        await user_manager.on_after_reset_password(user)
        click.echo(f"User {email} activated")


@users.command()
@click.option("-e", "--email", type=str, required=True)
@click.option("--send-email", is_flag=True, default=False)
@async_command
async def delete(
    email: str,
    send_email: bool = False,
):
    """Delete a user."""
    async with async_session_factory() as session:
        user_manager = user_manager_factory(session, send_emails=send_email)
        user = await user_manager.get_by_email(email)
        await user_manager.delete(user)
        click.echo(f"User {email} deleted")


@users.command(name="list")
@async_command
async def list_users():
    """List all users."""
    async with async_session_factory() as session:
        all_users = await UserModelManager(session).all()

    headers = [
        "ID",
        "Email",
        "First Name",
        "Last Name",
        "Is Active",
        "Is Verified",
        "Is Superuser",
    ]
    rows = [
        [
            user.id,
            user.email,
            user.first_name,
            user.last_name,
            user.is_active,
            user.is_verified,
            user.is_superuser,
        ]
        for user in all_users
    ]
    click.echo(tabulate(rows, headers=headers, tablefmt="pretty"))
