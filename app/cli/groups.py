import asyncio
import functools
import warnings

from . import click


def async_command(f=None):
    """
    Deprecated: async support is now built into the Command class.

    Simply declare your callback as ``async def`` and it will be run
    automatically via ``asyncio.run()``. This decorator is kept only
    for backwards compatibility and will be removed in a future version.
    """
    if f is None:
        return async_command

    warnings.warn(
        "async_command is deprecated. The Command class now handles async "
        "callbacks automatically. Remove the @async_command decorator.",
        DeprecationWarning,
        stacklevel=2,
    )

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))

    return wrapper


@click.group(invoke_without_command=True)
@click.option("-p", "--port", type=int, default=None, help="Port to run the server on")
@click.pass_context
def main(ctx: click.Context, port: int | None):
    if ctx.invoked_subcommand is None:
        dev_cmd = ctx.command.get_command(ctx, "dev")
        kwargs = {}
        if port is not None:
            kwargs["port"] = port
        ctx.invoke(dev_cmd, **kwargs)


@main.group()
def users():
    """Users management"""
    pass
