"""
End-to-end tests for importing layouts from a NetBox Data Source.

They use a real local DataSource synchronized by NetBox itself, real
object permissions, and the plugin's UI request paths.
"""

import hashlib
import re
import shutil
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from core.choices import DataSourceStatusChoices
from core.models import DataFile, DataSource, ObjectChange, ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.conf import settings
from django.core import signing
from django.core.management import call_command
from django.db import connection, transaction
from django.db.models.signals import post_save, pre_save
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.text import slugify
from extras.models import CustomField, Tag
from users.models import ObjectPermission, User

from netbox_device_view import datasource_import
from netbox_device_view.layout import parse_yaml, render_css
from netbox_device_view.models import DeviceView, RenderMode
from netbox_device_view.utils import normalize_grid_template_area
from netbox_device_view.views import DeviceViewDataSourceImportView

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_YAML = EXAMPLES / "yaml" / "Cisco" / "C9300-24T.yaml"
EXAMPLE_PATH = "examples/yaml/Cisco/C9300-24T.yaml"
EXAMPLE_CSS = EXAMPLES / "Cisco" / "C9300-24T.css"
EXAMPLE_CSS_PATH = "examples/Cisco/C9300-24T.css"

START_URL = "plugins:netbox_device_view:deviceview_datasource_import"
REVIEW_URL = "plugins:netbox_device_view:deviceview_datasource_import_review"
APPLY_URL = "plugins:netbox_device_view:deviceview_datasource_import_apply"

RECORDED_EVENTS = []
INJECTION = 'x" onmouseover="alert(1)'


def injected_layouts():
    """Return the example YAML with a quoted attribute injection in a base and a variant element."""
    element = (
        f"      - elements:\n          - {{key: mgmt0, css_classes: ['{INJECTION}']}}\n"
    )
    text = EXAMPLE_YAML.read_text()
    layouts = {
        "base": text.replace(
            "views:\n  front:\n    rows:\n", "views:\n  front:\n    rows:\n" + element
        ),
        "variant": text.replace(
            "    match: module\n    rows:\n", "    match: module\n    rows:\n" + element
        ),
    }
    assert text not in layouts.values()
    return layouts


def record_events(events):
    RECORDED_EVENTS.extend(events)


class _FormFields(HTMLParser):
    """Collect the values a browser submits for the first form posting to one action."""

    def __init__(self, action):
        super().__init__()
        self.action = action
        self.state = "before"
        self.select = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and self.state == "before":
            self.state = "inside" if attrs.get("action") == self.action else "before"
        if self.state != "inside":
            return
        if tag == "input" and attrs.get("name"):
            if attrs.get("type") == "checkbox" and "checked" not in attrs:
                return
            self.fields.setdefault(attrs["name"], []).append(attrs.get("value", ""))
        elif tag == "select":
            self.select = attrs["name"]
        elif tag == "option" and self.select and "selected" in attrs:
            self.fields.setdefault(self.select, []).append(attrs["value"])

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None
        elif tag == "form" and self.state == "inside":
            self.state = "after"


def submitted(response, url_name):
    """Return what the page's form for url_name would submit, without unchecked boxes."""
    parser = _FormFields(reverse(url_name))
    parser.feed(response.content.decode())
    assert parser.state == "after", f"no form posts to {url_name}"
    return parser.fields


class DataSourceImportTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manufacturer = Manufacturer.objects.create(name="Cisco", slug="cisco")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="C9300-24T", slug="c9300-24t"
        )
        site = Site.objects.create(name="Site 1", slug="site-1")
        role = DeviceRole.objects.create(name="Switch", slug="switch")
        cls.device = Device.objects.create(
            name="sw1", device_type=cls.device_type, role=role, site=site
        )

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        self.write_file(EXAMPLE_PATH, EXAMPLE_YAML.read_bytes())
        self.source = DataSource.objects.create(
            name="Layouts", type="local", source_url=f"file://{self.repo}"
        )
        self.source.sync()

        self.user = User.objects.create_user(username="importer")
        self.client.force_login(self.user)
        self.grant(DataSource, "view")
        self.grant(DataFile, "view")
        self.grant(Manufacturer, "view")
        self.grant(DeviceType, "view")
        self.grant(Device, "view")
        self.grant(DeviceView, "view")
        self.grant(DeviceView, "add")

    def write_file(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def grant(self, model, action, constraints=None):
        perm = ObjectPermission.objects.create(
            name=f"{model._meta.model_name}-{action}-{ObjectPermission.objects.count()}",
            actions=[action],
            constraints=constraints,
        )
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))

    def compete_before_save(self, device_type):
        """Create a device view for the type just before the import saves its own."""

        def create_competing_view(sender, instance, **kwargs):
            # bulk_create sends no signals, so this receiver runs once.
            if instance.device_type_id == device_type.pk:
                DeviceView.objects.bulk_create(
                    [DeviceView(device_type=device_type, grid_template_area="x")]
                )

        pre_save.connect(create_competing_view, sender=DeviceView, weak=False)
        self.addCleanup(pre_save.disconnect, create_competing_view, sender=DeviceView)

    def datafile(self, path=EXAMPLE_PATH):
        return self.source.datafiles.get(path=path)

    def review(self, selections, source=None, replace=()):
        data = {
            "source": (source or self.source).pk,
            "prefix": "examples/",
            "step": "review",
        }
        data["file"] = [datafile.pk for datafile, _ in selections]
        for datafile, device_type in selections:
            data[f"device_type_{datafile.pk}"] = device_type.pk
        for datafile in replace:
            data[f"replace_{datafile.pk}"] = "on"
        return self.client.post(reverse(REVIEW_URL), data)

    def match(self, datafiles):
        return self.client.post(
            reverse(REVIEW_URL),
            {
                "source": self.source.pk,
                "prefix": "examples/",
                "step": "match",
                "file": [datafile.pk for datafile in datafiles],
            },
        )

    def review_file(self, path, content):
        self.write_file(path, content.encode())
        self.source.sync()
        return self.review([(self.datafile(path), self.device_type)])

    def review_token(self, selections):
        response = self.review(selections)
        self.assertEqual(response.status_code, 200)
        token = response.context["review"].token
        self.assertTrue(token, response.context["review"].rows)
        return token

    def apply(self, token):
        return self.client.post(reverse(APPLY_URL), {"token": token})

    def delete_when_read(self, datafile):
        """Delete the DataFile just before the import loads its deferred content."""
        deleted = []

        def delete_first(execute, sql, params, many, context):
            # Only the deferred load selects the data column without LENGTH().
            if not deleted and '"core_datafile"."data"' in sql and "LENGTH" not in sql:
                deleted.append(datafile.pk)
                DataFile.objects.filter(pk=datafile.pk).delete()
            return execute(sql, params, many, context)

        self.enterContext(connection.execute_wrapper(delete_first))
        self.addCleanup(self.assertEqual, deleted, [datafile.pk])

    def assert_rejected(self, response):
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "No records were changed", status_code=400)
        self.assertFalse(DeviceView.objects.exists())
        self.assertFalse(
            ObjectChange.objects.filter(
                changed_object_type=ObjectType.objects.get_for_model(DeviceView)
            ).exists()
        )


class ImportWorkflowTests(DataSourceImportTestBase):
    def test_import_one_yaml_layout_end_to_end(self):
        list_page = self.client.get(
            reverse("plugins:netbox_device_view:deviceview_list")
        )
        self.assertContains(list_page, reverse(START_URL))

        start = self.client.get(reverse(START_URL))
        self.assertContains(start, "Layouts")
        self.assertContains(start, 'value="examples/"')

        catalog = self.client.get(
            reverse(START_URL), {"source": self.source.pk, "prefix": "examples/"}
        )
        self.assertContains(catalog, EXAMPLE_PATH)
        datafile = self.datafile()
        self.assertContains(catalog, f'value="{datafile.pk}"')

        assign = self.client.post(
            reverse(REVIEW_URL),
            {
                "source": self.source.pk,
                "prefix": "examples/",
                "step": "match",
                "file": [datafile.pk],
            },
        )
        self.assertContains(assign, f'name="device_type_{datafile.pk}"')
        self.assertIsNone(assign.context["review"])

        review = self.review([(datafile, self.device_type)])
        self.assertContains(review, "C9300-24T")
        self.assertContains(review, "Create")
        token = review.context["review"].token
        self.assertTrue(token)
        self.assertFalse(DeviceView.objects.exists())

        result = self.apply(token)
        self.assertEqual(result.status_code, 200)
        device_view = DeviceView.objects.get(device_type=self.device_type)
        self.assertContains(result, device_view.get_absolute_url())
        self.assertEqual(device_view.yaml_layout, EXAMPLE_YAML.read_text())
        self.assertEqual(device_view.render_mode, RenderMode.CSS)
        self.assertIn("grid-template-areas", device_view.grid_template_area)
        self.assertTrue(
            ObjectChange.objects.filter(
                changed_object_id=device_view.pk, action="create"
            ).exists()
        )

        tab = self.client.get(
            reverse("dcim:device_deviceview", kwargs={"pk": self.device.pk})
        )
        self.assertEqual(tab.status_code, 200)
        first_row = re.search(
            r'grid-template-areas:\s*"([^"]+)"', device_view.grid_template_area
        )
        self.assertContains(tab, first_row.group(1))


class SourceSelectionTests(DataSourceImportTestBase):
    def source_choices(self):
        form = self.client.get(reverse(START_URL)).context["form"]
        return set(form.fields["source"].queryset)

    def test_only_visible_synchronized_enabled_sources_are_offered(self):
        never_synced = DataSource.objects.create(
            name="New", type="local", source_url=f"file://{self.repo}"
        )
        disabled = DataSource.objects.create(
            name="Disabled", type="local", source_url=f"file://{self.repo}"
        )
        disabled.sync()
        DataSource.objects.filter(pk=disabled.pk).update(enabled=False)
        self.assertEqual(self.source_choices(), {self.source})

        catalog = self.client.get(
            reverse(START_URL), {"source": never_synced.pk, "prefix": ""}
        )
        self.assertIsNone(catalog.context.get("page"))

    def test_source_hidden_by_permission_constraint_is_not_offered(self):
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DataSource)
        ).update(constraints={"name": "Other"})
        self.assertEqual(self.source_choices(), set())

    def test_source_parameters_are_not_exposed(self):
        DataSource.objects.filter(pk=self.source.pk).update(
            parameters={"password": "s3cr3t-value"}
        )
        catalog = self.client.get(
            reverse(START_URL), {"source": self.source.pk, "prefix": "examples/"}
        )
        self.assertNotContains(catalog, "s3cr3t-value")
        self.assertNotContains(catalog, str(self.repo))


