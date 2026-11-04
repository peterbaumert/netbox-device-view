"""Apply holds its target locks only for cheap checks and the writes, not for file content work."""

import re
import sys
from collections import Counter
from contextlib import contextmanager

from core.models import DataFile
from dcim.models import DeviceType
from django.db import connection, transaction
from django.urls import reverse

from netbox_device_view import datasource_import
from netbox_device_view.layout.css_profile import validate_css
from netbox_device_view.layout.parser import parse
from netbox_device_view.layout.renderers.css_grid import render
from netbox_device_view.models import DeviceView, RenderMode

from .test_datasource_import import APPLY_URL, EXAMPLE_PATH, EXAMPLES
from .test_datasource_import_concurrency import OTHER_YAML, ConcurrentApplyTestBase

CSS_PATH = "examples/Cisco/C9300-48P.css"
# Code objects match a call through any import alias.
WATCHED = {
    function.__code__: f"{function.__module__}.{function.__qualname__}"
    for function in (parse, render, validate_css)
}
DATA_COLUMN = '"core_datafile"."data"'
# The stored size, and the hash of a file within the size limit, stay in the database.
ALLOWED_DATA = re.compile(
    r'CASE WHEN LENGTH\("core_datafile"\."data"\) <= %s'
    r' THEN encode\(sha256\("core_datafile"\."data"\), %s\) ELSE NULL END'
    r'|LENGTH\("core_datafile"\."data"\)'
)


class LockScopeGuard:
    """Record file content loads and layout work while a transaction holds a lock."""

    def __init__(self):
        self.lock_block = None
        self.lock_seen = False
        self.unlocked_calls = Counter()
        self.violations = []

    @property
    def locked(self):
        # The lock lasts until its transaction ends with a commit or a rollback.
        return any(block is self.lock_block for block in connection.atomic_blocks)

    def execute(self, execute, sql, params, many, context):
        if (
            not self.locked
            and connection.in_atomic_block
            and "FOR NO KEY UPDATE" in sql
        ):
            self.lock_block = connection.atomic_blocks[-1]
            self.lock_seen = True
        if self.locked and DATA_COLUMN in ALLOWED_DATA.sub("", sql):
            self.violations.append(sql)
        return execute(sql, params, many, context)

    def profile(self, frame, event, arg):
        if event == "call" and frame.f_code in WATCHED:
            name = WATCHED[frame.f_code]
            if self.locked:
                self.violations.append(name)
            else:
                self.unlocked_calls[name] += 1

    @contextmanager
    def watch(self):
        previous = sys.getprofile()
        sys.setprofile(self.profile)
        try:
            with connection.execute_wrapper(self.execute):
                yield self
        finally:
            sys.setprofile(previous)

    def check(self, test):
        test.assertEqual(self.violations, [])
        test.assertTrue(self.lock_seen, "no lock query was observed")
        test.assertEqual(set(self.unlocked_calls), set(WATCHED.values()))


class LockScopeTests(ConcurrentApplyTestBase):
    def setUp(self):
        super().setUp()
        self.write_file(CSS_PATH, (EXAMPLES.parent / CSS_PATH).read_bytes())
        self.source.sync()
        self.css_type = DeviceType.objects.create(
            manufacturer=self.device_type.manufacturer,
            model="C9300-48P",
            slug="c9300-48p",
        )
        self.existing = DeviceView.objects.create(
            device_type=self.css_type,
            yaml_layout=OTHER_YAML,
            grid_template_area=render(parse(OTHER_YAML)),
            render_mode=RenderMode.SVG,
        )
        # One YAML create and one CSS replacement.
        self.apply_token = self.token_for(
            [(EXAMPLE_PATH, self.device_type), (CSS_PATH, self.css_type)],
            replace=True,
        )

    def guarded_apply(self):
        guard = LockScopeGuard()
        with guard.watch():
            response = self.clients[0].post(
                reverse(APPLY_URL), {"token": self.apply_token}
            )
        guard.check(self)
        return response

    def test_apply_does_no_content_work_under_the_lock(self):
        response = self.guarded_apply()
        self.assertContains(response, "Created: 1")
        self.assertContains(response, "Updated: 1")
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.render_mode, RenderMode.CSS)

    def test_rejected_apply_does_no_content_work_under_the_lock(self):
        DeviceView.objects.filter(pk=self.existing.pk).update(yaml_layout="")
        response = self.guarded_apply()
        self.assertContains(response, datasource_import.STALE_TARGET, status_code=400)
        self.assertFalse(
            DeviceView.objects.filter(device_type=self.device_type).exists()
        )

    def test_guard_fails_on_content_work_under_a_lock(self):
        guard = LockScopeGuard()
        with guard.watch():
            validate_css(render(parse(OTHER_YAML)))
            with transaction.atomic():
                list(
                    DeviceType.objects.select_for_update(no_key=True)
                    .filter(pk=self.device_type.pk)
                    .values_list("pk", flat=True)
                )
                parse(OTHER_YAML)
                bytes(DataFile.objects.get(path=EXAMPLE_PATH).data)
            parse(OTHER_YAML)
        self.assertEqual(guard.violations[0], WATCHED[parse.__code__])
        self.assertIn(DATA_COLUMN, guard.violations[1])
        self.assertEqual(len(guard.violations), 2)
        with self.assertRaises(AssertionError):
            guard.check(self)
