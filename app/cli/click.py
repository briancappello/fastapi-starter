"""
Drop-in replacement for the ``click`` module.

Usage::

    # before
    import click

    # after
    from app.cli import click

We override upstream to do the following:

- Accept ``-h`` and ``--help`` instead of just ``--help`` for showing help
- Support documenting help for ``click.Argument``
- Customize the formatting of the help output (arguments shown separately)
- Increase the max help string length to 120 characters
- Show default values for options automatically
- Show help instead of error when command with required params is invoked with no args
"""

import inspect as _inspect

import click

from click import *  # noqa: F401,F403
from click.formatting import join_options as _join_options


CLI_HELP_STRING_MAX_LEN = 120
DEFAULT_CONTEXT_SETTINGS = dict(
    help_option_names=("-h", "--help"),
    show_default=True,
)


def _update_ctx_settings(context_settings):
    """Merge user context_settings with our defaults (like -h for help)."""
    rv = DEFAULT_CONTEXT_SETTINGS.copy()
    if not context_settings:
        return rv
    rv.update(context_settings)
    return rv


class Command(click.Command):
    """
    Enhanced Command that displays arguments before the [OPTIONS] metavar
    and prints arguments in a separate section from options.

    Also shows help automatically when required options/arguments are missing.
    """

    def __init__(
        self,
        name,
        context_settings=None,
        callback=None,
        params=None,
        help=None,
        epilog=None,
        short_help=None,
        add_help_option=True,
        options_metavar="[OPTIONS]",
        **kwargs,
    ):
        super().__init__(
            name,
            callback=callback,
            params=params,
            help=help,
            epilog=epilog,
            short_help=short_help,
            add_help_option=add_help_option,
            context_settings=_update_ctx_settings(context_settings),
            options_metavar=options_metavar,
            **kwargs,
        )

    def make_context(self, info_name, args, parent=None, **extra):
        """
        Show help instead of error when required options/arguments are missing.

        If the command has required parameters and none were provided,
        display the help text instead of an error message.
        """
        # Check if we have required params and no args were provided
        has_required_params = any(getattr(p, "required", False) for p in self.params)

        if has_required_params and not args:
            # No arguments provided - check if any required options have no defaults
            # Create context without parsing to show help
            try:
                return super().make_context(info_name, args, parent, **extra)
            except click.MissingParameter:
                # Missing required param - show help instead
                ctx = super().make_context(
                    info_name,
                    ["--help"],  # Trigger help display
                    parent,
                    allow_extra_args=True,
                    allow_interspersed_args=False,
                    **extra,
                )
                return ctx

        return super().make_context(info_name, args, parent, **extra)

    def collect_usage_pieces(self, ctx):
        """Display args before the [OPTIONS] metavar in usage line."""
        rv = []
        for param in self.get_params(ctx):
            rv.extend(param.get_usage_pieces(ctx))
        rv.append(self.options_metavar)
        return rv

    def format_options(self, ctx, formatter):
        """Print arguments first in their own section, then options."""
        args = []
        opts = []
        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv is not None:
                if isinstance(param, click.Argument):
                    args.append(rv)
                else:
                    opts.append(rv)
        if args:
            with formatter.section("Arguments"):
                formatter.write_dl(args)
        if opts:
            with formatter.section(self.options_metavar):
                formatter.write_dl(opts)

    def get_short_help_str(self, limit=0):
        """Use a longer limit for short help strings."""
        return super().get_short_help_str(limit=CLI_HELP_STRING_MAX_LEN)


class GroupOverrideMixin:
    """Mixin for Group that applies our default context settings."""

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            context_settings=_update_ctx_settings(kwargs.pop("context_settings", None)),
            **kwargs,
        )
        self.subcommand_metavar = "COMMAND [<args>...]"
        self.subcommands_metavar = "COMMAND1 [<args>...] [COMMAND2 [<args>...]]"

    def command(self, *args, **kwargs):
        """Create a command using our enhanced Command class by default."""
        kwargs.setdefault("cls", Command)
        return super().command(*args, **kwargs)

    def collect_usage_pieces(self, ctx):
        """Display COMMAND metavar appropriately for chain vs non-chain."""
        if self.chain:
            rv = [self.subcommands_metavar]
        else:
            rv = [self.subcommand_metavar]
        rv.extend(click.Command.collect_usage_pieces(self, ctx))
        return rv

    def get_short_help_str(self, limit=0):
        """Use a longer limit for short help strings."""
        return super().get_short_help_str(limit=CLI_HELP_STRING_MAX_LEN)


class Group(GroupOverrideMixin, click.Group):
    """
    Enhanced Group that uses our custom Command class and context settings.
    """

    def group(self, *args, **kwargs):
        """Create a subgroup using our enhanced Group class by default."""
        kwargs.setdefault("cls", Group)
        return super().group(*args, **kwargs)


