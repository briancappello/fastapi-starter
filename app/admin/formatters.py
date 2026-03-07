from typing import Any

from markupsafe import Markup
from sqladmin.helpers import get_object_identifier, slugify_class_name


def format_association_proxy_as_links(value: Any) -> Markup:
    """Format association proxy collections as HTML links to detail pages."""
    if value is None:
        return Markup("")

    def make_link(item):
        identity = slugify_class_name(item.__class__.__name__)
        pk = get_object_identifier(item)
        url = f"/admin/{identity}/details/{pk}"
        return f'<a href="{url}">{Markup.escape(str(item))}</a>'

    if not hasattr(value, "__iter__"):
        return Markup(make_link(value))

    items = list(value)
    if not items:
        return Markup("")

    return Markup("<br/>".join([make_link(item) for item in items]))
