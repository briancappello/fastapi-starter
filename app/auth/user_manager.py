from inspect import isawaitable
from typing import Callable

from fastapi import BackgroundTasks, Request
from fastapi_users import BaseUserManager, IntegerIDMixin
from fastapi_users.db import BaseUserDatabase
from fastapi_users.password import PasswordHelperProtocol

from app.config import Config
from app.db import models
from app.mail import Email, get_fast_mail


class UserManager(IntegerIDMixin, BaseUserManager[models.User, int]):
    reset_password_token_secret = Config.SECRET_KEY
    verification_token_secret = Config.SECRET_KEY

    def __init__(
        self,
        user_db: BaseUserDatabase[models.User, int],
        password_helper: PasswordHelperProtocol | None = None,
        background_tasks: BackgroundTasks | None = None,
        send_emails: bool = True,
    ):
        super().__init__(user_db=user_db, password_helper=password_helper)
        self.background_tasks: BackgroundTasks | None = background_tasks
        self.send_emails = send_emails

    async def in_background(self, fn: Callable, *args, **kwargs) -> None:
        if self.background_tasks:
            self.background_tasks.add_task(fn, *args, **kwargs)
        else:
            r = fn(*args, **kwargs)
            if isawaitable(r):
                await r

    async def send_email(
        self,
        message: Email,
        request: Request | None = None,
    ) -> None:
        if not self.send_emails:
            return

        message.template_context["base_url"] = (
            str(request.base_url).rstrip("/") if request else Config.BASE_URL
        )
        await self.in_background(get_fast_mail().send_message, message)

    async def on_after_register(
        self,
        user: models.User,
        request: Request | None = None,
    ) -> None:
        await self.send_email(
            Email(
                subject=f"Welcome to {Config.SITE_NAME}",
                recipients=[user.email],
                template="email/user-registered.html",
                template_context={
                    "user": user,
                },
            ),
            request,
        )
        if not user.is_verified:
            await self.request_verify(user, request)

    async def on_after_request_verify(
        self,
        user: models.User,
        token: str,
        request: Request | None = None,
    ) -> None:
        await self.send_email(
            Email(
                subject="Verify Your Email Address",
                recipients=[user.email],
                template="email/user-request-verify.html",
                template_context={
                    "user": user,
                    "token": token,
                },
            ),
            request,
        )

    async def on_after_forgot_password(
        self,
        user: models.User,
        token: str,
        request: Request | None = None,
    ) -> None:
        await self.send_email(
            Email(
                subject="Forgot Password Request",
                recipients=[user.email],
                template="email/user-forgot-password.html",
                template_context={
                    "user": user,
                    "token": token,
                },
            ),
            request,
        )
