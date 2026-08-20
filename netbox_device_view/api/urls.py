from django.urls import path
from netbox.api.routers import NetBoxRouter

from . import views

app_name = "netbox_device_view"

router = NetBoxRouter()
router.register("device-view", views.DeviceViewViewSet)

urlpatterns = router.urls + [
    path(
        "devices/<int:pk>/rendered-layout/",
        views.DeviceRenderedLayoutView.as_view(),
        name="device_rendered_layout",
    ),
]
