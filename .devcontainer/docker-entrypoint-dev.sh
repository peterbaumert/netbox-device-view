#!/bin/bash
set -e

echo "Installing netbox-device-view in editable mode..."
uv pip install --python /opt/netbox/venv/bin/python3 --no-cache -e /opt/code/netbox-device-view
echo "Plugin installed."

# Enable hot-reload in Granian (used by launch-netbox.sh via ${GRANIAN_EXTRA_ARGS[@]})
export GRANIAN_EXTRA_ARGS="--reload"

exec "$@"
