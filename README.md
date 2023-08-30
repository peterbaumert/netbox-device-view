# Netbox Device View Plugin
![Version](https://img.shields.io/pypi/v/netbox-device-view) ![Downloads](https://img.shields.io/pypi/dm/netbox-device-view)

## Install

The plugin is available as a Python package and can be installed with pip.

Run `pip install netbox-device-view` in your virtual env.

To ensure NetBox Device View plugin is automatically re-installed during future upgrades, create a file named `local_requirements.txt` (if not already existing) in the NetBox root directory (alongside `requirements.txt`) and list the `netbox-device-view` package:

```no-highlight
# echo netbox-device-view >> local_requirements.txt
```

Once installed, the plugin needs to be enabled in your `configuration.py` and optionally the `show_on_device_tab` setting enabled.

```python
# In your configuration.py
PLUGINS = ["netbox_device_view"]

PLUGINS_CONFIG = {
    'netbox_device_view': {
        'show_on_device_tab': True,
    },
}
```

First run `source /opt/netbox/venv/bin/activate` to enter the Python virtual environment.


Then run 
```bash
cd /opt/netbox/netbox
pip3 install netbox-device-view
python3 manage.py migrate netbox_device_view
python3 manage.py collectstatic --no-input
```

## How To Use

For each Device Type you need to add a DeviceView.

It is based on a CSS grid view with 32 columns and 2 rows.
You need to specify the grid-template-areas. 
- Interface positions will use the following format: {interfacename}{module}-{port} 
  or fallback to all lower case + [^.a-zA-Z\d] changed to "-"
- leading "empties" can be specified as x
- trailing "empties" can be specified as z
- between "empties" can be named s{0-99}
- numeric only ports have to be prefixed with "p" e.g. "p1"

Example for Cisco C9300-24T with 8x 10G module ( more in [examples](https://github.com/peterbaumert/netbox-device-view/blob/main/examples/) folder )

```
/* C9300-24T */
.deviceview.area {
	grid-template-areas:
	"x x x x x x x x x x x x x x gigabitethernet0-1 gigabitethernet0-3 gigabitethernet0-5 gigabitethernet0-7 gigabitethernet0-9 gigabitethernet0-11 s0 gigabitethernet0-13 gigabitethernet0-15 gigabitethernet0-17 gigabitethernet0-19 gigabitethernet0-21 gigabitethernet0-23 z z z z z"
	"x x x x x x x x x x x x x x gigabitethernet0-2 gigabitethernet0-4 gigabitethernet0-6 gigabitethernet0-8 gigabitethernet0-10 gigabitethernet0-12 s0 gigabitethernet0-14 gigabitethernet0-16 gigabitethernet0-18 gigabitethernet0-20 gigabitethernet0-22 gigabitethernet0-24 z z z z z";
}

/* C9300-24T with C9300-NM-8X */
.deviceview.moduleC9300-NM-8X.area {
	grid-template-areas:
	"x x x x x x x x x x x x x x gigabitethernet0-1 gigabitethernet0-3 gigabitethernet0-5 gigabitethernet0-7 gigabitethernet0-9 gigabitethernet0-11 s0 gigabitethernet0-13 gigabitethernet0-15 gigabitethernet0-17 gigabitethernet0-19 gigabitethernet0-21 gigabitethernet0-23 s1 tengigabitethernet1-1 tengigabitethernet1-3 tengigabitethernet1-5 tengigabitethernet1-7"
	"x x x x x x x x x x x x x x gigabitethernet0-2 gigabitethernet0-4 gigabitethernet0-6 gigabitethernet0-8 gigabitethernet0-10 gigabitethernet0-12 s0 gigabitethernet0-14 gigabitethernet0-16 gigabitethernet0-18 gigabitethernet0-20 gigabitethernet0-22 gigabitethernet0-24 s1 tengigabitethernet1-2 tengigabitethernet1-4 tengigabitethernet1-6 tengigabitethernet1-8";
}
```

It will look like

![example](https://github.com/peterbaumert/netbox-device-view/blob/main/docs/example_view.png?raw=true)