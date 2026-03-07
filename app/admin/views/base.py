from typing import Any

from sqladmin import ModelView
from sqlalchemy.ext.associationproxy import (
    AssociationProxyInstance,
    _AssociationCollection,
)

from app.admin.formatters import format_association_proxy_as_links


CATEGORY_SECURITY = "Security"
CATEGORY_SECURITY_ICON = "fa-solid fa-lock"


class BaseModelView(ModelView):
    page_size = 100
    show_compact_lists = False

    def __init__(self):
        # Build mapping of association proxy id -> attribute name BEFORE super().__init__()
        # because _get_prop_name is called during parent initialization
        self._association_proxy_names: dict[int, str] = {}
        for name in dir(self.model):
            if not name.startswith("_"):
                attr = getattr(self.model, name, None)
                if isinstance(attr, AssociationProxyInstance):
                    self._association_proxy_names[id(attr)] = name

        super().__init__()

        # Fill in default title-case labels for any prop not already labelled
        all_props = (
            set(self._prop_names)
            | set(self._details_prop_names)
            | set(self._list_prop_names)
        )
        for attr in all_props:
            if attr not in self._column_labels:
                self._column_labels[attr] = (
                    "ID" if attr == "id" else attr.replace("_", " ").title()
                )
        self._column_labels_value_by_key = {v: k for k, v in self._column_labels.items()}

    def _get_prop_name(self, prop: Any) -> str:
        """
        Override to handle association proxy instances.
        """
        if isinstance(prop, str):
            return prop

        # For association proxies, look up the actual attribute name
        if isinstance(prop, AssociationProxyInstance):
            return self._association_proxy_names.get(id(prop), prop.target_collection)
        return prop.key

    def _default_formatter(self, value: Any) -> Any:
        """
        Override to handle association proxy instances.
        """
        # Check if this is an unresolved association proxy (accessed on class, not instance)
        if isinstance(value, AssociationProxyInstance):
            return ""

        # Check if it's a resolved association proxy collection
        if isinstance(value, _AssociationCollection):
            return format_association_proxy_as_links(value)

        return super()._default_formatter(value)
