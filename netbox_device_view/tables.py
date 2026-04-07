import django_tables2 as tables
from django.utils.html import mark_safe

from netbox.tables import NetBoxTable, columns

from .models import DeviceView


class LayoutColumn(tables.Column):
    """Show YAML and/or CSS badges depending on which layout data is present."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("orderable", False)
        kwargs.setdefault("verbose_name", "Layout")
        super().__init__(*args, **kwargs)

    def render(self, record):
        badges = []
        if record.has_yaml_layout:
            badges.append('<span class="badge text-bg-success">YAML</span>')
        if record.grid_template_area and record.grid_template_area.strip():
            badges.append('<span class="badge text-bg-secondary">CSS</span>')
        return (
            mark_safe(" ".join(badges))
            if badges
            else mark_safe('<span class="text-muted">—</span>')
        )

    def value(self, record):
        parts = []
        if record.has_yaml_layout:
            parts.append("YAML")
        if record.grid_template_area and record.grid_template_area.strip():
            parts.append("CSS")
        return ", ".join(parts) if parts else "—"


class DeviceViewTable(NetBoxTable):
    device_type = tables.Column(
        verbose_name="Device Type",
        linkify=lambda record: record.device_type.get_absolute_url(),
        accessor="device_type",
        order_by=("device_type__manufacturer__name", "device_type__model"),
    )

    device_count = columns.LinkedCountColumn(
        viewname="dcim:device_list",
        url_params={"device_type_id": "device_type_id"},
        verbose_name="Devices",
    )

    layout = LayoutColumn(accessor="pk")

    render_mode = columns.ChoiceFieldColumn(verbose_name="Render Mode")

    class Meta(NetBoxTable.Meta):
        model = DeviceView
        fields = ("pk", "id", "device_type", "device_count", "layout", "render_mode")
        default_columns = ("device_type", "device_count", "layout", "render_mode")