class CatalogTests(DataSourceImportTestBase):
    def catalog_paths(self, prefix):
        response = self.client.get(
            reverse(START_URL), {"source": self.source.pk, "prefix": prefix}
        )
        self.assertEqual(response.status_code, 200)
        page = response.context.get("page")
        return None if page is None else [datafile.path for datafile in page]

    def test_prefix_matches_path_components(self):
        self.write_file("examples-old/Cisco/C9300-24T.yaml", EXAMPLE_YAML.read_bytes())
        self.write_file("examples/notes.txt", b"not a layout")
        self.source.sync()
        self.assertEqual(self.catalog_paths("examples/"), [EXAMPLE_PATH])
        self.assertEqual(self.catalog_paths("examples"), [EXAMPLE_PATH])
        self.assertEqual(
            self.catalog_paths(""),
            ["examples-old/Cisco/C9300-24T.yaml", EXAMPLE_PATH],
        )

    def test_unsafe_prefixes_are_rejected(self):
        for prefix in ("/etc", "../examples", "examples/../..", "exam\x00ples"):
            with self.subTest(prefix=prefix):
                self.assertIsNone(self.catalog_paths(prefix))

    def test_empty_catalog_says_so(self):
        response = self.client.get(
            reverse(START_URL), {"source": self.source.pk, "prefix": "nothing/"}
        )
        self.assertContains(response, "No layout files below this directory.")
        self.assertNotContains(response, "Assign device types")

    def test_datafile_hidden_by_permission_constraint_is_not_readable(self):
        other_path = "examples/yaml/Cisco/Other.yaml"
        self.write_file(other_path, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        hidden = self.datafile()
        token = self.review_token([(hidden, self.device_type)])
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DataFile)
        ).update(constraints={"path": other_path})

        self.assertEqual(self.catalog_paths("examples/"), [other_path])
        match = self.client.post(
            reverse(REVIEW_URL),
            {
                "source": self.source.pk,
                "prefix": "examples/",
                "step": "match",
                "file": [hidden.pk],
            },
        )
        self.assertEqual(match.status_code, 302)
        selection = datasource_import.Selection(
            datafile_id=hidden.pk, device_type_id=self.device_type.pk
        )
        review = datasource_import.review(self.user, self.source, [selection])
        self.assertEqual(review.rows[0].error, datasource_import.FILE_UNAVAILABLE)
        self.assertEqual(review.rows[0].proposed_fields, {})
        self.assertEqual(self.apply(token).status_code, 400)
        self.assertFalse(DeviceView.objects.exists())

    def test_catalog_does_not_load_file_content(self):
        with CaptureQueriesContext(connection) as queries:
            self.catalog_paths("examples/")
        datafile_queries = [q["sql"] for q in queries if '"core_datafile"' in q["sql"]]
        self.assertTrue(datafile_queries)
        for sql in datafile_queries:
            self.assertNotIn('"core_datafile"."data"', sql)


