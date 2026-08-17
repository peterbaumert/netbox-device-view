FROM ghcr.io/netbox-community/netbox:latest
RUN uv pip install netbox-device-view

# Bake PLUGINS into the image's own default config -- settings.py reads
# PLUGINS purely from /etc/netbox/config/plugins.py (no env var fallback),
# which is empty at build time (the Helm chart's runtime ConfigMap mount
# doesn't exist here yet). Harmless to leave in the final image: at real
# deploy time the chart's own `plugins` value takes over identically, this
# is just what makes collectstatic (below) actually see the plugin so it
# collects its static files too, not just NetBox core's.
RUN echo 'PLUGINS = ["netbox_device_view"]' > /etc/netbox/config/plugins.py

# NetBox's container filesystem is read-only at runtime (security hardening),
# and static assets aren't on a mounted volume -- collectstatic must run here
# at build time, not on every pod startup. SECRET_KEY is a dummy value used
# only so manage.py can initialize Django settings for this build-time step;
# it has no bearing on the real deployed secret (set separately via the Helm
# chart's `secretKey` value).
RUN SECRET_KEY="dummydummydummydummydummydummydummydummydummydummy" \
    /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
