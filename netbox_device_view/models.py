from django.db import models
from netbox.models import NetBoxModel


class RenderMode(models.TextChoices):
    CSS = "css", "CSS Grid (default)"
    SVG = "svg", "SVG"


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

    render_mode = models.CharField(
        max_length=10,
        choices=RenderMode.choices,
        default=RenderMode.CSS,
        help_text=(
            "Rendering engine to use for this device view. "
            "'CSS Grid' uses the existing HTML+CSS approach and works with both YAML and legacy CSS layouts. "
            "'SVG' produces a scalable vector graphic and requires a YAML layout."
        ),
    )

    class Meta:
        ordering = ("device_type",)

    def __str__(self):
        return f"{self.device_type.manufacturer} {self.device_type.model}"

    def get_render_mode_color(self):
        return {"css": "secondary", "svg": "info"}.get(self.render_mode, "secondary")

    @property
    def has_yaml_layout(self):
        """Return True if a YAML layout is defined for this device view."""
        return bool(self.yaml_layout and self.yaml_layout.strip())

    @property
    def use_svg(self):
        """Return True when SVG rendering is selected AND a YAML layout is available."""
        return self.render_mode == RenderMode.SVG and self.has_yaml_layout