class SuggestionTests(DataSourceImportTestBase):
    def catalog(self, **params):
        response = self.client.get(
            reverse(START_URL),
            {"source": self.source.pk, "prefix": "examples/", **params},
        )
        self.assertEqual(response.status_code, 200)
        return response

    def rows(self, **params):
        return {
            row.datafile.path: row for row in self.catalog(**params).context["rows"]
        }

    def assert_preselected(self, response, datafile, device_type):
        field = response.context["target_form"][f"device_type_{datafile.pk}"]
        self.assertEqual(field.value(), device_type.pk if device_type else None)

    def test_bundled_examples_are_suggested_by_exact_path_match(self):
        expected = {}
        for example in sorted(EXAMPLES.glob("yaml/*/*.yaml")):
            path = f"examples/{example.relative_to(EXAMPLES)}"
            self.write_file(path, example.read_bytes())
            manufacturer, _ = Manufacturer.objects.get_or_create(
                name=example.parent.name,
                defaults={"slug": slugify(example.parent.name)},
            )
            expected[path], _ = DeviceType.objects.get_or_create(
                manufacturer=manufacturer,
                model=example.stem,
                defaults={"slug": slugify(example.stem)},
            )
        self.source.sync()
        self.assertGreater(len(expected), 10)

        response = self.catalog(per_page=100)
        rows = {row.datafile.path: row for row in response.context["rows"]}
        self.assertEqual(set(rows), set(expected))
        for path, device_type in expected.items():
            with self.subTest(path=path):
                row = rows[path]
                self.assertEqual(row.suggestion, device_type)
                self.assertEqual(row.format, datasource_import.Format.YAML)
                self.assertEqual(
                    row.state, datasource_import.TargetState.NO_DEVICE_VIEW
                )
                self.assertEqual(row.problems, [])
                self.assertTrue(row.is_selectable)
        self.assertContains(response, "No device view")
        self.assertContains(
            response, reverse("dcim:devicetype", kwargs={"pk": self.device_type.pk})
        )

        datafiles = [self.datafile(path) for path in expected]
        match = self.match(datafiles)
        for datafile in datafiles:
            self.assert_preselected(match, datafile, expected[datafile.path])

    def test_only_exact_matches_are_suggested(self):
        Manufacturer.objects.create(name="Generic", slug="generic")
        paths = [
            "examples/cisco/C9300-24T.yaml",
            "examples/Cisco/c9300-24t.yaml",
            "examples/Cisco/C9300_24T.yaml",
            "examples/Cisco /C9300-24T.yaml",
            "examples/Generic/C9300-24T.yaml",
            "examples/C9300-24T.yaml",
            "examples/Cisco/C9300-24T/layout.yaml",
        ]
        for path in paths:
            self.write_file(path, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        rows = self.rows()
        for path in paths:
            with self.subTest(path=path):
                self.assertIsNone(rows[path].suggestion)
                self.assertEqual(rows[path].problems, [datasource_import.NO_MATCH])
                self.assertFalse(rows[path].is_selectable)
        self.assertEqual(rows[EXAMPLE_PATH].suggestion, self.device_type)
        self.assertFalse(Manufacturer.objects.filter(name="cisco").exists())

    def test_unmatched_file_is_manually_assignable(self):
        path = "examples/custom/my-layout.yaml"
        self.write_file(path, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        datafile = self.datafile(path)
        self.assert_preselected(self.match([datafile]), datafile, None)
        self.review_token([(datafile, self.device_type)])

    def test_suggestion_can_be_corrected_manually(self):
        other = DeviceType.objects.create(
            manufacturer=self.manufacturer, model="Other", slug="other"
        )
        datafile = self.datafile()
        self.assert_preselected(self.match([datafile]), datafile, self.device_type)
        response = self.review([(datafile, other)])
        review = response.context["review"]
        self.assertEqual(review.rows[0].device_type, other)
        self.assertTrue(review.token)
        self.apply(review.token)
        self.assertTrue(DeviceView.objects.filter(device_type=other).exists())
        self.assertFalse(
            DeviceView.objects.filter(device_type=self.device_type).exists()
        )

    def test_ambiguous_matches_are_flagged_across_the_prefix(self):
        other_path = "examples/Cisco/C9300-24T.yml"
        self.write_file(other_path, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        for params in ({}, {"q": ".yml"}):
            with self.subTest(params=params):
                for row in self.rows(**params).values():
                    self.assertEqual(row.suggestion, self.device_type)
                    self.assertEqual(row.problems, [datasource_import.AMBIGUOUS_MATCH])
                    self.assertFalse(row.is_selectable)
        datafiles = [self.datafile(), self.datafile(other_path)]
        match = self.match(datafiles)
        for datafile in datafiles:
            self.assert_preselected(match, datafile, None)

    def test_hidden_manufacturer_or_device_type_is_not_suggested(self):
        for model, constraints in (
            (Manufacturer, {"name": "Other"}),
            (DeviceType, {"model": "Other"}),
        ):
            with self.subTest(model=model.__name__):
                permissions = ObjectPermission.objects.filter(
                    object_types=ObjectType.objects.get_for_model(model)
                )
                permissions.update(constraints=constraints)
                try:
                    row = self.rows()[EXAMPLE_PATH]
                    self.assertIsNone(row.suggestion)
                    self.assertFalse(row.is_selectable)
                finally:
                    permissions.update(constraints=None)

    def create_device_type(self, model, part_number):
        return DeviceType.objects.create(
            manufacturer=self.manufacturer,
            model=model,
            slug=slugify(model),
            part_number=part_number,
        )

    def write_layouts(self, *paths):
        for path in paths:
            self.write_file(path, EXAMPLE_YAML.read_bytes())
        self.source.sync()

    def test_part_number_match_is_suggested(self):
        device_type = self.create_device_type("Catalyst 8300-1N1S-6T", "C8300-1N1S-6T")
        Manufacturer.objects.create(name="Generic", slug="generic")
        path = "examples/Cisco/C8300-1N1S-6T.yaml"
        unmatched = [
            "examples/Cisco/c8300-1n1s-6t.yaml",
            "examples/Cisco/C8300_1N1S_6T.yaml",
            "examples/Generic/C8300-1N1S-6T.yaml",
        ]
        self.write_layouts(path, *unmatched)
        rows = self.rows()
        self.assertEqual(rows[path].suggestion, device_type)
        self.assertEqual(rows[path].state, datasource_import.TargetState.NO_DEVICE_VIEW)
        self.assertEqual(rows[path].problems, [])
        self.assertTrue(rows[path].is_selectable)
        for other_path in unmatched:
            with self.subTest(path=other_path):
                self.assertIsNone(rows[other_path].suggestion)
                self.assertEqual(
                    rows[other_path].problems, [datasource_import.NO_MATCH]
                )
        datafile = self.datafile(path)
        self.assert_preselected(self.match([datafile]), datafile, device_type)

    def test_model_match_wins_over_part_number_match(self):
        datafile = self.datafile()
        # First one device type has the part number, then two have it.
        for model in ("Catalyst 9300-24T", "Catalyst 9300-24T-E"):
            self.create_device_type(model, "C9300-24T")
            with self.subTest(model=model):
                row = self.rows()[EXAMPLE_PATH]
                self.assertEqual(row.suggestion, self.device_type)
                self.assertEqual(row.problems, [])
                self.assert_preselected(
                    self.match([datafile]), datafile, self.device_type
                )

    def test_part_number_of_several_device_types_is_not_suggested(self):
        device_type = self.create_device_type("Catalyst 8300-1N1S-6T", "C8300-1N1S-6T")
        self.create_device_type("Catalyst 8300-1N1S-6T-E", "C8300-1N1S-6T")
        path = "examples/Cisco/C8300-1N1S-6T.yaml"
        self.write_layouts(path)
        row = self.rows()[path]
        self.assertIsNone(row.suggestion)
        self.assertEqual(row.problems, [datasource_import.NO_MATCH])
        self.assertFalse(row.is_selectable)
        datafile = self.datafile(path)
        self.assert_preselected(self.match([datafile]), datafile, None)
        self.review_token([(datafile, device_type)])

    def test_part_number_and_model_files_of_one_device_type_are_ambiguous(self):
        device_type = self.create_device_type("Catalyst 8300-1N1S-6T", "C8300-1N1S-6T")
        paths = [
            "examples/Cisco/C8300-1N1S-6T.yaml",
            "examples/Cisco/Catalyst 8300-1N1S-6T.yaml",
        ]
        self.write_layouts(*paths)
        rows = self.rows()
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(rows[path].suggestion, device_type)
                self.assertEqual(
                    rows[path].problems, [datasource_import.AMBIGUOUS_MATCH]
                )
                self.assertFalse(rows[path].is_selectable)

    def test_hidden_device_type_with_the_part_number_is_not_suggested(self):
        self.create_device_type("Hidden 8300", "C8300-1N1S-6T")
        path = "examples/Cisco/C8300-1N1S-6T.yaml"
        self.write_layouts(path)
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DeviceType)
        ).update(constraints={"model__in": ["C9300-24T", "Catalyst 8300-1N1S-6T"]})
        response = self.catalog()
        row = {row.datafile.path: row for row in response.context["rows"]}[path]
        self.assertIsNone(row.suggestion)
        self.assertEqual(row.problems, [datasource_import.NO_MATCH])
        self.assertNotContains(response, "Hidden 8300")

        # The hidden device type does not make the visible one ambiguous.
        visible = self.create_device_type("Catalyst 8300-1N1S-6T", "C8300-1N1S-6T")
        row = self.rows()[path]
        self.assertEqual(row.suggestion, visible)
        self.assertEqual(row.problems, [])

    def test_visible_existing_device_view_is_shown_and_preselected(self):
        device_view = DeviceView.objects.create(
            device_type=self.device_type, grid_template_area="x"
        )
        response = self.catalog()
        row = response.context["rows"][0]
        self.assertEqual(row.state, datasource_import.TargetState.EXISTING)
        self.assertEqual(row.device_view, device_view)
        self.assertTrue(row.is_selectable)
        self.assertContains(response, device_view.get_absolute_url())
        self.assert_preselected(
            self.match([self.datafile()]), self.datafile(), self.device_type
        )

    def test_hidden_device_view_is_only_unavailable(self):
        device_view = DeviceView.objects.create(
            device_type=self.device_type, grid_template_area="hidden-area"
        )
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DeviceView)
        ).update(constraints={"device_type__model": "Other"})
        response = self.catalog()
        row = response.context["rows"][0]
        self.assertEqual(row.state, datasource_import.TargetState.UNAVAILABLE)
        self.assertIsNone(row.device_view)
        self.assertEqual(row.problems, [datasource_import.TARGET_UNAVAILABLE])
        self.assertFalse(row.is_selectable)
        self.assertNotContains(response, device_view.get_absolute_url())
        self.assertNotContains(response, "hidden-area")
        review = self.review([(self.datafile(), self.device_type)]).context["review"]
        self.assertEqual(review.rows[0].error, datasource_import.TARGET_UNAVAILABLE)
        self.assert_preselected(self.match([self.datafile()]), self.datafile(), None)

    def test_file_named_only_by_its_suffix_is_listed(self):
        # Native sync skips dotfiles; other DataFile writers may not.
        for path in ("examples/Cisco/.yaml", "examples/.yml"):
            self.write_file(path, EXAMPLE_YAML.read_bytes())
            datafile = DataFile(source=self.source, path=path)
            datafile.refresh_from_disk(self.repo)
            datafile.save()
        rows = self.rows()
        for path in ("examples/Cisco/.yaml", "examples/.yml"):
            with self.subTest(path=path):
                self.assertEqual(rows[path].format, datasource_import.Format.YAML)
                self.assertIsNone(rows[path].suggestion)

    def test_device_type_of_hidden_manufacturer_is_not_a_permitted_target(self):
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(Manufacturer)
        ).update(constraints={"name": "Other"})
        self.assertIsNone(self.rows()[EXAMPLE_PATH].suggestion)
        response = self.review([(self.datafile(), self.device_type)])
        self.assertIsNone(response.context["review"])
        self.assertTrue(response.context["target_form"].errors)
        selection = datasource_import.Selection(
            datafile_id=self.datafile().pk, device_type_id=self.device_type.pk
        )
        review = datasource_import.review(self.user, self.source, [selection])
        self.assertEqual(review.rows[0].error, datasource_import.TARGET_UNAVAILABLE)

    def test_search_filters_paths_and_keeps_pagination(self):
        # The paginator keeps up to five orphans on the last page.
        paths = [f"examples/Ubiquiti/USW-{index:02}.yaml" for index in range(12)]
        for path in paths:
            self.write_file(path, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        self.assertEqual(set(self.rows(q="ubiquiti")), set(paths))
        first_page = self.catalog(q="ubiquiti", per_page=6)
        self.assertEqual(
            [row.datafile.path for row in first_page.context["rows"]], paths[:6]
        )
        self.assertContains(first_page, 'name="page" value="2"')
        self.assertContains(first_page, 'value="ubiquiti"')
        empty = self.catalog(q="nothing-matches")
        self.assertContains(empty, "No layout files match the search.")
        self.assertNotContains(empty, "Assign device types")


class CatalogSelectionTests(DataSourceImportTestBase):
    """Ticked files stay selected when the user changes the page or the search."""

    def setUp(self):
        super().setUp()
        self.paths = [f"examples/Ubiquiti/USW-{index:02}.yaml" for index in range(12)]
        for path in self.paths:
            self.write_file(path, EXAMPLE_YAML.read_bytes())
        self.source.sync()

    def navigate(self, response, ticked=(), **buttons):
        """Tick files on the page, then click a page or search button, as a browser does."""
        fields = submitted(response, START_URL)
        fields.setdefault("file", []).extend(str(self.datafile(p).pk) for p in ticked)
        for name, value in buttons.items():
            self.assertContains(response, f'name="{name}" value="{value}"')
            fields[name] = [str(value)]
        return self.client.post(reverse(START_URL), fields, follow=True)

    def paths_on_page(self, response):
        return [row.datafile.path for row in response.context["rows"]]

    def test_ticked_files_survive_page_and_search_changes(self):
        first = self.client.get(
            reverse(START_URL),
            {"source": self.source.pk, "prefix": "examples/", "per_page": 6},
        )
        self.assertEqual(self.paths_on_page(first), self.paths[:6])

        second = self.navigate(first, ticked=[self.paths[0]], page=2)
        self.assertEqual(self.paths_on_page(second), self.paths[6:] + [EXAMPLE_PATH])
        self.assertContains(second, "1 selected file is not shown here")

        searched = submitted(second, START_URL)
        searched["q"] = ["USW-1"]
        searched["file"] = searched.get("file", []) + [
            str(self.datafile(self.paths[11]).pk)
        ]
        searched["page"] = ["1"]
        found = self.client.post(reverse(START_URL), searched, follow=True)
        self.assertEqual(self.paths_on_page(found), self.paths[10:])
        self.assertContains(found, 'value="USW-1"')
        ticked = submitted(found, START_URL)["file"]
        self.assertEqual(
            sorted(ticked),
            sorted(str(self.datafile(p).pk) for p in (self.paths[0], self.paths[11])),
        )

        self.assertContains(found, f'formaction="{reverse(REVIEW_URL)}"')
        match = self.client.post(reverse(REVIEW_URL), submitted(found, START_URL))
        self.assertEqual(
            [datafile.path for datafile in match.context["target_form"].datafiles],
            [self.paths[0], self.paths[11]],
        )

    def test_unticked_file_is_dropped(self):
        first = self.client.get(
            reverse(START_URL),
            {"source": self.source.pk, "prefix": "examples/", "per_page": 6},
        )
        second = self.navigate(first, ticked=[self.paths[0]], page=2)
        back = submitted(second, START_URL)
        back["page"] = ["1"]
        first_again = self.client.post(reverse(START_URL), back, follow=True)
        fields = submitted(first_again, START_URL)
        self.assertEqual(fields["file"], [str(self.datafile(self.paths[0]).pk)])
        del fields["file"]
        fields["page"] = ["2"]
        second_again = self.client.post(reverse(START_URL), fields, follow=True)
        self.assertNotIn("file", submitted(second_again, START_URL))

    def test_too_many_ticked_files_are_rejected(self):
        response = self.client.get(
            reverse(START_URL),
            {
                "source": self.source.pk,
                "prefix": "examples/",
                "file": [str(pk) for pk in range(datasource_import.MAX_BATCH_ROWS + 1)],
            },
            follow=True,
        )
        self.assertContains(response, datasource_import.TOO_MANY_ROWS)
        self.assertNotIn("file", submitted(response, START_URL))

    def test_too_many_ticked_files_stay_out_of_the_redirect(self):
        # Each ticked file adds to the redirect URL, which a server limits in length.
        files = [str(pk) for pk in range(datasource_import.MAX_BATCH_ROWS + 1)]
        response = self.client.post(
            reverse(START_URL),
            {"source": self.source.pk, "prefix": "examples/", "file": files},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("file=", response.url)
        page = self.client.get(response.url)
        self.assertContains(page, datasource_import.TOO_MANY_ROWS)

    def test_redirect_does_not_use_the_request_path(self):
        # The resolver does not route a path such as "//host/", so call the view directly.
        request = RequestFactory().post(
            reverse(START_URL),
            {"source": self.source.pk, "prefix": "examples/", "page": "2"},
            PATH_INFO="//evil.example/",
        )
        request.user = self.user
        response = DeviceViewDataSourceImportView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url.partition("?")[0], reverse(START_URL))


class StrictYamlImportTests(DataSourceImportTestBase):
    def test_bundled_yaml_examples_pass_review_unchanged(self):
        selections = []
        for example in sorted(EXAMPLES.glob("yaml/*/*.yaml")):
            path = f"examples/{example.relative_to(EXAMPLES)}"
            self.write_file(path, example.read_bytes())
            manufacturer, _ = Manufacturer.objects.get_or_create(
                name=example.parent.name,
                defaults={"slug": slugify(example.parent.name)},
            )
            device_type, _ = DeviceType.objects.get_or_create(
                manufacturer=manufacturer,
                model=example.stem,
                defaults={"slug": slugify(example.stem)},
            )
            selections.append((path, device_type))
        self.source.sync()
        self.assertGreater(len(selections), 10)
        review = self.review(
            [(self.datafile(path), device_type) for path, device_type in selections]
        ).context["review"]
        self.assertEqual([row.error for row in review.rows], [""] * len(selections))
        self.assertTrue(review.token)

    def test_quoted_attribute_injection_is_rejected_before_any_write(self):
        for name, content in injected_layouts().items():
            with self.subTest(name):
                response = self.review_file(f"examples/{name}.yaml", content)
                review = response.context["review"]
                self.assertIn("css_classes", review.rows[0].error)
                self.assertEqual(review.token, "")
                self.assertNotContains(response, INJECTION)
                self.assertNotContains(response, "Apply import")
        self.assertFalse(DeviceView.objects.exists())
        self.assertFalse(
            ObjectChange.objects.filter(
                changed_object_type=ObjectType.objects.get_for_model(DeviceView)
            ).exists()
        )

    def test_malformed_yaml_error_is_escaped(self):
        response = self.review_file(
            "examples/malformed.yaml", "views: [<script>alert(1)</script>"
        )
        error = response.context["review"].rows[0].error
        self.assertIn("<script>", error)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)")

    def test_unbounded_sequence_is_rejected(self):
        content = EXAMPLE_YAML.read_text().replace("count: 12", "count: 1000000000", 1)
        started = time.monotonic()
        review = self.review_file("examples/huge.yaml", content).context["review"]
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("10000 elements", review.rows[0].error)

    def test_review_rejects_shapes_the_lenient_parser_cannot_read(self):
        huge = "1" + "0" * 3000
        contents = {
            "views-list": ("version: 1\nviews: [front]\n", "views"),
            "duplicate-key": (
                "views:\n  front: {elements: [{key: p1}]}\n  front: {elements: [{key: p2}]}\n",
                "duplicate key",
            ),
            "huge-key-number": (
                (
                    "views:\n  front:\n    rows:\n      - sequence: "
                    f"{{prefix: p, count: {huge}, step: {huge}}}\n"
                ),
                "prefix",
            ),
        }
        wide = ", ".join(
            f"{{key: p{i}, span: {{cols: {'9' * 4299}}}}}" for i in range(12)
        )
        contents["huge-span"] = (
            f"views:\n  front:\n    rows:\n      - elements: [{wide}]\n      - blank: 1\n",
            "span.cols",
        )
        for name, (content, message) in contents.items():
            with self.subTest(name):
                response = self.review_file(f"examples/{name}.yaml", content)
                self.assertEqual(response.status_code, 200)
                review = response.context["review"]
                self.assertIn(message, review.rows[0].error)
                self.assertEqual(review.token, "")

    def test_overlapping_elements_are_rejected(self):
        overlaps = {
            "base": "  front:\n    elements: [{key: big, span: {rows: 2, cols: 2}}, "
            "{key: small, at: {row: 2, col: 2}}]\n",
            "variant": "  front:\n    elements: [{key: big, span: {rows: 2, cols: 2}}]\n"
            "variants:\n  NM:\n    elements: [{key: small, at: {row: 2, col: 2}}]\n",
        }
        for name, where in (("base", "views.front"), ("variant", "variants.NM")):
            with self.subTest(name):
                content = "canvas: {columns: 4, rows: 2}\nviews:\n" + overlaps[name]
                response = self.review_file(f"examples/{name}.yaml", content)
                review = response.context["review"]
                self.assertIn(
                    f"{where}: element 'small' covers port 'big'.", review.rows[0].error
                )
                self.assertEqual(review.token, "")
                self.assertNotContains(response, "Apply import")
        self.assertFalse(DeviceView.objects.exists())

    def test_variants_that_do_not_render_are_rejected(self):
        variant = "variants:\n  NM:\n    view: rear\n    elements: [{key: te1}]\n"
        layouts = {
            "both-faces": (
                "views:\n  front: {elements: [{key: p1}]}\n  rear: {elements: [{key: p1}]}\n",
                "variants: a layout with both a front and a rear view cannot have variants.",
            ),
            "undefined-view": (
                "views:\n  front: {elements: [{key: p1}]}\n",
                "variants.NM.view: the layout has no rear view.",
            ),
        }
        for name, (views, message) in layouts.items():
            with self.subTest(name):
                response = self.review_file(f"examples/{name}.yaml", views + variant)
                review = response.context["review"]
                self.assertIn(message, review.rows[0].error)
                self.assertEqual(review.token, "")

    def test_local_definitions_keep_the_lenient_parser(self):
        # Rendering reads stored YAML with the lenient parser; only imports are strict.
        DeviceView.objects.create(
            device_type=self.device_type,
            yaml_layout="views:\n  front:\n    elements:\n      - {key: p1, extra: 1}\n",
        )
        tab = self.client.get(
            reverse("dcim:device_deviceview", kwargs={"pk": self.device.pk})
        )
        self.assertEqual(tab.status_code, 200)


