import asyncio
import functools

from . import click


def async_command(f=None):
    """
    Decorator to add async support to click commands. Must be last:

        @click.command()
        @click.option(...)
        @async_command
        async def do_stuff():
            pass
    """
    if f is None:
        return async_command

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))

    return wrapper


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context):
    if ctx.invoked_subcommand is None:
        ctx.invoke(ctx.command.get_command(ctx, "dev"))


@main.group()
def users():
    """Users management"""
    pass
