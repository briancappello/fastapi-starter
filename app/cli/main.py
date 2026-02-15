import click
import uvicorn

from tabulate import tabulate

from .groups import main


@main.command()
@click.option("-p", "--port", type=int, default=8000, help="Port to run the server on")
def dev(port: int):
    """Run the development server with hot reload."""
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)


@main.command()
def urls():
    """List all routes registered on the app."""
    from fastapi.routing import APIRoute
    from starlette.routing import BaseRoute, Mount, Route, WebSocketRoute

    from app import app

    def get_endpoint_path(endpoint) -> str:
        module = getattr(endpoint, "__module__", "")
        name = getattr(endpoint, "__qualname__", getattr(endpoint, "__name__", ""))
        return f"{module}.{name}" if module else name

    def collect_routes(routes: list[BaseRoute], prefix: str = "") -> list[list[str]]:
        rows = []
        for route in routes:
            if isinstance(route, APIRoute):
                methods = ", ".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
                rows.append(
                    [methods, prefix + route.path, get_endpoint_path(route.endpoint)]
                )
            elif isinstance(route, WebSocketRoute):
                rows.append(
                    ["WS", prefix + route.path, get_endpoint_path(route.endpoint)]
                )
            elif isinstance(route, Route):
                methods = (
                    ", ".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
                    if route.methods
                    else "ANY"
                )
                rows.append(
                    [methods, prefix + route.path, get_endpoint_path(route.endpoint)]
                )
            elif isinstance(route, Mount):
                if route.routes:
                    rows.extend(collect_routes(route.routes, prefix + route.path))
                elif route.app:
                    app_class = route.app.__class__
                    rows.append(
                        ["MOUNT", prefix + route.path, get_endpoint_path(app_class)]
                    )
        return rows

    rows = collect_routes(app.routes)
    rows.sort(key=lambda r: (r[1], r[0]))
    click.echo(
        tabulate(
            rows,
            headers=["Method", "URL", "Endpoint"],
            tablefmt="pretty",
            stralign="left",
        )
    )
