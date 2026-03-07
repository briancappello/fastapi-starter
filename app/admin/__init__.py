from typing import (
    Sequence,
)

from fastapi import FastAPI
from sqladmin import Admin as BaseAdmin
from sqladmin._types import ENGINE_TYPE
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.middleware import Middleware

from app.config import Config
from app.db import async_session_factory

from . import views
from .auth import AdminAuth


class Admin(BaseAdmin):
    def __init__(
        self,
        app: Starlette | None = None,
        engine: ENGINE_TYPE | None = None,
        session_maker: sessionmaker | async_sessionmaker | None = None,
        base_url: str = "/admin",
        title: str = "Admin",
        logo_url: str | None = None,
        favicon_url: str | None = None,
        middlewares: Sequence[Middleware] | None = None,
        debug: bool = False,
        templates_dir: str = "templates",
        authentication_backend: AuthenticationBackend | None = None,
    ) -> None:
        # fake app to allow using the application factory pattern with init_app
        class FakeNoopApp(Starlette):
            def mount(self, *args, **kwargs):
                pass

        super().__init__(
            app=app or FakeNoopApp(),
            engine=engine,
            session_maker=session_maker,
            base_url=base_url,
            title=title,
            logo_url=logo_url,
            favicon_url=favicon_url,
            middlewares=middlewares,
            debug=debug,
            templates_dir=templates_dir,
            authentication_backend=authentication_backend,
        )

    def init_app(self, app: FastAPI):
        self.app = app
        self.app.mount(self.base_url, app=self.admin, name="admin")


admin = Admin(
    title=f"{Config.SITE_NAME} Admin",
    session_maker=async_session_factory,
    templates_dir=Config.TEMPLATE_DIR,
    authentication_backend=AdminAuth(secret_key=Config.SECRET_KEY),
)


def register_admin_views(app: FastAPI) -> None:
    admin.init_app(app)
    admin.add_view(views.UserAdmin)
    admin.add_view(views.AccessTokenAdmin)
