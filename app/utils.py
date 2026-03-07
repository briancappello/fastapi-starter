"""Utility functions for the application."""

import importlib
import importlib.util
import inspect
import pkgutil

from typing import Literal, TypeVar, overload


T = TypeVar("T")


@overload
def collect_objects(
    klass: type[T],
    module_paths: list[str],
    instances: Literal[False] = False,
) -> list[type[T]]: ...


@overload
def collect_objects(
    klass: type[T],
    module_paths: list[str],
    instances: Literal[True],
) -> list[T]: ...


def collect_objects(
    klass: type[T],
    module_paths: list[str],
    instances: bool = False,
) -> list[type[T]] | list[T]:
    """
    Dynamically import modules and find subclasses or instances of a given class.

    Recursively searches packages using submodule_search_locations to discover
    and import all submodules.

    Args:
        klass: The base class to search for subclasses/instances of.
        module_paths: List of dot-notation module paths to search in.
        instances: If True, find instances of the class. If False, find subclasses.

    Returns:
        A list of subclasses or instances found in the given modules.
        Does not include the passed class itself.
    """
    results: list[type[T]] | list[T] = []
    visited_objects: set[int] = set()
    visited_modules: set[str] = set()

    def _check_object(obj: object) -> None:
        """Check if object matches criteria and add to results."""
        obj_id = id(obj)
        if obj_id in visited_objects:
            return
        visited_objects.add(obj_id)

        if instances:
            if isinstance(obj, klass) and type(obj) is not type:
                results.append(obj)  # type: ignore[arg-type]
        else:
            if isinstance(obj, type) and issubclass(obj, klass) and obj is not klass:
                results.append(obj)  # type: ignore[arg-type]

    def _search_module(module_name: str) -> None:
        """Import a module and search it, recursively handling packages."""
        if module_name in visited_modules:
            return
        visited_modules.add(module_name)

        try:
            module = importlib.import_module(module_name)
        except ImportError:
            return

        # Search all members of this module
        for _, member in inspect.getmembers(module):
            _check_object(member)

        # If this is a package, recursively search submodules
        spec = importlib.util.find_spec(module_name)
        if spec and spec.submodule_search_locations:
            for importer, submodule_name, is_pkg in pkgutil.iter_modules(
                spec.submodule_search_locations, prefix=f"{module_name}."
            ):
                _search_module(submodule_name)

    for module_path in module_paths:
        _search_module(module_path)

    return results
