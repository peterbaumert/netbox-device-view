from netbox.api.viewsets import NetBoxModelViewSet

from .. import filtersets, models
from .serializers import DeviceViewSerializer


class DeviceViewViewSet(NetBoxModelViewSet):
    queryset = models.DeviceView.objects.prefetch_related("tags")
    serializer_class = DeviceViewSerializer
