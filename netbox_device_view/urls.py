from django.urls import path
from netbox.views.generic import ObjectChangeLogView

from . import models, views

urlpatterns = (
    path(
        "device-type-interfaces/",
        views.DeviceTypeInterfacesView.as_view(),
        name="devicetype_interfaces",
    ),
    path("device-view/", views.DeviceViewListView.as_view(), name="deviceview_list"),
    path("device-view/add/", views.DeviceViewEditView.as_view(), name="deviceview_add"),
    path(
        "device-view/import/",
        views.DeviceViewBulkImportView.as_view(),
        name="deviceview_import",
    ),
    path(
        "device-view/import-from-data-source/",
        views.DeviceViewDataSourceImportView.as_view(),
        name="deviceview_datasource_import",
    ),
    path(
        "device-view/import-from-data-source/review/",
        views.DeviceViewDataSourceImportReviewView.as_view(),
        name="deviceview_datasource_import_review",
    ),
    path(
        "device-view/import-from-data-source/apply/",
        views.DeviceViewDataSourceImportApplyView.as_view(),
        name="deviceview_datasource_import_apply",
    ),
    path("device-view/<int:pk>/", views.DeviceViewView.as_view(), name="deviceview"),
    path(
        "device-view/<int:pk>/edit/",
        views.DeviceViewEditView.as_view(),
        name="deviceview_edit",
    ),
    path(
        "device-view/<int:pk>/delete/",
        views.DeviceViewDeleteView.as_view(),
        name="deviceview_delete",
    ),
    path(
        "device-view/<int:pk>/changelog/",
        ObjectChangeLogView.as_view(),
        name="deviceview_changelog",
        kwargs={"model": models.DeviceView},
    ),
)
