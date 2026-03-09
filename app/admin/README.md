# Admin Panel

sqladmin-based administration interface mounted at `/admin`.

## Architecture

```
FastAPI app
  |
  +-- register_admin_views(app)       # called at startup
        |
        +-- admin.init_app(app)       # mounts at /admin
        +-- admin.add_view(UserAdmin)
        +-- admin.add_view(AccessTokenAdmin)
```

## Authentication

The admin panel reuses the application's existing `fastapi_users` auth infrastructure.
Only active, verified superusers can access the panel.

```
Login flow:
  1. POST /admin/login (email + password)
  2. UserManager.authenticate(credentials)
  3. Check is_active, is_verified (if required), is_superuser
  4. Create DB-backed AccessToken
  5. Store token in HTTP session
  6. Redirect to /admin

Request auth:
  1. Read token from session
  2. Validate via fastapi_users authenticator
  3. Allow or deny
```

## Files

| File | Purpose |
|---|---|
| `__init__.py` | `Admin` class (deferred mount pattern), `register_admin_views()` entry point |
| `auth.py` | `AdminAuth` backend -- login, logout, per-request authentication |
| `formatters.py` | HTML link formatter for association proxy collections |
| `views/__init__.py` | Re-exports `UserAdmin`, `AccessTokenAdmin` |
| `views/base.py` | `BaseModelView` -- auto title-case labels, association proxy support |
| `views/user_admin.py` | `UserAdmin` -- list/detail/create/edit for `User` model |
| `views/access_token_admin.py` | `AccessTokenAdmin` -- read-only view for `AccessToken` model |

## Model Views

### UserAdmin

- **Filters**: `is_verified`, `is_active`, `is_superuser` (boolean)
- **List columns**: id, first_name, last_name, email, is_active, is_verified, is_superuser
- **Detail columns**: adds access_tokens, created_at, updated_at
- **Form**: excludes id, timestamps, and access_tokens

### AccessTokenAdmin

- **Read-only** (no create/edit)
- **Columns**: user, token, created_at

## Adding a New Admin View

```python
# app/admin/views/my_model_admin.py
from app.admin.views.base import BaseModelView
from app.db.models import MyModel

class MyModelAdmin(BaseModelView, model=MyModel):
    column_list = [MyModel.id, MyModel.name, MyModel.created_at]
```

Register it in `app/admin/__init__.py`:

```python
from .views.my_model_admin import MyModelAdmin

def register_admin_views(app):
    admin.init_app(app)
    admin.add_view(UserAdmin)
    admin.add_view(AccessTokenAdmin)
    admin.add_view(MyModelAdmin)  # add here
```