class CssImportTests(DataSourceImportTestBase):
    def setUp(self):
        super().setUp()
        self.write_file(EXAMPLE_CSS_PATH, EXAMPLE_CSS.read_bytes())
        self.source.sync()

    def test_import_one_css_layout_end_to_end(self):
        css = EXAMPLE_CSS.read_text()
        catalog = self.client.get(
            reverse(START_URL),
            {"source": self.source.pk, "prefix": "examples/", "q": ".css"},
        )
        (row,) = catalog.context["rows"]
        self.assertEqual(row.datafile.path, EXAMPLE_CSS_PATH)
        self.assertEqual(row.format, datasource_import.Format.CSS)
        self.assertContains(catalog, "<td>CSS</td>", html=True)

        datafile = self.datafile(EXAMPLE_CSS_PATH)
        review = self.review([(datafile, self.device_type)])
        (reviewed,) = review.context["review"].rows
        self.assertEqual(reviewed.error, "")
        self.assertEqual(reviewed.format, datasource_import.Format.CSS)
        self.assertEqual(reviewed.render_mode_label, RenderMode.CSS.label)
        self.assertContains(review, "<td>CSS</td>", html=True)
        self.assertEqual(self.apply(review.context["review"].token).status_code, 200)

        device_view = DeviceView.objects.get(device_type=self.device_type)
        self.assertEqual(device_view.grid_template_area, css)
        self.assertEqual(device_view.yaml_layout, "")
        self.assertEqual(device_view.render_mode, RenderMode.CSS)
        tab = self.client.get(
            reverse("dcim:device_deviceview", kwargs={"pk": self.device.pk})
        )
        self.assertEqual(tab.status_code, 200)
        first_row = re.search(r'grid-template-areas:\s*"([^"]+)"', css)
        self.assertContains(tab, first_row.group(1))

    def test_css_whitespace_is_normalized(self):
        raw = '.deviceview.area {\n  grid-template-areas: "a\u00a0b\u2009c";\n}\n'
        self.write_file("examples/Cisco/spaces.css", raw.encode())
        self.source.sync()
        token = self.review_token(
            [(self.datafile("examples/Cisco/spaces.css"), self.device_type)]
        )
        self.apply(token)
        self.assertEqual(
            DeviceView.objects.get().grid_template_area,
            '.deviceview.area {\n  grid-template-areas: "a b c";\n}\n',
        )

    def test_css_line_endings_are_normalized(self):
        expected = '.deviceview.area {\n  grid-template-areas: "a b";\n}\n'
        for name, newline in (("crlf", "\r\n"), ("cr", "\r"), ("form-feed", "\f")):
            with self.subTest(name):
                DeviceView.objects.all().delete()
                path = f"examples/Cisco/{name}.css"
                self.write_file(path, expected.replace("\n", newline).encode())
                self.source.sync()
                token = self.review_token([(self.datafile(path), self.device_type)])
                self.assertEqual(self.apply(token).status_code, 200)
                self.assertEqual(DeviceView.objects.get().grid_template_area, expected)

    def test_unsafe_css_is_rejected_before_any_write(self):
        base = '.deviceview.area {\n  grid-template-areas: "a b";\n}\n'
        unsafe = {
            "html": base + "/* </style><script>alert(1)</script> */\n",
            "parse-error": base + ".deviceview.area { background: #000;",
            "at-rule": '@import "other.css";\n' + base,
            "url": base.replace("{", "{ background: url(x.png);"),
            "custom-property": base.replace("{", "{ --x: 1;"),
            "nested-rule": base.replace("{", "{ .x { background: #000; }"),
            "combinator": base.replace(".deviceview.area", ".deviceview .area"),
            "attribute": base.replace(".area", ".area[title]"),
            "pseudo-class": base.replace(".area", ".area:hover"),
            "selector-list": base.replace(".area", ".area, .deviceview.area.dRear"),
            "property": base.replace("{", "{ position: fixed;"),
            "image": base.replace("{", "{ background: linear-gradient(red, blue);"),
            "no-grid-area": ".deviceview.area { background: #000; }\n",
            "escape": base.replace('"a b"', '"\\61  b"'),
            "important": base.replace('";', '" !important;'),
            "row-widths": base.replace('"a b"', '"a b" "c"'),
            "split-area": base.replace('"a b"', '"a b a"'),
            "l-shaped-area": base.replace('"a b"', '"a a" "a b"'),
            "number-row": base.replace('"a b"', "1"),
            "percentage-row": base.replace('"a b"', '"a b" 5%'),
            "sizing": base.replace("{", "{ grid-auto-rows: 20px;"),
            "other-class": base.replace(".area", ".area.other"),
            "type-selector": base.replace(".deviceview", "div.deviceview"),
        }
        for name, css in unsafe.items():
            with self.subTest(name):
                response = self.review_file(f"examples/Cisco/{name}.css", css)
                self.assertEqual(response.status_code, 200)
                review = response.context["review"]
                self.assertIn("Invalid CSS layout", review.rows[0].error)
                self.assertEqual(review.token, "")
                self.assertNotContains(response, "Apply import")
                self.assertNotContains(response, "<script>alert(1)")
        self.assertFalse(DeviceView.objects.exists())
        self.assertFalse(
            ObjectChange.objects.filter(
                changed_object_type=ObjectType.objects.get_for_model(DeviceView)
            ).exists()
        )

    def test_css_with_a_byte_order_mark_is_accepted(self):
        css = '.deviceview.area {\n  grid-template-areas: "a b";\n}\n'
        self.write_file("examples/Cisco/bom.css", b"\xef\xbb\xbf" + css.encode())
        self.source.sync()
        self.apply(
            self.review_token(
                [(self.datafile("examples/Cisco/bom.css"), self.device_type)]
            )
        )
        self.assertEqual(DeviceView.objects.get().grid_template_area, css)

    def test_stored_css_cannot_close_the_style_element(self):
        # The import rejects this CSS; a form, API or earlier record can still hold it.
        DeviceView.objects.create(
            device_type=self.device_type,
            grid_template_area=(
                '.deviceview.area { grid-template-areas: "a<b"; }\n'
                "</style><script>alert(1)</script>\n"
            ),
        )
        plugin_config = {
            **settings.PLUGINS_CONFIG["netbox_device_view"],
            "show_on_device_tab": True,
        }
        with override_settings(
            PLUGINS_CONFIG={
                **settings.PLUGINS_CONFIG,
                "netbox_device_view": plugin_config,
            }
        ):
            pages = [
                self.client.get(
                    reverse("dcim:device_deviceview", kwargs={"pk": self.device.pk})
                ),
                self.client.get(self.device.get_absolute_url()),
            ]
        for page in pages:
            with self.subTest(url=page.request["PATH_INFO"]):
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, "<script>alert(1)")
                self.assertContains(page, '"a\\3c b"')
                self.assertContains(page, "\\3c /style>\\3c script>")

    def test_empty_or_non_utf8_css_is_rejected(self):
        self.write_file("examples/empty.css", b" \n\t\n")
        self.write_file(
            "examples/latin1.css", ".deviceview.area { caf\xe9 }".encode("latin-1")
        )
        self.source.sync()
        for path in ("examples/empty.css", "examples/latin1.css"):
            with self.subTest(path=path):
                review = self.review([(self.datafile(path), self.device_type)])
                self.assertTrue(review.context["review"].rows[0].error)
                self.assertEqual(review.context["review"].token, "")

    def test_bundled_css_examples_pass_review(self):
        selections = []
        for example in sorted(EXAMPLES.glob("*/*.css")):
            path = f"examples/{example.relative_to(EXAMPLES)}"
            self.write_file(path, example.read_bytes())
            manufacturer, _ = Manufacturer.objects.get_or_create(
                name=example.parent.name,
                defaults={"slug": slugify(example.parent.name)},
            )
            device_type, _ = DeviceType.objects.get_or_create(
                manufacturer=manufacturer,
                model=example.stem,
                defaults={"slug": slugify(example.stem)},
            )
            selections.append((path, device_type))
        self.source.sync()
        self.assertGreater(len(selections), 10)
        review = self.review(
            [(self.datafile(path), device_type) for path, device_type in selections]
        ).context["review"]
        self.assertEqual([row.error for row in review.rows], [""] * len(selections))
        self.assertTrue(review.token)

    def test_mixed_yaml_and_css_selection(self):
        other = DeviceType.objects.create(
            manufacturer=self.manufacturer, model="Other", slug="other"
        )
        token = self.review_token(
            [
                (self.datafile(), self.device_type),
                (self.datafile(EXAMPLE_CSS_PATH), other),
            ]
        )
        self.assertEqual(self.apply(token).status_code, 200)
        from_yaml = DeviceView.objects.get(device_type=self.device_type)
        from_css = DeviceView.objects.get(device_type=other)
        self.assertEqual(from_yaml.yaml_layout, EXAMPLE_YAML.read_text())
        self.assertEqual(from_css.yaml_layout, "")
        self.assertEqual(from_css.grid_template_area, EXAMPLE_CSS.read_text())

    def test_yaml_and_css_for_one_model_are_ambiguous(self):
        response = self.client.get(
            reverse(START_URL), {"source": self.source.pk, "prefix": "examples/"}
        )
        rows = {row.datafile.path: row for row in response.context["rows"]}
        for path in (EXAMPLE_PATH, EXAMPLE_CSS_PATH):
            with self.subTest(path=path):
                self.assertEqual(rows[path].suggestion, self.device_type)
                self.assertEqual(
                    rows[path].problems, [datasource_import.AMBIGUOUS_MATCH]
                )
        review = self.review(
            [
                (self.datafile(), self.device_type),
                (self.datafile(EXAMPLE_CSS_PATH), self.device_type),
            ]
        ).context["review"]
        self.assertEqual(
            [row.error for row in review.rows], [datasource_import.DUPLICATE_TARGET] * 2
        )
        self.assertEqual(review.token, "")
        self.review_token([(self.datafile(EXAMPLE_CSS_PATH), self.device_type)])


