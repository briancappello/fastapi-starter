from app.db.models import AccessToken

from .base import BaseModelView


class AccessTokenAdmin(BaseModelView, model=AccessToken):
    column_list = (
        AccessToken.user,
        AccessToken.token,
        AccessToken.created_at,
    )
