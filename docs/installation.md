# Installation

## Requirements

- Python 3.12+
- NetBox 3.5+ (see [COMPATIBILITY.md](https://github.com/peterbaumert/netbox-device-view/blob/main/COMPATIBILITY.md))

## Install the package

Install from PyPI into the NetBox virtual environment:

```bash
source /opt/netbox/venv/bin/activate
pip install netbox-device-view
```

To ensure the plugin is reinstalled on future NetBox upgrades, add it to `local_requirements.txt`:

```bash
echo netbox-device-view >> /opt/netbox/local_requirements.txt
```

## Enable the plugin

Add the plugin to `PLUGINS` in your NetBox `configuration.py`:

```python
PLUGINS = ["netbox_device_view"]
```

## Run migrations and collect static files

```bash
cd /opt/netbox/netbox
python manage.py migrate netbox_device_view
python manage.py collectstatic --no-input
```

## Restart NetBox

```bash
sudo systemctl restart netbox netbox-rq
```
