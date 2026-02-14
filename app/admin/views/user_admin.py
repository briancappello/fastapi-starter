from sqladmin.filters import BooleanFilter

from app.db.models import User

from .base import BaseModelView


class UserAdmin(BaseModelView, model=User):
    column_filters = (
        BooleanFilter(User.is_verified, title="Is Verified"),
        BooleanFilter(User.is_active, title="Is Active"),
        BooleanFilter(User.is_superuser, title="Is Superuser"),
    )
    column_list = (
        User.id,
        User.first_name,
        User.last_name,
        User.email,
        User.is_verified,
        User.is_active,
        User.is_superuser,
    )

    column_details_list = (
        User.id,
        User.first_name,
        User.last_name,
        User.email,
        User.is_verified,
        User.is_active,
        User.is_superuser,
        User.access_tokens,
    )
