PLUGINS = ["netbox_device_view"]

PLUGINS_CONFIG = {
    "netbox_device_view": {
        "show_on_device_tab": False,
    }
}

DEVELOPER = True  # allows makemigrations; safe in devcontainer only

# Required since NetBox 4.6 for v2 API tokens; dev-only placeholder value.
API_TOKEN_PEPPERS = {
    1: "devcontainer-only-not-a-secret-0123456789abcdef0123456789abcdef"
}
