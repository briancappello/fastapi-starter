from sqladmin import ModelView


class BaseModelView(ModelView):
    def __init__(self):
        super().__init__()

        self._column_labels = {
            attr: self.column_labels.get(
                attr,
                {"id": "ID"}.get(
                    attr,
                    attr.replace("_", " ").title(),
                ),
            )
            for attr in self._prop_names
        }
        self._column_labels_value_by_key = {v: k for k, v in self._column_labels.items()}
