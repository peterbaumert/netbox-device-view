# Contributing

Thanks for your interest in contributing to netbox-device-view!

## Development setup

The recommended way to develop is via the devcontainer, which provides a full NetBox environment with the plugin installed in editable mode.

**Requirements:** Docker and the VS Code [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension.

```bash
cp .devcontainer/.env.example .devcontainer/.env
# Open in VS Code → "Reopen in Container"
```

On first start the container builds the image, starts Postgres/Redis/NetBox, installs the plugin, runs migrations, and creates an `admin` / `admin` superuser. NetBox UI is at **http://localhost:8000**.

Without VS Code:

```bash
cp .devcontainer/.env.example .devcontainer/.env
docker compose -f .devcontainer/docker-compose.yml up -d
```

## Running tests

Tests must run inside the devcontainer (the NetBox venv and Django settings are only available there):

```bash
docker compose -f .devcontainer/docker-compose.yml exec -w /opt/code/netbox-device-view netbox \
  /opt/netbox/venv/bin/pytest tests/ -v
```

## Linting

```bash
# Inside the container terminal:
black netbox_device_view/ tests/
ruff check netbox_device_view/ tests/
```

Both are enforced by CI and pre-commit hooks. Run them before pushing.

## Migrations

```bash
docker compose -f .devcontainer/docker-compose.yml exec netbox \
  /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py makemigrations netbox_device_view

docker compose -f .devcontainer/docker-compose.yml exec netbox \
  /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py migrate netbox_device_view
```

## Branch and PR conventions

- Base PRs against `main`.
- Keep PRs focused — one logical change per PR.
- Fill in the PR template checklist before requesting review.

## Release process

Versioning and changelog are managed by [release-please](https://github.com/googleapis/release-please). To trigger a release, merge a release-please PR created automatically from conventional commits on `main`. The release workflow then builds and publishes to PyPI.
