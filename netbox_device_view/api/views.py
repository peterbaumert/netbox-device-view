from dcim.models import Device
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework.generics import RetrieveAPIView
from rest_framework.response import Response

from .. import models
from .rendered_layout import compose_rendered_layout
from .serializers import DeviceViewSerializer


class DeviceViewViewSet(NetBoxModelViewSet):
    queryset = models.DeviceView.objects.prefetch_related("tags")
    serializer_class = DeviceViewSerializer


class DeviceRenderedLayoutView(RetrieveAPIView):
    """
    GET /api/plugins/device_view/devices/<pk>/rendered-layout/

    Read-only, versioned JSON composition of a device's rendered layout —
    SVG structure plus hotspot geometry and connection state, keyed by
    NetBox object — for API clients that can't (or shouldn't) reimplement
    this plugin's YAML/SVG rendering pipeline. See
    netbox_device_view.api.rendered_layout for the composition logic and
    docs/rendered-layout-api.md for the response schema.

    Uses the same permission/authentication stack as the rest of the
    plugin's API (DRF's default TokenPermissions, configured globally by
    NetBox): a 403 for users without ``dcim.view_device`` at all, a normal
    404 for a device that doesn't exist *or* is outside the requesting
    user's object-level permission scope (``get_queryset`` applies
    ``.restrict()`` — the same object-level filtering NetBoxModelViewSet
    uses — so an out-of-scope device is indistinguishable from a missing
    one, matching NetBox's own API behaviour elsewhere).

    No serializer_class: the response is a composed dict, not a model
    representation, so it's returned directly rather than forced through
    ModelSerializer machinery that doesn't fit its shape.
    """

    queryset = Device.objects.all()

    def get_queryset(self):
        return super().get_queryset().restrict(self.request.user, "view")

    def get(self, request, *args, **kwargs):
        device = self.get_object()
        return Response(compose_rendered_layout(device))
