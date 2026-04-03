FROM netboxcommunity/netbox:latest

RUN uv pip install --python /opt/netbox/venv/bin/python3 --no-cache black "granian[reload]"

COPY .devcontainer/docker-entrypoint-dev.sh /docker-entrypoint-dev.sh
RUN chmod +x /docker-entrypoint-dev.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/docker-entrypoint-dev.sh"]
CMD ["/opt/netbox/docker-entrypoint.sh", "/opt/netbox/launch-netbox.sh"]
