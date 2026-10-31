"""Plugin behaviour in an active NetBox Branching branch; skipped when netbox-branching is not enabled."""

import unittest

from core.models import ObjectChange
from dcim.models import DeviceType
from django.conf import settings
from django.urls import reverse

from netbox_device_view.models import DeviceView

from .test_datasource_import import APPLY_URL, EXAMPLE_PATH, EXAMPLE_YAML
from .test_datasource_import_concurrency import ConcurrentApplyTestBase

BRANCHING = "netbox_branching" in settings.PLUGINS


def provision_branch(name):
    from netbox_branching.models import Branch

    branch = Branch(name=name)
    branch.save(provision=False)
    branch.provision(user=None)
    branch.refresh_from_db()
    return branch


@unittest.skipUnless(BRANCHING, "netbox-branching is not enabled")
class DataSourceImportInBranchTests(ConcurrentApplyTestBase):
    def setUp(self):
        super().setUp()
        self.branch = provision_branch("Layouts")
        self.addCleanup(self.branch.deprovision)
        self.clients[0].cookies["active_branch"] = self.branch.schema_id

    def test_import_creates_the_device_view_in_the_branch_only(self):
        from netbox_branching.utilities import activate_branch

        response = self.clients[0].post(
            reverse(APPLY_URL), {"token": self.token(EXAMPLE_PATH)}
        )

        self.assertContains(response, "Created: 1")
        self.assertFalse(DeviceView.objects.exists())
        with activate_branch(self.branch):
            self.assertEqual(
                DeviceView.objects.get().yaml_layout, EXAMPLE_YAML.read_text()
            )

    def test_a_rejected_import_leaves_no_change_records(self):
        from netbox_branching.models import ChangeDiff
        from netbox_branching.utilities import activate_branch

        with activate_branch(self.branch):
            other_type = DeviceType.objects.create(
                manufacturer=self.device_type.manufacturer, model="Other", slug="other"
            )
        token = self.token_for(
            [
                (EXAMPLE_PATH, self.device_type),
                ("examples/yaml/Cisco/Other.yaml", other_type),
            ]
        )
        # The second target gets a device view after the review, so its row is stale.
        with activate_branch(self.branch):
            DeviceView.objects.create(device_type=other_type)

        response = self.clients[0].post(reverse(APPLY_URL), {"token": token})

        self.assertEqual(response.status_code, 400)
        with activate_branch(self.branch):
            self.assertEqual(DeviceView.objects.count(), 1)
        self.assertFalse(ChangeDiff.objects.exists())
        self.assertFalse(ObjectChange.objects.using("default").exists())
