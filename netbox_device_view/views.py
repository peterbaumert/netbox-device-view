from netbox.views import generic
from . import forms, models, tables


class DeviceViewView(generic.ObjectView):
    queryset = models.DeviceView.objects.all()


class DeviceViewListView(generic.ObjectListView):
    queryset = models.DeviceView.objects.all()
    table = tables.DeviceViewTable


class DeviceViewEditView(generic.ObjectEditView):
    queryset = models.DeviceView.objects.all()
    form = forms.DeviceViewForm


class DeviceViewDeleteView(generic.ObjectDeleteView):
    queryset = models.DeviceView.objects.all()
