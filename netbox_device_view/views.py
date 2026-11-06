from dcim.models import Device
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from django.views import View
from netbox.object_actions import (
    AddObject,
    BulkDelete,
    BulkEdit,
    BulkExport,
    BulkImport,
    BulkRename,
    ObjectAction,
)
from netbox.views import generic
from netbox.views.generic import BulkImportView
from utilities.paginator import EnhancedPaginator, get_paginate_count
from utilities.views import (
    ContentTypePermissionRequiredMixin,
    ViewTab,
    register_model_view,
)

from netbox_device_view.forms import DeviceViewImportForm

from . import datasource_import, forms, models, tables
from .utils import (
    device_view_context,
    get_stylenames_for_device_type,
)


class DeviceViewView(generic.ObjectView):
    queryset = models.DeviceView.objects


class DataSourceImport(ObjectAction):
    name = "datasource_import"
    label = _("Import from Data Source")
    template_name = "netbox_device_view/buttons/datasource_import.html"

    @classmethod
    def render(cls, context, obj, **kwargs):
        # permissions_required needs every permission; the import needs add or change.
        if not datasource_import.can_import(context["request"].user):
            return ""
        return super().render(context, obj, **kwargs)


class DeviceViewListView(generic.ObjectListView):
    queryset = models.DeviceView.objects.annotate(
        device_count=Count("device_type__instances")
    )
    table = tables.DeviceViewTable
    actions = (
        AddObject,
        BulkImport,
        DataSourceImport,
        BulkExport,
        BulkEdit,
        BulkRename,
        BulkDelete,
    )


class DeviceViewEditView(generic.ObjectEditView):
    queryset = models.DeviceView.objects
    form = forms.DeviceViewForm
    template_name = "netbox_device_view/deviceview_edit.html"


# The largest PostgreSQL bigint; a larger value makes the id comparison skip the index.
MAX_PK = 2**63 - 1


def _parse_pk(value):
    """Return value as a primary key, or None; isdigit() alone also accepts "²"."""
    if (
        value.isascii()
        and value.isdigit()
        and len(value) <= 19
        and int(value) <= MAX_PK
    ):
        return int(value)
    return None


@method_decorator(login_required, name="dispatch")
class DeviceTypeInterfacesView(View):
    """Return JSON list of component templates with their CSS stylenames for
    the given device type.  Used by the Add/Edit form to preview grid-area
    names before saving."""

    def get(self, request):
        device_type_id = request.GET.get("device_type_id", "").strip()
        device_type_pk = _parse_pk(device_type_id)
        if device_type_pk is None:
            return JsonResponse([], safe=False)
        return JsonResponse(get_stylenames_for_device_type(device_type_pk), safe=False)


class DeviceViewBulkImportView(BulkImportView):
    queryset = models.DeviceView.objects.all()
    model_form = DeviceViewImportForm


def _selected_datafiles(user, source_form, data):
    """Return the selected catalog DataFiles; raise ValidationError for any other selection."""
    values = data.getlist("file")
    if len(values) > datasource_import.MAX_BATCH_ROWS:
        raise ValidationError(datasource_import.TOO_MANY_ROWS)
    file_ids = {pk for pk in map(_parse_pk, values) if pk is not None}
    datafiles = datasource_import.catalog(
        user, source_form.cleaned_data["source"], source_form.cleaned_data["prefix"]
    ).filter(pk__in=file_ids)
    if not values or len(file_ids) != len(values) or len(datafiles) != len(values):
        raise ValidationError(_("Select one or more files from the catalog."))
    return datafiles


class DataSourceImportPermissionMixin(ContentTypePermissionRequiredMixin):
    def has_permission(self):
        return datasource_import.can_import(self.request.user)


def _ticked_file_ids(request, source, prefix) -> set[int]:
    """Return the ids of the ticked files that are still in the catalog."""
    values = request.GET.getlist("file")
    if len(values) > datasource_import.MAX_BATCH_ROWS:
        messages.error(request, datasource_import.TOO_MANY_ROWS)
        return set()
    file_ids = {pk for pk in map(_parse_pk, values) if pk is not None}
    return set(
        datasource_import.catalog(request.user, source, prefix)
        .filter(pk__in=file_ids)
        .values_list("pk", flat=True)
    )


