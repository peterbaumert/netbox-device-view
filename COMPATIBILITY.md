# Compatibility

## Plugin ↔ NetBox version matrix

| Plugin version | NetBox version | Python | Notes |
|----------------|---------------|--------|-------|
| 0.1.15         | 4.5.x         | 3.12+  | Tested |
| 0.1.x          | 3.5+          | 3.10+  | Minimum supported (dcim migration 0172) |

> The plugin depends on NetBox's `dcim` app at migration `0172` and `extras` at `0092`.
> NetBox 3.5 is the earliest release that ships those migrations.

## Upgrade notes

### 0.1.14 → 0.1.15

No breaking changes. Run migrations after upgrading:

```bash
python manage.py migrate netbox_device_view
```

**Migration 0002** — `device_type` field changed from `ForeignKey` to `OneToOneField`.
If you have duplicate DeviceViews for the same DeviceType, the migration will fail.
Deduplicate first:

```python
# Run in the NetBox shell (manage.py nbshell) before migrating
from netbox_device_view.models import DeviceView
from django.db.models import Count

dupes = (
    DeviceView.objects.values("device_type")
    .annotate(count=Count("id"))
    .filter(count__gt=1)
)
for d in dupes:
    views = DeviceView.objects.filter(device_type=d["device_type"]).order_by("id")
    # Keep the first, delete the rest
    views.exclude(pk=views.first().pk).delete()
```

## NetBox Docker image

The devcontainer and CI use `netboxcommunity/netbox:v4.5.4`.
