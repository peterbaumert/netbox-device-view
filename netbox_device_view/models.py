from django.db import models

from netbox.models import NetBoxModel


class DeviceView(NetBoxModel):
    device_type = models.OneToOneField(
        to="dcim.DeviceType",
        on_delete=models.PROTECT,
        related_name="+",
    )

    grid_template_area = models.TextField(blank=False)

    yaml_layout = models.TextField(
        blank=True,
        default="",
        help_text=(
            "YAML-based layout definition. When provided, this takes precedence over "
            "the legacy CSS grid_template_area field for rendering. "
            "See the documentation for the schema reference."
        ),
    )

    class Meta:
        ordering = ("device_type",)

    def __str__(self):
        return self.device_type.model

    @property
    def has_yaml_layout(self):
        """Return True if a YAML layout is defined for this device view."""
        return bool(self.yaml_layout and self.yaml_layout.strip())
