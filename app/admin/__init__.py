from fastapi import FastAPI
from sqladmin import Admin

from app.config import Config
from app.db import async_engine

from . import views
from .auth import AdminAuth


def register_admins(app: FastAPI) -> None:
    admin = Admin(
        app,
        title=f"{Config.SITE_NAME} Admin",
        engine=async_engine,
        templates_dir=Config.TEMPLATE_DIR,
        authentication_backend=AdminAuth(secret_key=Config.SECRET_KEY),
    )

    admin.add_view(views.UserAdmin)
    admin.add_view(views.AccessTokenAdmin)
