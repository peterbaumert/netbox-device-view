from django.db import models

from netbox.models import NetBoxModel


class DeviceView(NetBoxModel):
    device_type = models.OneToOneField(
        to="dcim.DeviceType",
        on_delete=models.PROTECT,
        related_name="+",
    )

    grid_template_area = models.TextField(blank=False)

    class Meta:
        ordering = ("device_type",)

    def __str__(self):
        return self.device_type.model
