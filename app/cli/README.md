# CLI

Command-line interface built on a custom Click implementation with async callbacks
and FastAPI-style `Depends()` dependency injection.

## Running

```bash
# Via pyproject.toml script entry point
app --help

# Or directly
python -m app.cli --help
```

## Command Tree

```
app                                   Root group (runs `dev` if no subcommand)
  -p/--port INT
  |
  +-- dev                             Run dev server (uvicorn with hot reload)
  |     -p/--port INT [8000]
  |
  +-- urls                            List all registered routes
  |
  +-- users                           User management
  |     +-- create                    Create a new user
  |     |     -e/--email (required)
  |     |     -f/--first-name (required)
  |     |     -l/--last-name (required)
  |     |     -p/--password (prompted)
  |     |     --verify/--no-verify [True]
  |     |     --superuser/--no-superuser [False]
  |     |     --send-email
  |     +-- activate -e/--email       Activate a user
  |     +-- deactivate -e/--email     Deactivate a user
  |     +-- verify -e/--email         Verify a user
  |     +-- set-password -e/--email   Reset password
  |     +-- delete -e/--email         Delete a user
  |     +-- list                      List all users (table)
  |
  +-- events                          Event system management
  |     +-- list                      List event types and handler groups
  |     +-- relay                     Run the outbox relay (DB -> RabbitMQ)
  |     +-- worker [GROUPS]           Run event handler workers
  |                                     GROUPS: comma-separated, or omit for all
  |
  +-- kafka                           Kafka consumer management
        +-- list                      List registered consumers
        +-- run [CONSUMERS]           Run consumers standalone
                                        CONSUMERS: comma-separated, or omit for all
```

## Custom Click Framework

`app/cli/click.py` replaces the standard `click` module with enhancements:

- **Async callbacks**: Commands can be `async def` -- automatically wrapped with `asyncio.run()`
- **`Depends()` DI**: Parameters with `Depends(async_session)` defaults are resolved automatically, matching FastAPI's pattern
- **`-h` for help**: Both `-h` and `--help` work
- **Argument help text**: `click.Argument` supports a `help` parameter
- **Default display**: Boolean flags show `[default: False]` in help

```python
from app.cli import click
from app.db import async_session
from fastapi import Depends

@main.command()
@click.option("-e", "--email", required=True)
async def my_command(email: str, session=Depends(async_session)):
    # session is injected automatically
    ...
```

### Dependency Overrides

For testing, `click.dependency_overrides` works like FastAPI's `app.dependency_overrides`:

```python
from app.cli.click import dependency_overrides
dependency_overrides[async_session] = lambda: mock_session
```

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Package init, re-exports all command groups |
| `click.py` | Custom Click with async + DI support |
| `groups.py` | Root `main` group and `users` subgroup |
| `main.py` | `dev` and `urls` commands |
| `users.py` | User CRUD commands |
| `events.py` | Event system commands (list, relay, worker) |
| `kafka.py` | Kafka consumer commands (list, run) |

## Long-Running Commands

`relay`, `worker`, and `kafka run` are long-running processes with graceful shutdown
via SIGINT/SIGTERM. They connect to external services (RabbitMQ, Kafka) and run
until interrupted.

```bash
# Run relay in foreground (Ctrl+C to stop)
app events relay

# Run specific handler groups
app events worker notifications,analytics

# Run specific Kafka consumers
app kafka run feed-one,feed-two
```
