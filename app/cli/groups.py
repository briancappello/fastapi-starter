import asyncio
import functools

import click


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


@click.group()
def main():
    pass


@main.group()
def users():
    """Users management"""
    pass