class DeviceViewDataSourceImportView(DataSourceImportPermissionMixin, View):
    """Select a Data Source and directory, then list the importable layout files."""

    def get(self, request):
        form = forms.DataSourceImportSourceForm(
            request.GET if "source" in request.GET else None, user=request.user
        )
        context = {"form": form}
        if form.is_bound and form.is_valid():
            source = form.cleaned_data["source"]
            prefix = form.cleaned_data["prefix"]
            search = form.cleaned_data["q"]
            paginator = EnhancedPaginator(
                datasource_import.catalog(request.user, source, prefix, search),
                get_paginate_count(request),
            )
            page = paginator.get_page(request.GET.get("page"))
            ticked = _ticked_file_ids(request, source, prefix)
            context["paginator"] = paginator
            context["page"] = page
            context["rows"] = datasource_import.catalog_rows(
                request.user, source, prefix, page.object_list
            )
            context["ticked"] = ticked
            context["ticked_elsewhere"] = sorted(
                ticked - {row.datafile.pk for row in context["rows"]}
            )
        return render(request, "netbox_device_view/datasource_import.html", context)

    def post(self, request):
        """Keep the ticked files when the user changes the page or the search."""
        form = forms.DataSourceImportSourceForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(
                request,
                "netbox_device_view/datasource_import.html",
                {"form": form},
                status=400,
            )
        files = request.POST.getlist("file")
        if len(files) > datasource_import.MAX_BATCH_ROWS:
            messages.error(request, datasource_import.TOO_MANY_ROWS)
            files = []
        params = {
            "source": form.cleaned_data["source"].pk,
            "prefix": form.cleaned_data["prefix"],
            "q": form.cleaned_data["q"],
            "file": files,
        }
        for name in ("page", "per_page"):
            if name in request.POST:
                params[name] = request.POST[name]
        catalog = reverse("plugins:netbox_device_view:deviceview_datasource_import")
        return redirect(f"{catalog}?{urlencode(params, doseq=True)}")


class DeviceViewDataSourceImportReviewView(DataSourceImportPermissionMixin, View):
    """Assign device types to the selected files and review the proposed records."""

    def post(self, request):
        source_form = forms.DataSourceImportSourceForm(request.POST, user=request.user)
        if not source_form.is_valid():
            return render(
                request,
                "netbox_device_view/datasource_import.html",
                {"form": source_form},
                status=400,
            )
        source = source_form.cleaned_data["source"]
        prefix = source_form.cleaned_data["prefix"]
        catalog_url = (
            reverse("plugins:netbox_device_view:deviceview_datasource_import")
            + "?"
            + urlencode({"source": source.pk, "prefix": prefix})
        )
        try:
            datafiles = _selected_datafiles(request.user, source_form, request.POST)
        except ValidationError as exc:
            messages.error(request, exc.message)
            return redirect(catalog_url)

        if request.POST.get("step") == "review":
            target_form = forms.DataSourceImportTargetForm(
                request.POST, user=request.user, datafiles=datafiles
            )
        else:
            target_form = forms.DataSourceImportTargetForm(
                user=request.user,
                datafiles=datafiles,
                catalog_rows=datasource_import.catalog_rows(
                    request.user, source, prefix, datafiles
                ),
            )
        review = None
        if target_form.is_bound and target_form.is_valid():
            try:
                review = datasource_import.review(
                    request.user, source, target_form.selections()
                )
            except datasource_import.ImportRejected as exc:
                return render(
                    request,
                    "netbox_device_view/datasource_import_result.html",
                    {
                        "error": str(exc),
                        "source_form": source_form,
                        "target_form": target_form,
                    },
                    status=400,
                )
        return render(
            request,
            "netbox_device_view/datasource_import_review.html",
            {
                "source_form": source_form,
                "target_form": target_form,
                "review": review,
                "catalog_url": catalog_url,
            },
        )


def _review_again_context(request):
    """Rebuild the review forms from a rejected apply POST; apply itself ignores them."""
    source_form = forms.DataSourceImportSourceForm(request.POST, user=request.user)
    if not source_form.is_valid():
        return {}
    try:
        datafiles = _selected_datafiles(request.user, source_form, request.POST)
    except ValidationError:
        return {}
    target_form = forms.DataSourceImportTargetForm(
        request.POST, user=request.user, datafiles=datafiles
    )
    return {"source_form": source_form, "target_form": target_form}


class DeviceViewDataSourceImportApplyView(DataSourceImportPermissionMixin, View):
    """Apply a reviewed import."""

    def post(self, request):
        try:
            result = datasource_import.apply(
                request.user, request.POST.get("token", "")
            )
        except datasource_import.ImportRejected as exc:
            return render(
                request,
                "netbox_device_view/datasource_import_result.html",
                {"error": str(exc), **_review_again_context(request)},
                status=400,
            )
        visible = set(
            models.DeviceView.objects.restrict(request.user, "view")
            .filter(pk__in=[device_view.pk for device_view in result.device_views])
            .values_list("pk", flat=True)
        )
        outcomes = (
            (_("Created"), result.created),
            (_("Updated"), result.updated),
            (_("Unchanged"), result.unchanged),
        )
        return render(
            request,
            "netbox_device_view/datasource_import_result.html",
            {
                "outcomes": [
                    (label, [(dv, dv.pk in visible) for dv in device_views])
                    for label, device_views in outcomes
                ]
            },
        )


class DeviceViewDeleteView(generic.ObjectDeleteView):
    queryset = models.DeviceView.objects


@register_model_view(Device, "deviceview", path="device-view")
class DeviceDeviceView(generic.ObjectView):
    queryset = models.DeviceView.objects

    tab = ViewTab(
        label="Device View",
        badge=lambda obj: models.DeviceView.objects.filter(
            device_type=obj.device_type
        ).count(),
        hide_if_empty=True,
    )

    def get_extra_context(self, request, instance):
        return {
            **(device_view_context(instance) or {}),
            "cable_colors": request.GET.get("cable_colors", "off"),
        }

    def get_object(self, **kwargs):
        return Device.objects.get(pk=kwargs.get("pk"))
