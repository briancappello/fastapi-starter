import functools
import inspect

from fastapi import Depends

from app.config import Config
from app.db import models

from .dependencies import fastapi_users


def require_user(
    func=None,
    *,
    is_active: bool = True,
    is_superuser: bool = False,
):
    """
    Authorization required decorator for views.

    If the decorated view has an arg/kwarg with a type annotation of models.User,
    the current user will be injected automatically.

    Raises 401 if no user, or 403 if insufficient permissions.
    """

    def decorator(func_inner):
        dependency = fastapi_users.current_user(
            active=is_active,
            verified=Config.AUTH_REQUIRE_USER_VERIFIED,
            superuser=is_superuser,
        )

        @functools.wraps(func_inner)
        async def wrapper(*args, **kwargs):
            # Consume the injected dependency
            user_obj = kwargs.pop("__auth_user", None)

            # Inject user into arguments if expected
            sig = inspect.signature(func_inner)
            for name, param in sig.parameters.items():
                if param.annotation == models.User:
                    kwargs[name] = user_obj
                    break

            if inspect.iscoroutinefunction(func_inner):
                return await func_inner(*args, **kwargs)
            return func_inner(*args, **kwargs)

        sig = inspect.signature(func_inner)
        params = list(sig.parameters.values())

        # Remove parameters annotated with User from the wrapper signature
        # so FastAPI doesn't try to resolve them as query/body parameters
        params = [p for p in params if p.annotation != models.User]

        # Create a new parameter for the dependency
        new_param = inspect.Parameter(
            "__auth_user",
            inspect.Parameter.KEYWORD_ONLY,
            default=Depends(dependency),
            annotation=models.User,
        )

        # Add the new parameter to the signature
        new_params = params + [new_param]
        new_sig = sig.replace(parameters=new_params)
        wrapper.__signature__ = new_sig

        return wrapper

    if func:
        return decorator(func)

    return decorator
