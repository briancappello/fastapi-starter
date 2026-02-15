from ..models import User
from .base import ModelManager


class UserModelManager(ModelManager[User]):
    model = User
