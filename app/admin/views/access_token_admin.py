from app.db.models import AccessToken

from .base import BaseModelView


class AccessTokenAdmin(BaseModelView, model=AccessToken):
    can_create = False
    can_edit = False

    column_list = (
        AccessToken.user,
        AccessToken.token,
        AccessToken.created_at,
    )
    column_details_list = (
        AccessToken.user,
        AccessToken.token,
        AccessToken.created_at,
    )
