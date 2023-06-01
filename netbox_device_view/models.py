from django.contrib.postgres.fields import ArrayField
from django.db import models

from netbox.models import NetBoxModel


class DeviceView(NetBoxModel):
    device_type = models.ForeignKey(
        to="dcim.DeviceType",
        on_delete=models.PROTECT,
        related_name="+",
        blank=False,
        null=False,
    )

    grid_template_area = models.TextField(blank=False)

    class Meta:
        ordering = ("device_type",)

    def __str__(self):
        return self.device_type.model
