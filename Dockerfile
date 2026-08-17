FROM ghcr.io/netbox-community/netbox:latest
RUN /opt/netbox/venv/bin/pip install --no-warn-script-location \
    "git+https://github.com/peterbaumert/netbox-device-view.git@main"
