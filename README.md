# NetBox Device View Plugin

![Version](https://img.shields.io/pypi/v/netbox-device-view)
![Downloads](https://img.shields.io/pypi/dm/netbox-device-view)
![CI](https://github.com/peterbaumert/netbox-device-view/actions/workflows/test.yml/badge.svg)
![License](https://img.shields.io/github/license/peterbaumert/netbox-device-view)

Renders a visual front/rear panel of a device's physical ports and interfaces directly on the NetBox device detail page. Supports both a CSS Grid renderer and a scalable SVG renderer driven by a YAML layout.

![example](https://github.com/peterbaumert/netbox-device-view/blob/main/docs/assets/example_view.png?raw=true)

**Full documentation: [peterbaumert.github.io/netbox-device-view](https://peterbaumert.github.io/netbox-device-view/)**

---

## Requirements

- Python 3.12+
- NetBox 3.5+ (see [COMPATIBILITY.md](COMPATIBILITY.md))

## Installation

Install from PyPI into the NetBox virtual environment:

```bash
source /opt/netbox/venv/bin/activate
pip install netbox-device-view
```

Add to `local_requirements.txt` to survive NetBox upgrades:

```bash
echo netbox-device-view >> /opt/netbox/local_requirements.txt
```

Enable in `configuration.py`:

```python
PLUGINS = ["netbox_device_view"]
```

Run migrations and collect static files:

```bash
cd /opt/netbox/netbox
python manage.py migrate netbox_device_view
python manage.py collectstatic --no-input
```

For configuration options, layout formats, the SVG renderer, and troubleshooting see the **[full documentation](https://peterbaumert.github.io/netbox-device-view/)**.