class ReviewTests(DataSourceImportTestBase):
    def test_file_deleted_while_review_reads_it_is_a_row_error(self):
        datafile = self.datafile()
        self.delete_when_read(datafile)
        response = self.review([(datafile, self.device_type)])
        self.assertEqual(response.status_code, 200)
        review = response.context["review"]
        self.assertEqual(
            [row.error for row in review.rows], [datasource_import.FILE_UNAVAILABLE]
        )
        self.assertEqual(review.token, "")

    def test_review_is_read_only_and_token_holds_no_content(self):
        datafile = self.datafile()
        token = self.review_token([(datafile, self.device_type)])
        self.assertFalse(DeviceView.objects.exists())

        payload = signing.loads(token, salt=datasource_import.SIGNING_SALT)
        self.assertEqual(payload["user"], self.user.pk)
        self.assertEqual(payload["source"], self.source.pk)
        baseline = payload["rows"][0].pop("baseline")
        self.assertRegex(baseline, r"^[0-9a-f]{64}$")
        self.assertEqual(
            payload["rows"],
            [
                {
                    "datafile_id": datafile.pk,
                    "path": EXAMPLE_PATH,
                    "sha256": hashlib.sha256(EXAMPLE_YAML.read_bytes()).hexdigest(),
                    "device_type_id": self.device_type.pk,
                    "action": "create",
                    "render_mode": "css",
                }
            ],
        )
        self.assertNotIn(
            str(self.repo), signing.b64_decode(token.split(":")[0].encode()).decode()
        )

    def test_hidden_device_type_cannot_be_selected(self):
        hidden = DeviceType.objects.create(
            manufacturer=self.manufacturer, model="Hidden", slug="hidden"
        )
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DeviceType)
        ).update(constraints={"model": "C9300-24T"})
        response = self.review([(self.datafile(), hidden)])
        self.assertIsNone(response.context["review"])
        self.assertTrue(response.context["target_form"].errors)

    def test_device_type_with_hidden_device_view_is_not_distinguished(self):
        hidden = DeviceView.objects.create(
            device_type=self.device_type, grid_template_area="hidden-area"
        )
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DeviceView)
        ).update(constraints={"device_type__model": "Other"})
        response = self.review(
            [(self.datafile(), self.device_type)], replace=[self.datafile()]
        )
        self.assertNotContains(response, "grid_template_area")
        self.assertNotContains(response, "hidden-area")
        self.assertNotContains(response, hidden.get_absolute_url())
        row = response.context["review"].rows[0]
        self.assertEqual(row.error, datasource_import.TARGET_UNAVAILABLE)
        self.assertIsNone(row.action)
        self.assertEqual(response.context["review"].token, "")

    def test_invalid_yaml_is_reported_without_token(self):
        self.write_file("examples/broken.yaml", b"version: 1\nviews: [not closed")
        self.write_file("examples/latin1.yaml", "meta: caf\xe9".encode("latin-1"))
        self.source.sync()
        for path in ("examples/broken.yaml", "examples/latin1.yaml"):
            with self.subTest(path=path):
                response = self.review([(self.datafile(path), self.device_type)])
                review = response.context["review"]
                self.assertTrue(review.rows[0].error)
                self.assertEqual(review.token, "")
                self.assertNotContains(response, "Apply import")

    def test_utf8_bom_is_accepted(self):
        self.write_file(
            "examples/bom.yaml", b"\xef\xbb\xbf" + EXAMPLE_YAML.read_bytes()
        )
        self.source.sync()
        self.review_token([(self.datafile("examples/bom.yaml"), self.device_type)])

    def test_non_numeric_file_selection_is_rejected(self):
        response = self.client.post(
            reverse(REVIEW_URL),
            {
                "source": self.source.pk,
                "prefix": "examples/",
                "step": "match",
                "file": [self.datafile().pk, "x"],
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_non_ascii_or_oversized_file_selection_is_rejected(self):
        for value in ("\u00b2", "\u0661", "9" * 5000):
            with self.subTest(value=value[:8]):
                response = self.client.post(
                    reverse(REVIEW_URL),
                    {
                        "source": self.source.pk,
                        "prefix": "examples/",
                        "step": "match",
                        "file": [value],
                    },
                )
                self.assertEqual(response.status_code, 302)

    def test_file_selection_above_bigint_is_not_queried(self):
        # PostgreSQL compares a bigint id with a larger value as numeric, without the index.
        value = str(2**63)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(
                reverse(REVIEW_URL),
                {
                    "source": self.source.pk,
                    "prefix": "examples/",
                    "step": "match",
                    "file": [value],
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse([q["sql"] for q in queries if value in q["sql"]])

    def test_two_files_for_one_device_type_are_rejected_until_one_is_kept(self):
        other_path = "examples/yaml/Cisco/Other.yaml"
        self.write_file(other_path, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        response = self.review(
            [
                (self.datafile(), self.device_type),
                (self.datafile(other_path), self.device_type),
            ]
        )
        review = response.context["review"]
        self.assertEqual(
            [row.error for row in review.rows], [datasource_import.DUPLICATE_TARGET] * 2
        )
        self.assertEqual(review.token, "")
        self.assertNotContains(response, "Apply import")

        fields = submitted(response, REVIEW_URL)
        fields["file"].remove(str(self.datafile(other_path).pk))
        kept = self.client.post(reverse(REVIEW_URL), fields).context["review"]
        self.assertEqual([row.path for row in kept.rows], [EXAMPLE_PATH])
        self.assertTrue(kept.token)

    def test_review_rejects_a_source_the_user_cannot_view(self):
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DataSource)
        ).update(constraints={"name": "Other"})
        selection = datasource_import.Selection(
            datafile_id=self.datafile().pk, device_type_id=self.device_type.pk
        )
        with self.assertRaises(datasource_import.ImportRejected):
            datasource_import.review(self.user, self.source, [selection])

    def test_sync_that_starts_during_review_is_rejected(self):
        """A sync that starts after the form check rejects the review, without a server error."""
        started = []

        def start_sync(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if not started and sql.startswith("SELECT") and '"core_datasource"' in sql:
                started.append(sql)
                DataSource.objects.filter(pk=self.source.pk).update(
                    status=DataSourceStatusChoices.SYNCING
                )
            return result

        with connection.execute_wrapper(start_sync):
            response = self.review([(self.datafile(), self.device_type)])
        self.assertTrue(started)
        self.assertContains(
            response, "The Data Source is not available for import.", status_code=400
        )
        self.assertContains(response, "No records were changed", status_code=400)
        self.assertContains(response, "Review again", status_code=400)
        self.assertNotContains(response, "Apply import", status_code=400)

    def test_review_and_apply_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        token = self.review_token([(self.datafile(), self.device_type)])
        self.assertEqual(
            client.post(reverse(REVIEW_URL), {"source": self.source.pk}).status_code,
            403,
        )
        self.assertEqual(
            client.post(reverse(APPLY_URL), {"token": token}).status_code, 403
        )
        self.assertFalse(DeviceView.objects.exists())


class ApplyTests(DataSourceImportTestBase):
    def test_tampered_token_is_rejected(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        self.assert_rejected(
            self.apply(token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1])
        )

    def test_expired_token_is_rejected(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        later = time.time() + datasource_import.REVIEW_MAX_AGE_SECONDS + 1
        with mock.patch("django.core.signing.time.time", return_value=later):
            self.assert_rejected(self.apply(token))

    def test_token_from_another_user_is_rejected(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        other = User.objects.create_user(username="other", is_superuser=True)
        self.client.force_login(other)
        self.assert_rejected(self.apply(token))

    def test_add_constraint_is_checked_after_save(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DeviceView), actions=["add"]
        ).update(constraints={"device_type__model": "Other"})
        self.assert_rejected(self.apply(token))

    @override_settings(EVENTS_PIPELINE=["tests.test_datasource_import.record_events"])
    def test_rejected_apply_sends_no_events(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DeviceView), actions=["add"]
        ).update(constraints={"device_type__model": "Other"})
        RECORDED_EVENTS.clear()
        self.assert_rejected(self.apply(token))
        self.assertEqual(RECORDED_EVENTS, [])

        ObjectPermission.objects.filter(actions=["add"]).update(constraints=None)
        self.assertEqual(self.apply(token).status_code, 200)
        self.assertEqual(
            [event["object_type"].model for event in RECORDED_EVENTS], ["deviceview"]
        )

    def test_concurrent_device_view_for_the_target_is_rejected(self):
        token = self.review_token([(self.datafile(), self.device_type)])

        self.compete_before_save(self.device_type)
        response = self.apply(token)
        self.assert_rejected(response)
        self.assertContains(
            response,
            datasource_import.CONCURRENT_CREATE,
            status_code=400,
        )

    def test_file_changed_after_review_is_rejected(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        self.write_file(EXAMPLE_PATH, EXAMPLE_YAML.read_bytes() + b"\n# edited\n")
        self.source.sync()
        self.assert_rejected(self.apply(token))

    def test_file_deleted_while_apply_reads_it_is_rejected(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        self.delete_when_read(self.datafile())
        response = self.apply(token)
        self.assert_rejected(response)
        self.assertContains(
            response, datasource_import.FILE_UNAVAILABLE, status_code=400
        )

    def test_file_replaced_by_malformed_yaml_is_changed_after_review(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        self.write_file(EXAMPLE_PATH, b"views: [not closed")
        self.source.sync()
        response = self.apply(token)
        self.assert_rejected(response)
        self.assertEqual(
            response.context["error"],
            f"{EXAMPLE_PATH}: {datasource_import.FILE_CHANGED}",
        )

    def test_oversized_file_at_another_path_is_too_large(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        DataFile.objects.filter(pk=self.datafile().pk).update(
            path="examples/yaml/Cisco/Moved.yaml",
            data=b"#" * (datasource_import.MAX_FILE_BYTES + 1),
        )
        response = self.apply(token)
        self.assert_rejected(response)
        self.assertEqual(
            response.context["error"],
            f"{EXAMPLE_PATH}: {datasource_import.FILE_TOO_LARGE}",
        )

    def test_apply_compares_the_stored_data_not_the_hash_column(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        DataFile.objects.filter(pk=self.datafile().pk).update(hash="0" * 64)
        self.assertContains(self.apply(token), "Created: 1")

    def test_pages_require_add_or_change_permission(self):
        ObjectPermission.objects.filter(actions=["add"]).delete()
        list_page = self.client.get(
            reverse("plugins:netbox_device_view:deviceview_list")
        )
        self.assertNotContains(list_page, reverse(START_URL))
        self.assertEqual(self.client.get(reverse(START_URL)).status_code, 403)
        self.assertEqual(self.client.post(reverse(REVIEW_URL)).status_code, 403)
        self.assertEqual(self.client.post(reverse(APPLY_URL)).status_code, 403)

    def test_anonymous_user_is_sent_to_login(self):
        self.client.logout()
        response = self.client.get(reverse(START_URL))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class BulkImportTests(DataSourceImportTestBase):
    # In catalog order, which sorts by path.
    BULK_PATHS = (
        "examples/Cisco/C9300-48P.css",
        EXAMPLE_PATH,
        "examples/yaml/Cisco/C9500-24Y4C.yaml",
    )

    def setUp(self):
        super().setUp()
        for path in (self.BULK_PATHS[0], self.BULK_PATHS[2]):
            self.write_file(path, (EXAMPLES.parent / path).read_bytes())
        self.source.sync()
        for model in ("C9300-48P", "C9500-24Y4C"):
            DeviceType.objects.create(
                manufacturer=self.manufacturer, model=model, slug=slugify(model)
            )

    def catalog(self):
        return self.client.get(
            reverse(START_URL), {"source": self.source.pk, "prefix": "examples/"}
        )

    def review_page(self, datafiles):
        """Submit the catalog and the match pages as a browser would."""
        # "Assign device types" posts the catalog form to the review URL.
        fields = submitted(self.catalog(), START_URL)
        fields["file"] = [str(datafile.pk) for datafile in datafiles]
        match = self.client.post(reverse(REVIEW_URL), fields)
        self.assertEqual(match.status_code, 200)
        return self.client.post(reverse(REVIEW_URL), submitted(match, REVIEW_URL))

    def write_sized(self, count, size, name="file"):
        for index in range(count):
            self.write_file(f"examples/limits/{name}{index:03}.css", b"\xff" * size)
        self.source.sync()
        return list(
            self.source.datafiles.filter(path__startswith="examples/limits/").order_by(
                "path"
            )
        )

    def test_mixed_bulk_create_end_to_end(self):
        datafiles = [self.datafile(path) for path in self.BULK_PATHS]
        review = self.review_page(datafiles)
        self.assertEqual(
            [row.format for row in review.context["review"].rows],
            [
                datasource_import.Format.CSS,
                datasource_import.Format.YAML,
                datasource_import.Format.YAML,
            ],
        )
        result = self.client.post(reverse(APPLY_URL), submitted(review, APPLY_URL))
        self.assertEqual(result.status_code, 200)
        created = DeviceView.objects.order_by("device_type__model")
        self.assertEqual(
            [device_view.device_type.model for device_view in created],
            ["C9300-24T", "C9300-48P", "C9500-24Y4C"],
        )
        for device_view in created:
            self.assertContains(result, device_view.get_absolute_url())
        self.assertEqual(
            ObjectChange.objects.filter(
                changed_object_type=ObjectType.objects.get_for_model(DeviceView),
                action="create",
            ).count(),
            3,
        )

    def test_mixed_bulk_create_and_update_end_to_end(self):
        self.grant(DeviceView, "change")
        css_target = DeviceType.objects.get(model="C9300-48P")
        old_yaml = (EXAMPLES.parent / self.BULK_PATHS[2]).read_text()
        yaml_record = DeviceView.objects.create(
            device_type=css_target,
            yaml_layout=old_yaml,
            grid_template_area=render_css(parse_yaml(old_yaml)),
            render_mode=RenderMode.SVG,
        )
        css_record = DeviceView.objects.create(
            device_type=self.device_type,
            grid_template_area=normalize_grid_template_area(EXAMPLE_CSS.read_text()),
        )
        datafiles = [self.datafile(path) for path in self.BULK_PATHS]
        skipped = self.review_page(datafiles)
        self.assertEqual(
            [row.action for row in skipped.context["review"].rows],
            [
                datasource_import.Action.SKIP,
                datasource_import.Action.SKIP,
                datasource_import.Action.CREATE,
            ],
        )

        fields = submitted(skipped, REVIEW_URL)
        for datafile in datafiles[:2]:
            fields[f"replace_{datafile.pk}"] = ["on"]
        review = self.client.post(reverse(REVIEW_URL), fields)
        rows = review.context["review"].rows
        self.assertEqual(
            [(row.action, row.render_mode_label) for row in rows],
            [
                (
                    datasource_import.Action.REPLACE,
                    f"{RenderMode.SVG.label} → {RenderMode.CSS.label}",
                ),
                (datasource_import.Action.REPLACE, RenderMode.CSS.label),
                (datasource_import.Action.CREATE, RenderMode.CSS.label),
            ],
        )
        result = self.client.post(reverse(APPLY_URL), submitted(review, APPLY_URL))
        self.assertContains(result, "Created: 1")
        self.assertContains(result, "Updated: 2")
        yaml_record.refresh_from_db()
        css_record.refresh_from_db()
        self.assertEqual(
            (yaml_record.yaml_layout, yaml_record.render_mode), ("", RenderMode.CSS)
        )
        self.assertEqual(css_record.yaml_layout, EXAMPLE_YAML.read_text())
        self.assertEqual(DeviceView.objects.count(), 3)
        changes = ObjectChange.objects.filter(
            changed_object_type=ObjectType.objects.get_for_model(DeviceView)
        )
        self.assertEqual(
            sorted(changes.values_list("action", flat=True)),
            ["create", "update", "update"],
        )

    def test_result_links_only_visible_device_views(self):
        ObjectPermission.objects.filter(
            object_types=ObjectType.objects.get_for_model(DeviceView), actions=["view"]
        ).update(constraints={"device_type__model": "C9300-24T"})
        datafiles = [self.datafile(path) for path in self.BULK_PATHS[:2]]
        result = self.client.post(
            reverse(APPLY_URL), submitted(self.review_page(datafiles), APPLY_URL)
        )
        self.assertEqual(result.status_code, 200)
        visible = DeviceView.objects.get(device_type__model="C9300-24T")
        hidden = DeviceView.objects.get(device_type__model="C9300-48P")
        self.assertContains(result, visible.get_absolute_url())
        self.assertNotContains(result, hidden.get_absolute_url())
        self.assertContains(result, str(hidden))

    def test_file_over_size_limit_is_rejected_before_decoding(self):
        limit = datasource_import.MAX_FILE_BYTES
        padded = EXAMPLE_YAML.read_bytes()
        padded += b"#" * (limit - len(padded) - 1) + b"\n"
        self.write_file(EXAMPLE_PATH, padded)
        self.write_file("examples/limits/big.css", b"\xff" * (limit + 1))
        self.source.sync()
        self.review_token([(self.datafile(), self.device_type)])
        review = self.review(
            [(self.datafile("examples/limits/big.css"), self.device_type)]
        ).context["review"]
        self.assertEqual(review.rows[0].error, datasource_import.FILE_TOO_LARGE)

    def test_batch_over_byte_limit_is_rejected_before_reading(self):
        limit = datasource_import.MAX_BATCH_BYTES
        size = datasource_import.MAX_FILE_BYTES
        datafiles = self.write_sized(limit // size + 1, size)
        at_limit = self.review(
            [(datafile, self.device_type) for datafile in datafiles[:-1]]
        ).context["review"]
        self.assertEqual(at_limit.errors, [])
        response = self.review([(datafile, self.device_type) for datafile in datafiles])
        review = response.context["review"]
        self.assertEqual(review.errors, [datasource_import.BATCH_TOO_LARGE])
        self.assertEqual(review.rows, [])
        self.assertEqual(review.token, "")
        self.assertContains(response, datasource_import.BATCH_TOO_LARGE)

    def test_batch_over_row_limit_is_rejected_before_reading(self):
        limit = datasource_import.MAX_BATCH_ROWS
        datafiles = self.write_sized(limit + 1, 1)
        at_limit = self.review(
            [(datafile, self.device_type) for datafile in datafiles[:-1]]
        ).context["review"]
        self.assertEqual(at_limit.errors, [])
        for step in ("match", "review"):
            with self.subTest(step=step):
                fields = {
                    "source": self.source.pk,
                    "prefix": "examples/",
                    "step": step,
                    "file": [datafile.pk for datafile in datafiles],
                }
                response = self.client.post(reverse(REVIEW_URL), fields, follow=True)
                self.assertEqual(response.redirect_chain[0][1], 302)
                self.assertContains(response, datasource_import.TOO_MANY_ROWS)
        selections = [
            datasource_import.Selection(datafile.pk, self.device_type.pk)
            for datafile in datafiles
        ]
        review = datasource_import.review(self.user, self.source, selections)
        self.assertEqual(review.errors, [datasource_import.TOO_MANY_ROWS])
        self.assertEqual(review.rows, [])

    def test_limits_use_the_stored_bytes_not_the_size_column(self):
        size = datasource_import.MAX_FILE_BYTES
        self.write_file("examples/big.css", b"\xff" * (size + 1))
        datafiles = self.write_sized(
            datasource_import.MAX_BATCH_BYTES // size + 1, size
        )
        big = self.datafile("examples/big.css")
        DataFile.objects.filter(pk__in=[big.pk, *(df.pk for df in datafiles)]).update(
            size=1
        )
        review = self.review(
            [(datafile, self.device_type) for datafile in datafiles]
        ).context["review"]
        self.assertEqual(review.errors, [datasource_import.BATCH_TOO_LARGE])
        single = self.review([(big, self.device_type)]).context["review"]
        self.assertEqual(single.rows[0].error, datasource_import.FILE_TOO_LARGE)

    def test_a_file_selected_twice_is_rejected(self):
        other = DeviceType.objects.get(model="C9500-24Y4C")
        selections = [
            datasource_import.Selection(self.datafile().pk, device_type.pk)
            for device_type in (self.device_type, other)
        ]
        review = datasource_import.review(self.user, self.source, selections)
        self.assertEqual(review.errors, [datasource_import.REPEATED_FILE])
        self.assertEqual(review.token, "")

    def test_invalid_unselected_file_does_not_block_a_valid_batch(self):
        self.write_file("examples/yaml/Cisco/Broken.yaml", b"views: [not closed")
        self.source.sync()
        datafiles = [self.datafile(path) for path in self.BULK_PATHS[:2]]
        review = self.review_page(datafiles)
        self.assertTrue(review.context["review"].token)
        result = self.client.post(reverse(APPLY_URL), submitted(review, APPLY_URL))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(DeviceView.objects.count(), 2)

    def test_mixed_valid_invalid_review_keeps_selections_and_corrections(self):
        broken = "examples/yaml/Cisco/Broken.yaml"
        self.write_file(broken, b"views: [not closed")
        self.source.sync()
        other = DeviceType.objects.get(model="C9500-24Y4C")
        response = self.review(
            [(self.datafile(), self.device_type), (self.datafile(broken), other)]
        )
        review = response.context["review"]
        errors = {row.path: row.error for row in review.rows}
        self.assertEqual(errors[EXAMPLE_PATH], "")
        self.assertTrue(errors[broken])
        self.assertEqual(review.token, "")
        self.assertFalse(DeviceView.objects.exists())

        fields = submitted(response, REVIEW_URL)
        self.assertEqual(
            fields["file"], [str(self.datafile(broken).pk), str(self.datafile().pk)]
        )
        self.assertEqual(
            fields[f"device_type_{self.datafile(broken).pk}"], [str(other.pk)]
        )

    def test_one_invalid_row_rolls_back_the_whole_apply(self):
        last = "examples/yaml/Cisco/C9500-24Y4C.yaml"
        datafiles = [self.datafile(path) for path in self.BULK_PATHS]
        review = self.review_page(datafiles)
        apply_fields = submitted(review, APPLY_URL)
        self.write_file(last, (EXAMPLES.parent / last).read_bytes() + b"\n# edited\n")
        self.source.sync()
        saved = []

        def record_save(sender, instance, created, **kwargs):
            saved.append(instance.device_type.model)

        post_save.connect(record_save, sender=DeviceView)
        self.addCleanup(post_save.disconnect, record_save, sender=DeviceView)
        response = self.client.post(reverse(APPLY_URL), apply_fields)
        # Apply checks the content of every row before it saves any row.
        self.assertEqual(saved, [])
        self.assert_rejected(response)
        self.assertContains(response, last, status_code=400)
        self.assertNotContains(response, "Created", status_code=400)

    def test_rejected_apply_offers_a_new_review_with_the_same_selections(self):
        manual = "examples/yaml/Cisco/Manual.yaml"
        self.write_file(manual, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        corrected = DeviceType.objects.get(model="C9500-24Y4C")
        selections = [
            (self.datafile(), self.device_type),
            (self.datafile(manual), corrected),
        ]
        review = self.review(selections)
        apply_fields = submitted(review, APPLY_URL)
        self.write_file(manual, EXAMPLE_YAML.read_bytes() + b"\n# edited\n")
        self.source.sync()
        rejected = self.client.post(reverse(APPLY_URL), apply_fields)
        self.assert_rejected(rejected)

        again = self.client.post(reverse(REVIEW_URL), submitted(rejected, REVIEW_URL))
        rows = again.context["review"].rows
        self.assertEqual(
            [(row.path, row.device_type) for row in rows],
            [(EXAMPLE_PATH, self.device_type), (manual, corrected)],
        )
        self.assertTrue(again.context["review"].token)

    def test_apply_ignores_selection_fields_outside_the_token(self):
        review = self.review([(self.datafile(), self.device_type)])
        fields = submitted(review, APPLY_URL)
        other = DeviceType.objects.get(model="C9500-24Y4C")
        fields[f"device_type_{self.datafile().pk}"] = [str(other.pk)]
        fields["file"].append(str(self.datafile(self.BULK_PATHS[0]).pk))
        self.assertEqual(self.client.post(reverse(APPLY_URL), fields).status_code, 200)
        self.assertEqual(
            list(DeviceView.objects.values_list("device_type", flat=True)),
            [self.device_type.pk],
        )


class CustomFieldTests(DataSourceImportTestBase):
    def custom_field(self, **fields):
        custom_field = CustomField.objects.create(type="text", required=True, **fields)
        custom_field.object_types.set([ObjectType.objects.get_for_model(DeviceView)])

    def test_create_applies_custom_field_defaults(self):
        self.custom_field(name="owner", default="ops")
        review = self.review([(self.datafile(), self.device_type)]).context["review"]
        self.assertEqual(review.rows[0].error, "")
        self.assertEqual(review.rows[0].action, datasource_import.Action.CREATE)
        self.assertEqual(self.apply(review.token).status_code, 200)
        self.assertEqual(DeviceView.objects.get().custom_field_data, {"owner": "ops"})

    def test_unique_default_rejects_the_second_create_of_one_batch(self):
        custom_field = CustomField.objects.create(
            type="text", name="asset", unique=True, default="A1"
        )
        custom_field.object_types.set([ObjectType.objects.get_for_model(DeviceView)])
        other = DeviceType.objects.create(
            manufacturer=self.manufacturer, model="Other", slug="other"
        )
        manual = "examples/yaml/Cisco/Manual.yaml"
        self.write_file(manual, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        token = self.review_token(
            [(self.datafile(), self.device_type), (self.datafile(manual), other)]
        )
        response = self.apply(token)
        self.assert_rejected(response)
        self.assertEqual(
            response.context["error"],
            f"{manual}: Custom field 'asset' must have a unique value.",
        )

    def test_required_custom_field_without_default_is_a_row_error(self):
        self.custom_field(name="owner")
        response = self.review([(self.datafile(), self.device_type)])
        self.assertEqual(response.status_code, 200)
        review = response.context["review"]
        self.assertIn("Missing required custom field 'owner'", review.rows[0].error)
        self.assertEqual(review.token, "")


class ReplacementTestBase(DataSourceImportTestBase):
    OLD_YAML = (
        EXAMPLE_YAML.read_text()
        .replace("Cisco Catalyst 9300-24T", "Old model")
        .replace("blank: 14", "blank: 13")
    )

    def setUp(self):
        super().setUp()
        self.grant(DeviceView, "change")

    def existing(self, **fields):
        fields = {
            "yaml_layout": self.OLD_YAML,
            "grid_template_area": render_css(parse_yaml(self.OLD_YAML)),
            "render_mode": RenderMode.SVG,
            **fields,
        }
        return DeviceView.objects.create(device_type=self.device_type, **fields)

    def device_view_changes(self):
        return ObjectChange.objects.filter(
            changed_object_type=ObjectType.objects.get_for_model(DeviceView)
        )

    def replace(self, path=EXAMPLE_PATH):
        """Review a replacement of the existing device view as a browser would."""
        response = self.review(
            [(self.datafile(path), self.device_type)], replace=[self.datafile(path)]
        )
        self.assertEqual(response.status_code, 200)
        return response

    def apply_page(self, review):
        return self.client.post(reverse(APPLY_URL), submitted(review, APPLY_URL))

    def tab(self):
        return self.client.get(
            reverse("dcim:device_deviceview", kwargs={"pk": self.device.pk})
        )


class ReplacementTests(ReplacementTestBase):
    def test_existing_target_defaults_to_skip(self):
        device_view = self.existing()
        match = self.match([self.datafile()])
        fields = submitted(match, REVIEW_URL)
        self.assertEqual(
            fields[f"device_type_{self.datafile().pk}"], [str(self.device_type.pk)]
        )
        self.assertNotIn(f"replace_{self.datafile().pk}", fields)

        response = self.client.post(reverse(REVIEW_URL), fields)
        review = response.context["review"]
        row = review.rows[0]
        self.assertEqual(row.action, datasource_import.Action.SKIP)
        self.assertEqual(row.error, "")
        self.assertEqual(
            [change.name for change in row.changes],
            ["yaml_layout", "grid_template_area"],
        )
        self.assertContains(response, "Old model")
        self.assertEqual(review.token, "")
        self.assertNotContains(response, "Apply import")
        self.assertContains(response, "Nothing to apply")
        device_view.refresh_from_db()
        self.assertEqual(device_view.yaml_layout, self.OLD_YAML)

    def test_yaml_replacement_keeps_render_mode_and_unrelated_fields(self):
        owner = CustomField.objects.create(name="owner", type="text")
        owner.object_types.set([ObjectType.objects.get_for_model(DeviceView)])
        tag = Tag.objects.create(name="Core", slug="core")
        device_view = self.existing(custom_field_data={"owner": "ops"})
        device_view.tags.add(tag)
        created = device_view.created

        review = self.replace()
        row = review.context["review"].rows[0]
        self.assertEqual(row.action, datasource_import.Action.REPLACE)
        self.assertEqual(row.render_mode_label, RenderMode.SVG.label)
        self.assertEqual(
            [change.name for change in row.changes],
            ["yaml_layout", "grid_template_area"],
        )
        # The template reads the diff several times; it is computed once per row.
        self.assertIs(row.changes, row.changes)
        lines = row.changes[0].lines
        self.assertEqual(lines[:2], [("hunk", "--- current"), ("hunk", "+++ proposed")])
        self.assertIn(("remove", '-  description: "Old model"'), lines)
        self.assertIn(("add", '+  description: "Cisco Catalyst 9300-24T"'), lines)
        self.assertContains(review, "-  description: &quot;Old model&quot;")
        self.assertContains(
            review, "+  description: &quot;Cisco Catalyst 9300-24T&quot;"
        )
        self.assertContains(review, "every device of that type")
        # The CSRF token is random text, so match the device link and text node only.
        self.assertNotContains(review, self.device.get_absolute_url())
        self.assertNotContains(review, ">sw1<")

        result = self.apply_page(review)
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Updated: 1")
        self.assertContains(result, device_view.get_absolute_url())
        updated = DeviceView.objects.get(pk=device_view.pk)
        self.assertEqual(updated.yaml_layout, EXAMPLE_YAML.read_text())
        self.assertEqual(
            updated.grid_template_area,
            render_css(parse_yaml(EXAMPLE_YAML.read_text())),
        )
        self.assertEqual(updated.render_mode, RenderMode.SVG)
        self.assertEqual(list(updated.tags.all()), [tag])
        self.assertEqual(updated.custom_field_data, {"owner": "ops"})
        self.assertEqual(updated.created, created)
        change = self.device_view_changes().get()
        self.assertEqual(change.action, "update")
        self.assertEqual(change.prechange_data["yaml_layout"], self.OLD_YAML)
        self.assertEqual(change.postchange_data["yaml_layout"], updated.yaml_layout)
        self.assertTrue(self.tab().context["use_svg"])

    def test_css_replacement_clears_yaml_and_selects_css(self):
        self.write_file(EXAMPLE_CSS_PATH, EXAMPLE_CSS.read_bytes())
        self.source.sync()
        device_view = self.existing()

        review = self.replace(EXAMPLE_CSS_PATH)
        row = review.context["review"].rows[0]
        self.assertEqual(row.action, datasource_import.Action.REPLACE)
        transition = f"{RenderMode.SVG.label} → {RenderMode.CSS.label}"
        self.assertEqual(row.render_mode_label, transition)
        self.assertContains(review, transition)
        self.assertEqual(
            [change.name for change in row.changes],
            ["yaml_layout", "grid_template_area", "render_mode"],
        )

        self.assertContains(self.apply_page(review), "Updated: 1")
        device_view.refresh_from_db()
        self.assertEqual(device_view.yaml_layout, "")
        self.assertEqual(device_view.render_mode, RenderMode.CSS)
        self.assertEqual(
            device_view.grid_template_area,
            normalize_grid_template_area(EXAMPLE_CSS.read_text()),
        )
        self.assertFalse(self.tab().context["use_svg"])

    def test_yaml_replacement_of_a_css_record_keeps_css_rendering(self):
        device_view = self.existing(
            yaml_layout="",
            grid_template_area=normalize_grid_template_area(EXAMPLE_CSS.read_text()),
            render_mode=RenderMode.CSS,
        )
        review = self.replace()
        row = review.context["review"].rows[0]
        self.assertEqual(row.render_mode_label, RenderMode.CSS.label)
        self.assertContains(self.apply_page(review), "Updated: 1")
        device_view.refresh_from_db()
        self.assertEqual(device_view.yaml_layout, EXAMPLE_YAML.read_text())
        self.assertEqual(device_view.render_mode, RenderMode.CSS)

    def test_identical_fields_are_unchanged_and_write_no_change(self):
        self.apply(self.review_token([(self.datafile(), self.device_type)]))
        device_view = DeviceView.objects.get()
        last_updated = device_view.last_updated
        for replace in ([], [self.datafile()]):
            with self.subTest(replace=bool(replace)):
                review = self.review(
                    [(self.datafile(), self.device_type)], replace=replace
                )
                row = review.context["review"].rows[0]
                self.assertEqual(row.action, datasource_import.Action.UNCHANGED)
                self.assertEqual(row.changes, [])
                self.assertContains(review, "already has these fields")
                result = self.apply_page(review)
                self.assertContains(result, "Unchanged: 1")
                self.assertContains(result, device_view.get_absolute_url())
        self.assertEqual(self.device_view_changes().count(), 1)
        device_view.refresh_from_db()
        self.assertEqual(device_view.last_updated, last_updated)

    def save_through_edit_form(self, device_view, **fields):
        """Save the device view as a browser submits the edit form: CRLF line endings."""
        data = {
            "device_type": device_view.device_type.pk,
            "render_mode": device_view.render_mode,
            **{
                name: getattr(device_view, name)
                for name in ("yaml_layout", "grid_template_area")
            },
            **fields,
        }
        for name in ("yaml_layout", "grid_template_area"):
            data[name] = data[name].replace("\n", "\r\n")
        response = self.client.post(
            reverse(
                "plugins:netbox_device_view:deviceview_edit",
                kwargs={"pk": device_view.pk},
            ),
            data,
        )
        self.assertEqual(response.status_code, 302)
        device_view.refresh_from_db()
        self.assertIn("\r\n", device_view.yaml_layout)

    def test_fields_saved_through_the_edit_form_are_unchanged(self):
        self.apply(self.review_token([(self.datafile(), self.device_type)]))
        device_view = DeviceView.objects.get()
        self.save_through_edit_form(device_view)
        self.assertNotEqual(device_view.yaml_layout, EXAMPLE_YAML.read_text())

        review = self.replace()
        row = review.context["review"].rows[0]
        self.assertEqual(row.action, datasource_import.Action.UNCHANGED)
        self.assertEqual(row.changes, [])
        self.assertContains(review, "already has these fields")

    def test_diff_ignores_line_endings_and_outer_whitespace(self):
        self.apply(self.review_token([(self.datafile(), self.device_type)]))
        device_view = DeviceView.objects.get()
        old_css = render_css(parse_yaml(self.OLD_YAML))
        self.save_through_edit_form(device_view, grid_template_area=old_css)

        row = self.replace().context["review"].rows[0]
        self.assertEqual(row.action, datasource_import.Action.REPLACE)
        self.assertEqual(
            [change.name for change in row.changes], ["grid_template_area"]
        )
        kinds = {kind for kind, _ in row.changes[0].lines}
        self.assertTrue({"add", "remove"} <= kinds, row.changes[0].lines)

    def test_large_fields_show_no_diff(self):
        padding = "# padding\n" * datasource_import.MAX_DIFF_LINES
        self.existing(yaml_layout=self.OLD_YAML + padding)
        review = self.replace()
        row = review.context["review"].rows[0]
        self.assertEqual(row.action, datasource_import.Action.REPLACE)
        changes = {change.name: change.lines for change in row.changes}
        self.assertIsNone(changes["yaml_layout"])
        self.assertTrue(changes["grid_template_area"])
        self.assertContains(review, "too large to show a difference", count=1)
        self.assertNotContains(review, "# padding")
        self.assertTrue(review.context["review"].token)

    def test_a_field_with_one_long_line_shows_no_diff(self):
        padding = "# " + "x" * datasource_import.MAX_DIFF_CHARS + "\n"
        self.existing(yaml_layout=self.OLD_YAML + padding)
        review = self.replace()
        changes = {
            change.name: change.lines
            for change in review.context["review"].rows[0].changes
        }
        self.assertIsNone(changes["yaml_layout"])
        self.assertContains(review, "too large to show a difference", count=1)
        self.assertNotContains(review, "xxxxxxxxxx")

    def test_identical_row_with_replace_needs_no_change_permission(self):
        self.apply(self.review_token([(self.datafile(), self.device_type)]))
        ObjectPermission.objects.filter(actions=["change"]).delete()
        review = self.replace()
        row = review.context["review"].rows[0]
        self.assertEqual(row.error, "")
        self.assertEqual(row.action, datasource_import.Action.UNCHANGED)
        self.assertContains(self.apply_page(review), "Unchanged: 1")

    def test_invalid_stored_render_mode_is_a_row_error(self):
        device_view = self.existing()
        DeviceView.objects.filter(pk=device_view.pk).update(render_mode="bogus")
        self.write_file(EXAMPLE_CSS_PATH, EXAMPLE_CSS.read_bytes())
        self.source.sync()
        for path in (EXAMPLE_PATH, EXAMPLE_CSS_PATH):
            for replace in (True, False):
                with self.subTest(path=path, replace=replace):
                    datafile = self.datafile(path)
                    response = self.review(
                        [(datafile, self.device_type)],
                        replace=[datafile] if replace else [],
                    )
                    self.assertEqual(response.status_code, 200)
                    review = response.context["review"]
                    self.assertIn("bogus", review.rows[0].error)
                    self.assertEqual(review.token, "")

    def test_repeated_apply_of_one_review_is_unchanged(self):
        create = self.review_token([(self.datafile(), self.device_type)])
        self.assertContains(self.apply(create), "Created: 1")
        self.assertContains(self.apply(create), "Unchanged: 1")

        DeviceView.objects.filter().update(yaml_layout=self.OLD_YAML)
        replace = self.replace()
        self.assertContains(self.apply_page(replace), "Updated: 1")
        self.assertContains(self.apply_page(replace), "Unchanged: 1")
        self.assertEqual(
            list(
                self.device_view_changes()
                .order_by("time")
                .values_list("action", flat=True)
            ),
            ["create", "update"],
        )

    def test_replace_requires_change_permission(self):
        self.existing()
        ObjectPermission.objects.filter(actions=["change"]).delete()
        row = self.replace().context["review"].rows[0]
        self.assertEqual(
            row.error, "You do not have permission to change this device view."
        )

    def test_change_constraint_is_checked_on_the_existing_record(self):
        self.existing()
        ObjectPermission.objects.filter(actions=["change"]).update(
            constraints={"render_mode": RenderMode.CSS}
        )
        review = self.replace()
        self.assertEqual(
            review.context["review"].rows[0].error,
            "You do not have permission to change this device view.",
        )
        self.assertEqual(review.context["review"].token, "")

    def test_change_constraint_is_checked_on_the_proposed_record(self):
        self.write_file(EXAMPLE_CSS_PATH, EXAMPLE_CSS.read_bytes())
        self.source.sync()
        device_view = self.existing()
        ObjectPermission.objects.filter(actions=["change"]).update(
            constraints={"render_mode": RenderMode.SVG}
        )
        response = self.apply_page(self.replace(EXAMPLE_CSS_PATH))
        self.assertContains(response, "No records were changed", status_code=400)
        self.assertContains(response, "violates your permissions", status_code=400)
        device_view.refresh_from_db()
        self.assertEqual(device_view.yaml_layout, self.OLD_YAML)
        self.assertFalse(self.device_view_changes().exists())

    def test_change_permission_removed_after_review_rejects_apply(self):
        self.existing()
        review = self.replace()
        ObjectPermission.objects.filter(actions=["change"]).delete()
        response = self.apply_page(review)
        self.assertContains(
            response,
            "You do not have permission to change this device view.",
            status_code=400,
        )
        self.assertEqual(DeviceView.objects.get().yaml_layout, self.OLD_YAML)

    def test_change_only_user_can_replace_but_not_create(self):
        self.existing()
        ObjectPermission.objects.filter(actions=["add"]).delete()
        list_page = self.client.get(
            reverse("plugins:netbox_device_view:deviceview_list")
        )
        self.assertContains(list_page, reverse(START_URL))
        self.assertEqual(self.client.get(reverse(START_URL)).status_code, 200)
        self.assertContains(self.apply_page(self.replace()), "Updated: 1")

        other = DeviceType.objects.create(
            manufacturer=self.manufacturer, model="Other", slug="other"
        )
        row = self.review([(self.datafile(), other)]).context["review"].rows[0]
        self.assertEqual(
            row.error, "You do not have permission to add this device view."
        )

    def test_quoted_attribute_injection_is_rejected_on_replacement(self):
        device_view = self.existing()
        for name, content in injected_layouts().items():
            with self.subTest(name):
                path = f"examples/{name}.yaml"
                self.write_file(path, content.encode())
                self.source.sync()
                response = self.replace(path)
                review = response.context["review"]
                self.assertIn("css_classes", review.rows[0].error)
                self.assertEqual(review.token, "")
                self.assertNotContains(response, INJECTION)
                self.assertNotContains(response, "Apply import")
        device_view.refresh_from_db()
        self.assertEqual(device_view.yaml_layout, self.OLD_YAML)
        self.assertFalse(self.device_view_changes().exists())

    def test_current_fields_in_the_diff_are_escaped(self):
        self.existing(
            yaml_layout="",
            grid_template_area="</pre><script>alert(1)</script>",
            render_mode=RenderMode.CSS,
        )
        response = self.replace()
        self.assertNotContains(response, "<script>alert(1)")
        self.assertContains(
            response, "-&lt;/pre&gt;&lt;script&gt;alert(1)&lt;/script&gt;"
        )

    def test_rejected_batch_leaves_the_replaced_target_unchanged(self):
        other = DeviceType.objects.create(
            manufacturer=self.manufacturer, model="Other", slug="other"
        )
        manual = "examples/yaml/Cisco/Manual.yaml"
        self.write_file(manual, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        device_view = self.existing()
        review = self.review(
            [(self.datafile(), self.device_type), (self.datafile(manual), other)],
            replace=[self.datafile()],
        )
        self.assertEqual(
            [row.action for row in review.context["review"].rows],
            [datasource_import.Action.REPLACE, datasource_import.Action.CREATE],
        )
        self.write_file(manual, EXAMPLE_YAML.read_bytes() + b"\n# edited\n")
        self.source.sync()
        rejected = self.apply_page(review)
        self.assertContains(rejected, "No records were changed", status_code=400)
        device_view.refresh_from_db()
        self.assertEqual(device_view.yaml_layout, self.OLD_YAML)
        self.assertEqual(list(DeviceView.objects.all()), [device_view])
        self.assertFalse(self.device_view_changes().exists())
        again = submitted(rejected, REVIEW_URL)
        self.assertEqual(again[f"replace_{self.datafile().pk}"], ["on"])
        self.assertNotIn(f"replace_{self.datafile(manual).pk}", again)


class DriftTests(ReplacementTestBase):
    """Apply rejects a review whose source, file, target or permissions changed."""

    def assert_stale(self, response):
        self.assertContains(response, "No records were changed", status_code=400)
        self.assertContains(response, "Review the import again", status_code=400)

    def test_apply_needs_a_usable_source(self):
        changes = {
            "deleted": lambda source: DataSource.objects.filter(pk=source.pk).delete(),
            "disabled": lambda source: DataSource.objects.filter(pk=source.pk).update(
                enabled=False
            ),
            "syncing": lambda source: DataSource.objects.filter(pk=source.pk).update(
                status=DataSourceStatusChoices.SYNCING
            ),
            "failed": lambda source: DataSource.objects.filter(pk=source.pk).update(
                status=DataSourceStatusChoices.FAILED
            ),
            "new": lambda source: DataSource.objects.filter(pk=source.pk).update(
                status=DataSourceStatusChoices.NEW
            ),
        }
        for name, change in changes.items():
            with self.subTest(name), transaction.atomic():
                token = self.review_token([(self.datafile(), self.device_type)])
                change(self.source)
                response = self.apply(token)
                self.assertContains(
                    response,
                    "The Data Source is not available for import.",
                    status_code=400,
                )
                self.assertFalse(DeviceView.objects.exists())
                transaction.set_rollback(True)

    def test_deleted_file_after_review_is_rejected(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        (self.repo / EXAMPLE_PATH).unlink()
        self.source.sync()
        response = self.apply(token)
        self.assertContains(
            response, datasource_import.FILE_UNAVAILABLE, status_code=400
        )
        self.assertFalse(DeviceView.objects.exists())

    def test_add_permission_removed_after_review_rejects_apply(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        ObjectPermission.objects.filter(actions=["add"]).delete()
        response = self.apply(token)
        self.assertContains(
            response,
            "You do not have permission to add this device view.",
            status_code=400,
        )
        self.assertFalse(DeviceView.objects.exists())

    def test_edit_after_review_is_not_overwritten(self):
        device_view = self.existing()
        review = self.replace()
        edited = self.OLD_YAML.replace("blank: 13", "blank: 12")
        DeviceView.objects.filter(pk=device_view.pk).update(yaml_layout=edited)
        self.assert_stale(self.apply_page(review))
        device_view.refresh_from_db()
        self.assertEqual(device_view.yaml_layout, edited)
        self.assertFalse(self.device_view_changes().exists())

    def test_render_mode_change_after_review_is_drift(self):
        device_view = self.existing()
        review = self.replace()
        DeviceView.objects.filter(pk=device_view.pk).update(render_mode=RenderMode.CSS)
        self.assertContains(
            self.apply_page(review), "No records were changed", status_code=400
        )
        device_view.refresh_from_db()
        self.assertEqual(device_view.yaml_layout, self.OLD_YAML)

    def test_delete_after_review_is_drift(self):
        # CSS rendering keeps the render-mode check equal, so the baseline check decides.
        self.existing(render_mode=RenderMode.CSS)
        review = self.replace()
        DeviceView.objects.all().delete()
        response = self.apply_page(review)
        self.assert_stale(response)
        self.assertContains(response, datasource_import.STALE_TARGET, status_code=400)
        self.assertFalse(DeviceView.objects.exists())

    def test_device_view_moved_to_another_type_after_review_is_drift(self):
        device_view = self.existing()
        review = self.replace()
        other = DeviceType.objects.create(
            manufacturer=self.manufacturer, model="Other", slug="other"
        )
        DeviceView.objects.filter(pk=device_view.pk).update(device_type=other)
        DeviceView.objects.create(
            device_type=self.device_type,
            yaml_layout=self.OLD_YAML,
            grid_template_area=device_view.grid_template_area,
            render_mode=RenderMode.SVG,
        )
        self.assert_stale(self.apply_page(review))
        self.assertEqual(
            set(DeviceView.objects.values_list("yaml_layout", flat=True)),
            {self.OLD_YAML},
        )

    def test_renamed_device_type_after_review_is_drift(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        DeviceType.objects.filter(pk=self.device_type.pk).update(model="Renamed")
        self.assert_stale(self.apply(token))
        self.assertFalse(DeviceView.objects.exists())

    def test_create_after_review_is_drift(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        self.existing()
        self.assert_stale(self.apply(token))
        self.assertEqual(DeviceView.objects.get().yaml_layout, self.OLD_YAML)

    def test_replay_needs_the_reviewed_permission(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        self.assertContains(self.apply(token), "Created: 1")
        ObjectPermission.objects.filter(actions=["add"]).update(
            constraints={"render_mode": RenderMode.SVG}
        )
        response = self.apply(token)
        self.assertContains(
            response,
            "You do not have permission to add this device view.",
            status_code=400,
        )

    def test_replay_after_the_record_changed_again_is_rejected(self):
        token = self.review_token([(self.datafile(), self.device_type)])
        self.assertContains(self.apply(token), "Created: 1")
        DeviceView.objects.update(yaml_layout=self.OLD_YAML)
        self.assert_stale(self.apply(token))
        self.assertEqual(DeviceView.objects.get().yaml_layout, self.OLD_YAML)

    def test_constraint_conflict_reports_no_partial_success(self):
        other = DeviceType.objects.create(
            manufacturer=self.manufacturer, model="Other", slug="other"
        )
        manual = "examples/yaml/Cisco/Manual.yaml"
        self.write_file(manual, EXAMPLE_YAML.read_bytes())
        self.source.sync()
        token = self.review_token(
            [(self.datafile(), self.device_type), (self.datafile(manual), other)]
        )

        self.compete_before_save(other)
        response = self.apply(token)
        self.assert_rejected(response)
        self.assertContains(
            response, datasource_import.CONCURRENT_CREATE, status_code=400
        )
        self.assertNotContains(response, "Created", status_code=400)

    def test_token_without_a_baseline_is_not_valid(self):
        payload = signing.loads(
            self.review_token([(self.datafile(), self.device_type)]),
            salt=datasource_import.SIGNING_SALT,
        )
        for row in payload["rows"]:
            del row["baseline"]
        token = signing.dumps(payload, salt=datasource_import.SIGNING_SALT)
        self.assert_rejected(self.apply(token))


class MigrationTests(TestCase):
    def test_import_needs_no_migration(self):
        call_command("makemigrations", "netbox_device_view", check=True, dry_run=True)
