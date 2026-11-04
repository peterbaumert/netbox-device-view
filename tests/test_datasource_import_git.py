"""End-to-end import from a Git Data Source that NetBox clones and synchronizes itself."""

import re
import shutil
import tempfile
import uuid
from importlib.util import find_spec
from pathlib import Path
from unittest import skipUnless

import django_rq
from core.choices import DataSourceStatusChoices, JobStatusChoices
from core.data_backends import GitBackend
from core.models import DataSource, Job
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rq import SimpleWorker
from users.models import User

from netbox_device_view import datasource_import
from netbox_device_view.models import DeviceView, RenderMode

from .test_datasource_import import (
    APPLY_URL,
    EXAMPLE_PATH,
    EXAMPLE_YAML,
    REVIEW_URL,
    START_URL,
    submitted,
)

OUTSIDE_PATH = "examples-old/yaml/Cisco/C9300-24T.yaml"


@skipUnless(
    find_spec("dulwich"),
    "NetBox's Git data backend needs the optional dulwich package, which is not installed.",
)
class GitDataSourceImportTests(TransactionTestCase):
    def setUp(self):
        # A private RQ queue, so the burst worker runs only the jobs this test enqueues.
        queue = f"netbox_device_view-test-{uuid.uuid4().hex}"
        self.enterContext(
            override_settings(
                RQ_QUEUES={**settings.RQ_QUEUES, queue: settings.RQ_QUEUES["default"]},
                QUEUE_MAPPINGS={"datasource": queue},
            )
        )
        self.queue = django_rq.get_queue(queue)
        self.addCleanup(self.queue.delete, delete_jobs=True)

        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        self.create_repo(
            {
                EXAMPLE_PATH: EXAMPLE_YAML.read_bytes(),
                OUTSIDE_PATH: EXAMPLE_YAML.read_bytes(),
                "README.md": b"Layouts\n",
            }
        )

        manufacturer = Manufacturer.objects.create(name="Cisco", slug="cisco")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="C9300-24T", slug="c9300-24t"
        )
        self.device = Device.objects.create(
            name="sw1",
            device_type=device_type,
            role=DeviceRole.objects.create(name="Switch", slug="switch"),
            site=Site.objects.create(name="Site 1", slug="site-1"),
        )
        user = User.objects.create_superuser(username="admin")
        self.client.force_login(user)

    def create_repo(self, files):
        from dulwich import porcelain

        for path, content in files.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        porcelain.init(str(self.repo))
        porcelain.add(str(self.repo))
        identity = b"Test <test@example.com>"
        porcelain.commit(
            str(self.repo), message=b"Add layouts", author=identity, committer=identity
        )

    def test_git_source_feeds_the_import_end_to_end(self):
        created = self.client.post(
            reverse("core:datasource_add"),
            {
                "name": "Layouts",
                "type": "git",
                "source_url": f"file://{self.repo}",
                "enabled": "on",
            },
        )
        self.assertEqual(created.status_code, 302)
        source = DataSource.objects.get(name="Layouts")
        self.assertIs(source.backend_class, GitBackend)

        detail = self.client.get(source.get_absolute_url())
        sync_url = reverse("core:datasource_sync", kwargs={"pk": source.pk})
        self.assertContains(detail, f'action="{sync_url}"')
        self.assertEqual(self.client.post(sync_url).status_code, 302)
        job = Job.objects.get(object_id=source.pk, name="Synchronization")
        self.assertEqual(job.queue_name, self.queue.name)

        worked = SimpleWorker([self.queue], connection=self.queue.connection).work(
            burst=True
        )
        self.assertTrue(worked)
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatusChoices.STATUS_COMPLETED, job.data)
        source.refresh_from_db()
        self.assertEqual(source.status, DataSourceStatusChoices.COMPLETED)
        self.assertEqual(
            set(source.datafiles.values_list("path", flat=True)),
            {"README.md", EXAMPLE_PATH, OUTSIDE_PATH},
        )

        prefix = self.client.get(reverse(START_URL)).context["form"]["prefix"].value()
        self.assertEqual(prefix, "examples/")
        catalog = self.client.get(
            reverse(START_URL), {"source": source.pk, "prefix": prefix}
        )
        self.assertEqual(
            [datafile.path for datafile in catalog.context["page"]], [EXAMPLE_PATH]
        )
        # "Assign device types" posts the catalog form to the review URL.
        fields = submitted(catalog, START_URL)
        fields["file"] = [str(source.datafiles.get(path=EXAMPLE_PATH).pk)]
        match = self.client.post(reverse(REVIEW_URL), fields)
        review = self.client.post(reverse(REVIEW_URL), submitted(match, REVIEW_URL))
        self.assertEqual(
            [(row.path, row.action) for row in review.context["review"].rows],
            [(EXAMPLE_PATH, datasource_import.Action.CREATE)],
        )
        result = self.client.post(reverse(APPLY_URL), submitted(review, APPLY_URL))
        self.assertContains(result, "Created: 1")

        device_view = DeviceView.objects.get(device_type=self.device.device_type)
        self.assertEqual(device_view.yaml_layout, EXAMPLE_YAML.read_text())
        self.assertEqual(device_view.render_mode, RenderMode.CSS)
        tab = self.client.get(
            reverse("dcim:device_deviceview", kwargs={"pk": self.device.pk})
        )
        first_row = re.search(
            r'grid-template-areas:\s*"([^"]+)"', device_view.grid_template_area
        )
        self.assertIsNotNone(first_row)
        self.assertContains(tab, first_row.group(1))