class Argument(click.Argument):
    """
    Enhanced Argument that supports a ``help`` parameter for documentation.

    By default, click.Argument does not support help text. This subclass adds:

    - ``help``: The help string to display for this argument
    - ``hidden``: Whether to hide this argument from help output
                  (defaults to True if no help is provided)

    Example::

        @click.command()
        @click.argument("name", help="The name to greet")
        def hello(name):
            click.echo(f"Hello {name}!")
    """

    def __init__(self, param_decls, required=None, help=None, hidden=None, **attrs):
        super().__init__(param_decls, required=required, **attrs)
        self.help = help
        self.hidden = hidden if hidden is not None else not help

    def make_metavar(self, ctx=None):
        """
        Format the metavar with angle brackets and optional markers.

        Examples (for argument named 'query'):
            Required:           <query>
            Optional:           [<query>]
            Required, nargs>1:  <query>, ...
            Optional, nargs>1:  [<query>, ...]
        """
        if self.metavar is not None:
            return self.metavar
        var = "" if self.required else "["
        var += "<" + self.name + ">"
        if self.nargs != 1:
            var += ", ..."
        if not self.required:
            var += "]"
        return var

    def get_help_record(self, ctx):
        """
        Return a tuple (formatted_arg, help_text) for help output.

        This is modeled after click.Option.get_help_record but adapted
        for positional arguments.
        """
        if self.hidden:
            return None

        any_prefix_is_slash = []

        def _write_opts(opts):
            rv, any_slashes = _join_options(opts)
            if any_slashes:
                any_prefix_is_slash[:] = [True]
            rv += ": " + self.make_metavar()
            return rv

        rv = [_write_opts(self.opts)]
        if self.secondary_opts:
            rv.append(_write_opts(self.secondary_opts))

        help_text = self.help or ""
        extra = []

        if self.default is not None and not self.required:
            if isinstance(self.default, (list, tuple)):
                default_string = ", ".join("%s" % d for d in self.default)
            elif _inspect.isfunction(self.default):
                default_string = "(dynamic)"
            else:
                default_string = self.default
            extra.append(f"default: {default_string}")

        if self.required:
            extra.append("required")

        if extra:
            help_text = "%s[%s]" % (
                help_text and help_text + "  " or "",
                "; ".join(extra),
            )

        return ((any_prefix_is_slash and "; " or " / ").join(rv), help_text)


class Option(click.Option):
    """
    Enhanced Option that always shows default values, including for flags.

    Click's default behavior hides defaults for boolean flags without
    secondary options (e.g., --flag without --no-flag) when the default
    is False. We override this to always show the boolean default value.
    """

    def _make_default_extra(self, ctx, extra):
        """
        Build the 'default' entry for the help extra dict.

        Overrides Click's behavior to show boolean defaults for simple flags.
        """
        from click.core import UNSET

        default_value = self.get_default(ctx, call=False)

        show_default = False
        show_default_is_str = False

        if self.show_default is not None:
            if isinstance(self.show_default, str):
                show_default_is_str = show_default = True
            else:
                show_default = self.show_default
        elif ctx.show_default is not None:
            show_default = ctx.show_default

        if show_default_is_str or (show_default and (default_value not in (None, UNSET))):
            if show_default_is_str:
                default_string = f"({self.show_default})"
            elif isinstance(default_value, (list, tuple)):
                default_string = ", ".join(str(d) for d in default_value)
            elif _inspect.isfunction(default_value):
                default_string = "(dynamic)"
            elif self.is_bool_flag and self.secondary_opts:
                # For boolean flags that have distinct True/False opts,
                # use the opt without prefix instead of the value.
                default_string = click.parser._split_opt(
                    (self.opts if default_value else self.secondary_opts)[0]
                )[1]
            # THIS IS THE KEY CHANGE: removed the elif that hides False for simple flags
            elif default_value == "":
                default_string = '""'
            else:
                # Show the actual value (including False for flags)
                default_string = str(default_value)

            if default_string:
                extra["default"] = default_string

    def get_help_record(self, ctx):
        """Override to use our custom default rendering."""
        if self.hidden:
            return None

        any_prefix_is_slash = False

        def _write_opts(opts):
            nonlocal any_prefix_is_slash
            rv, any_slashes = click.formatting.join_options(opts)
            if any_slashes:
                any_prefix_is_slash = True
            if not self.is_flag and not self.count:
                rv += f" {self.make_metavar(ctx)}"
            return rv

        rv = [_write_opts(self.opts)]
        if self.secondary_opts:
            rv.append(_write_opts(self.secondary_opts))

        help_text = self.help or ""
        extra = {}

        # Use our custom default rendering
        self._make_default_extra(ctx, extra)

        if self.required:
            extra["required"] = "required"

        if extra:
            extra_str = ", ".join(
                f"{k}: {v}" if k != "required" else v for k, v in extra.items()
            )
            help_text = f"{help_text}  [{extra_str}]" if help_text else f"[{extra_str}]"

        return ("; " if any_prefix_is_slash else " / ").join(rv), help_text


# Wrapper functions that use our enhanced classes by default


def command(name=None, cls=None, **attrs):
    """
    Create a command using our enhanced Command class by default.

    This is a drop-in replacement for ``click.command()`` that uses
    our Command class (with -h support and argument formatting).
    """
    return click.command(name=name, cls=cls or Command, **attrs)


def group(name=None, cls=None, **attrs):
    """
    Create a group using our enhanced Group class by default.

    This is a drop-in replacement for ``click.group()`` that uses
    our Group class (with -h support and custom Command class).
    """
    return click.group(name=name, cls=cls or Group, **attrs)


def argument(*param_decls, cls=None, **attrs):
    """
    Create an argument using our enhanced Argument class by default.

    This is a drop-in replacement for ``click.argument()`` that uses
    our Argument class (with help text support).

    Example::

        @click.command()
        @click.argument("name", help="The name to greet")
        def hello(name):
            click.echo(f"Hello {name}!")
    """
    return click.argument(*param_decls, cls=cls or Argument, **attrs)


def option(*param_decls, cls=None, **attrs):
    """
    Create an option using our enhanced Option class by default.

    This is a drop-in replacement for ``click.option()`` that uses
    our Option class (with show_default always enabled, including for flags).
    """
    return click.option(*param_decls, cls=cls or Option, **attrs)
