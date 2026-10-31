"""
Import device view layouts from synchronized NetBox Data Sources.

This module owns discovery, validation of selected content, review tokens, and
transactional apply. Views call it and only handle forms and display.
"""

from __future__ import annotations

import copy
import dataclasses
import difflib
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import PurePosixPath

from core.choices import DataSourceStatusChoices
from core.models import DataFile, DataSource
from core.signals import clear_events
from dcim.models import DeviceType, Manufacturer
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError, models, router, transaction
from django.db.models import Case, CharField, Func, Q, Sum, Value, When
from django.db.models.functions import Length
from extras.models import CustomField

from .layout import LayoutParseError, parse_yaml, render_css
from .layout.css_profile import validate_css
from .models import DeviceView, RenderMode
from .utils import normalize_grid_template_area

SIGNING_SALT = "netbox_device_view.datasource_import.review"
REVIEW_MAX_AGE_SECONDS = 15 * 60
TARGET_UNAVAILABLE = "The device type is not available as an import target."
NO_MATCH = "No device type matches this path."
AMBIGUOUS_MATCH = "Several files below this directory match this device type."
DUPLICATE_TARGET = "Several selected files target this device type. Keep one of them."
MAX_FILE_BYTES = 256 * 1024
MAX_BATCH_BYTES = 2 * 1024 * 1024
MAX_BATCH_ROWS = 100
# The review shows no diff for a field with more lines or characters than these, in both versions together.
MAX_DIFF_LINES = 1000
MAX_DIFF_CHARS = 64 * 1024
FILE_TOO_LARGE = f"The file is larger than {MAX_FILE_BYTES // 1024} KiB."
BATCH_TOO_LARGE = (
    f"The selected files are larger than {MAX_BATCH_BYTES // 1024 // 1024} MiB"
    " in total."
)
TOO_MANY_ROWS = f"Select at most {MAX_BATCH_ROWS} files in one import."
FILE_UNAVAILABLE = "The file is not available in this Data Source."
REVIEW_AGAIN = "Review the import again."
INVALID_TOKEN = "The review token is not valid."
STALE_TARGET = (
    f"the device view or its device type changed after review. {REVIEW_AGAIN}"
)
CONCURRENT_CREATE = (
    "another change created a device view for this device type during the import. "
    + REVIEW_AGAIN
)
REPEATED_FILE = "Each file can be selected only once."
FILE_CHANGED = "the file changed after review."
CONTENT_FIELDS = ("yaml_layout", "grid_template_area")
IMPORTED_FIELDS = (*CONTENT_FIELDS, "render_mode")


class Format(models.TextChoices):
    YAML = "yaml", "YAML"
    CSS = "css", "CSS"


FORMAT_SUFFIXES = {".yaml": Format.YAML, ".yml": Format.YAML, ".css": Format.CSS}
# tinycss2 reads these as "\n", and validate_css requires an unchanged round trip.
CSS_NEWLINES = re.compile(r"\r\n?|\f")
# Browsers submit CRLF line endings, and the edit form strips the value.
_FORM_NEWLINES = re.compile(r"\r\n?")


class ImportRejected(Exception):
    """The whole import batch was rejected; nothing was written."""


class Action(models.TextChoices):
    CREATE = "create", "Create"
    REPLACE = "replace", "Replace"
    UNCHANGED = "unchanged", "Unchanged"
    SKIP = "skip", "Skip"


# The object permission action that each writing action needs.
ACTION_PERMISSIONS = {Action.CREATE: "add", Action.REPLACE: "change"}
# The permission that a replayed row needs on a record that already has its fields.
REPLAY_PERMISSIONS = {**ACTION_PERMISSIONS, Action.UNCHANGED: "view"}


class TargetState(models.TextChoices):
    NO_DEVICE_VIEW = "none", "No device view"
    EXISTING = "existing", "Existing device view"
    UNAVAILABLE = "unavailable", "Unavailable"


@dataclass
class CatalogRow:
    """One catalog file with its suggested device type and the state of that target."""

    datafile: DataFile
    suggestion: DeviceType | None = None
    state: TargetState | None = None
    device_view: DeviceView | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def format(self) -> Format:
        return _file_format(self.datafile.path)

    @property
    def is_selectable(self):
        return not self.problems


@dataclass(frozen=True)
class Selection:
    datafile_id: int
    device_type_id: int
    replace: bool = False


@dataclass(frozen=True)
class ReviewedRow:
    """One reviewed row, as the signed review token stores it."""

    datafile_id: int
    path: str
    sha256: str
    device_type_id: int
    action: Action
    render_mode: str
    baseline: str

    def to_payload(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_payload(cls, payload: dict) -> ReviewedRow:
        return cls(**{**payload, "action": Action(payload["action"])})


@dataclass(frozen=True)
class FieldChange:
    """One imported field that a review row changes, with its unified diff lines or None."""

    name: str
    lines: list[tuple[str, str]] | None


@dataclass
class ReviewRow:
    """One row of the review page."""

    datafile_id: int
    action: Action | None = None
    path: str = ""
    format: Format | None = None
    device_type: DeviceType | None = None
    current_fields: dict[str, str] = field(default_factory=dict)
    proposed_fields: dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def render_mode_label(self):
        if not self.proposed_fields:
            return ""
        proposed = RenderMode(self.proposed_fields["render_mode"]).label
        current = self.current_fields.get("render_mode")
        if current is None or current == self.proposed_fields["render_mode"]:
            return proposed
        return f"{RenderMode(current).label} → {proposed}"

    @property
    def is_skipped(self):
        return self.action == Action.SKIP

    @cached_property
    def changes(self) -> list[FieldChange]:
        """Return a line diff for each imported field that differs from the current record."""
        return [
            FieldChange(name, _diff_lines(self.current_fields[name], value))
            for name, value in self.proposed_fields.items()
            if self.current_fields
            and _comparable_text(self.current_fields[name]) != _comparable_text(value)
        ]


@dataclass
class Review:
    source: DataSource
    rows: list[ReviewRow]
    errors: list[str] = field(default_factory=list)
    token: str = ""

    @property
    def is_valid(self):
        return (
            bool(self.rows)
            and not self.errors
            and not any(row.error for row in self.rows)
        )


@dataclass
class ApplyResult:
    created: list[DeviceView] = field(default_factory=list)
    updated: list[DeviceView] = field(default_factory=list)
    unchanged: list[DeviceView] = field(default_factory=list)

    @property
    def device_views(self):
        return self.created + self.updated + self.unchanged


@dataclass(frozen=True)
class _SourceFile:
    path: str
    format: Format
    content: bytes

    @property
    def sha256(self):
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class _PreparedContent:
    """The validated content fields of one file, as text without the file itself."""

    format: Format
    yaml_layout: str
    grid_template_area: str


@dataclass(frozen=True)
class _Proposal:
    device_type: DeviceType
    device_view: DeviceView | None
    action: Action
    fields: dict[str, str]
    baseline: str


def can_import(user) -> bool:
    """Return True when the user may add or change device views, so an import can do something."""
    return any(_has_permission(user, action) for action in ACTION_PERMISSIONS)


def importable_sources(user):
    """Return the Data Sources the user can view that have synchronized successfully."""
    return DataSource.objects.restrict(user, "view").filter(
        enabled=True, status=DataSourceStatusChoices.COMPLETED
    )


def normalize_prefix(raw: str) -> str:
    """Return a relative POSIX directory prefix ending in "/", or "" for the source root."""
    if "\x00" in raw:
        raise ValidationError("The directory prefix must not contain NUL bytes.")
    path = PurePosixPath(raw.strip())
    if path.is_absolute():
        raise ValidationError("The directory prefix must be relative to the source.")
    if ".." in path.parts:
        raise ValidationError("The directory prefix must not contain '..'.")
    parts = [part for part in path.parts if part != "."]
    return "/".join(parts) + "/" if parts else ""


def catalog(user, source: DataSource, prefix: str, search: str = ""):
    """Return the viewable importable DataFiles below the prefix, without their content."""
    suffixes = Q()
    for suffix in FORMAT_SUFFIXES:
        suffixes |= Q(path__endswith=suffix)
    datafiles = (
        DataFile.objects.restrict(user, "view")
        .filter(source=source, path__startswith=prefix)
        .filter(suffixes)
        .only("pk", "path")
        .order_by("path")
    )
    if search:
        datafiles = datafiles.filter(path__icontains=search)
    return datafiles


def permitted_device_types(user):
    """Return the device types the user can view, limited to visible manufacturers."""
    return DeviceType.objects.restrict(user, "view").filter(
        manufacturer__in=Manufacturer.objects.restrict(user, "view")
    )


def catalog_rows(user, source: DataSource, prefix: str, datafiles) -> list[CatalogRow]:
    """Return a catalog row with a suggested device type and its state for each DataFile."""
    matches = _match_paths(
        user, catalog(user, source, prefix).values_list("path", flat=True)
    )
    # A device type matched by several files below the prefix is ambiguous.
    files_per_device_type = Counter(dt.pk for dt in matches.values())
    rows = [
        CatalogRow(datafile=datafile, suggestion=matches.get(datafile.path))
        for datafile in datafiles
    ]
    device_view_ids = dict(
        DeviceView.objects.filter(
            device_type__in=[row.suggestion for row in rows if row.suggestion]
        ).values_list("device_type", "pk")
    )
    visible = DeviceView.objects.restrict(user, "view").in_bulk(
        device_view_ids.values()
    )
    for row in rows:
        if row.suggestion is None:
            row.problems.append(NO_MATCH)
            continue
        if files_per_device_type[row.suggestion.pk] > 1:
            row.problems.append(AMBIGUOUS_MATCH)
        device_view_id = device_view_ids.get(row.suggestion.pk)
        if device_view_id is None:
            row.state = TargetState.NO_DEVICE_VIEW
        elif device_view_id in visible:
            row.state = TargetState.EXISTING
            row.device_view = visible[device_view_id]
        else:
            row.state = TargetState.UNAVAILABLE
            row.problems.append(TARGET_UNAVAILABLE)
    return rows


def review(user, source: DataSource, selections: list[Selection]) -> Review:
    """Validate the selection and return a read-only review with a signed token."""
    source = _importable_source(user, source.pk)
    errors = _batch_errors(user, source, selections)
    if errors:
        return Review(source=source, rows=[], errors=errors)
    rows = []
    reviewed = []
    for selection in selections:
        row = ReviewRow(datafile_id=selection.datafile_id)
        rows.append(row)
        try:
            source_file = _read_source_file(user, source, selection.datafile_id)
        except ValidationError as exc:
            row.error = " ".join(exc.messages)
            continue
        row.path = source_file.path
        row.format = source_file.format
        try:
            proposal = _decide(
                user,
                _prepare_content(source_file),
                selection.device_type_id,
                replace=selection.replace,
            )
        except ValidationError as exc:
            row.error = " ".join(exc.messages)
            continue
        row.action = proposal.action
        row.device_type = proposal.device_type
        row.proposed_fields = proposal.fields
        if proposal.device_view is not None:
            row.current_fields = _imported_fields(proposal.device_view)
        if proposal.action == Action.SKIP:
            continue
        reviewed.append(
            ReviewedRow(
                datafile_id=selection.datafile_id,
                path=source_file.path,
                sha256=source_file.sha256,
                device_type_id=proposal.device_type.pk,
                action=proposal.action,
                render_mode=proposal.fields["render_mode"],
                baseline=proposal.baseline,
            )
        )
    files_per_device_type = Counter(
        row.device_type.pk for row in rows if row.device_type
    )
    for row in rows:
        if row.device_type and files_per_device_type[row.device_type.pk] > 1:
            row.error = DUPLICATE_TARGET

    result = Review(source=source, rows=rows)
    if result.is_valid and reviewed:
        result.token = signing.dumps(
            {
                "user": user.pk,
                "source": source.pk,
                "rows": [row.to_payload() for row in reviewed],
            },
            salt=SIGNING_SALT,
        )
    return result


def apply(user, token: str) -> ApplyResult:
    """Apply a reviewed import in one transaction and report what it did to each record."""
    try:
        payload = signing.loads(
            token, salt=SIGNING_SALT, max_age=REVIEW_MAX_AGE_SECONDS
        )
    except signing.SignatureExpired as exc:
        raise ImportRejected(
            "The review has expired. Review the import again."
        ) from exc
    except signing.BadSignature as exc:
        raise ImportRejected(INVALID_TOKEN) from exc
    if not user.is_authenticated or payload["user"] != user.pk:
        raise ImportRejected("The review token belongs to a different user.")
    try:
        rows = [ReviewedRow.from_payload(row) for row in payload["rows"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportRejected(INVALID_TOKEN) from exc
    for action in {row.action for row in rows} & ACTION_PERMISSIONS.keys():
        if not _has_permission(user, action):
            raise ImportRejected(_permission_denied(action))
    source = _importable_source(user, payload["source"])
    # Content work runs before the transaction, so the target locks are held only for cheap checks.
    contents = [_prepare_row(user, source, row) for row in rows]

    result = ApplyResult()
    outcomes = {
        Action.CREATE: result.created,
        Action.REPLACE: result.updated,
        Action.UNCHANGED: result.unchanged,
    }
    try:
        # NetBox Branching routes the targets to the branch and its ChangeDiff rows to main.
        with (
            transaction.atomic(using=DEFAULT_DB_ALIAS),
            transaction.atomic(using=router.db_for_write(DeviceView)),
        ):
            _lock_targets(rows)
            for row, content in zip(rows, contents, strict=True):
                action, device_view = _apply_row(user, source, row, content)
                outcomes[action].append(device_view)
            return result
    except ImportRejected:
        # Drop events queued for the rolled-back records.
        clear_events.send(sender=DeviceView)
        raise


def _prepare_row(user, source, reviewed: ReviewedRow) -> _PreparedContent:
    """Read one reviewed file, check it against the review and validate its content."""
    try:
        source_file = _read_source_file(user, source, reviewed.datafile_id)
    except ValidationError as exc:
        raise ImportRejected(f"{reviewed.path}: {' '.join(exc.messages)}") from exc
    _check_reviewed_file(reviewed, source_file.path, source_file.sha256)
    try:
        return _prepare_content(source_file)
    except ValidationError as exc:
        raise ImportRejected(f"{reviewed.path}: {' '.join(exc.messages)}") from exc


def _apply_row(
    user, source, reviewed: ReviewedRow, content: _PreparedContent
) -> tuple[Action, DeviceView]:
    _check_file_unchanged(user, source, reviewed)
    try:
        proposal = _decide(
            user,
            content,
            reviewed.device_type_id,
            replace=reviewed.action == Action.REPLACE,
        )
    except ValidationError as exc:
        raise ImportRejected(f"{reviewed.path}: {' '.join(exc.messages)}") from exc
    if proposal.fields["render_mode"] != reviewed.render_mode:
        raise ImportRejected(
            f"{reviewed.path}: the render mode differs from the review. {REVIEW_AGAIN}"
        )
    # A replayed review finds the proposed fields already in place.
    if proposal.action == Action.UNCHANGED:
        permission = REPLAY_PERMISSIONS[reviewed.action]
        if not _permits(user, permission, proposal.device_view):
            raise ImportRejected(
                f"{reviewed.path}: {_permission_denied(reviewed.action)}"
            )
        return Action.UNCHANGED, proposal.device_view
    if proposal.baseline != reviewed.baseline:
        raise ImportRejected(f"{reviewed.path}: {STALE_TARGET}")
    return proposal.action, _write(user, reviewed.path, proposal)


def _lock_targets(rows: list[ReviewedRow]) -> None:
    """Lock the target device types, then their device views, each in primary-key order."""
    device_type_ids = {row.device_type_id for row in rows}
    # NO KEY UPDATE serializes imports but lets foreign-key inserts (devices, other views) run.
    list(
        DeviceType.objects.select_for_update(no_key=True)
        .filter(pk__in=device_type_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    list(
        DeviceView.objects.select_for_update()
        .filter(device_type__in=device_type_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def _write(user, path: str, proposal: _Proposal) -> DeviceView:
    """Create or update the device view with native validation, logging and permissions."""
    if proposal.action == Action.CREATE:
        device_view = _new_device_view(proposal.device_type, proposal.fields)
    else:
        device_view = proposal.device_view
        device_view.snapshot()
        for name, value in proposal.fields.items():
            setattr(device_view, name, value)
    try:
        device_view.save()
    except IntegrityError as exc:
        raise ImportRejected(f"{path}: {CONCURRENT_CREATE}") from exc
    if not _permits(user, ACTION_PERMISSIONS[proposal.action], device_view):
        raise ImportRejected(
            f"{path}: the resulting device view violates your permissions."
        )
    return device_view


def _importable_source(user, source_id) -> DataSource:
    source = importable_sources(user).filter(pk=source_id).first()
    if source is None:
        raise ImportRejected("The Data Source is not available for import.")
    return source


def _batch_errors(user, source, selections: list[Selection]) -> list[str]:
    """Check the batch limits in the database, before any file content is read."""
    if len(selections) > MAX_BATCH_ROWS:
        return [TOO_MANY_ROWS]
    datafile_ids = [selection.datafile_id for selection in selections]
    if len(set(datafile_ids)) != len(datafile_ids):
        return [REPEATED_FILE]
    total = (
        DataFile.objects.restrict(user, "view")
        .filter(source=source, pk__in=datafile_ids)
        .aggregate(total=Sum(Length("data")))["total"]
    )
    if total and total > MAX_BATCH_BYTES:
        return [BATCH_TOO_LARGE]
    return []


def _stored_file(user, source, datafile_id, **annotations) -> DataFile:
    """Return the visible DataFile without its content; raise for an unavailable or oversized file."""
    datafile = (
        DataFile.objects.restrict(user, "view")
        .filter(source=source, pk=datafile_id)
        .annotate(stored_bytes=Length("data"), **annotations)
        .only("path")
        .first()
    )
    if datafile is None or _file_format(datafile.path) is None:
        raise ValidationError(FILE_UNAVAILABLE)
    if datafile.stored_bytes > MAX_FILE_BYTES:
        raise ValidationError(FILE_TOO_LARGE)
    return datafile


def _read_source_file(user, source, datafile_id) -> _SourceFile:
    datafile = _stored_file(user, source, datafile_id)
    # The deferred content loads in a second query, and a sync can replace or delete it first.
    try:
        content = bytes(datafile.data)
    except DataFile.DoesNotExist:
        raise ValidationError(FILE_UNAVAILABLE) from None
    if len(content) > MAX_FILE_BYTES:
        raise ValidationError(FILE_TOO_LARGE)
    return _SourceFile(
        path=datafile.path, format=_file_format(datafile.path), content=content
    )


def _check_reviewed_file(reviewed: ReviewedRow, path: str, sha256: str) -> None:
    if path != reviewed.path:
        raise ImportRejected(f"{reviewed.path}: {FILE_UNAVAILABLE}")
    if sha256 != reviewed.sha256:
        raise ImportRejected(f"{reviewed.path}: {FILE_CHANGED}")


def _check_file_unchanged(user, source, reviewed: ReviewedRow) -> None:
    """Check in one query, without loading the content, that the reviewed file is still stored."""
    # The database hashes the stored bytes, so DataFile.hash is not trusted.
    sha256 = Func(
        Func("data", function="sha256"),
        Value("hex"),
        function="encode",
        output_field=CharField(),
    )
    try:
        datafile = _stored_file(
            user,
            source,
            reviewed.datafile_id,
            # An oversized file is rejected without a hash.
            sha256=Case(When(stored_bytes__lte=MAX_FILE_BYTES, then=sha256)),
        )
    except ValidationError as exc:
        raise ImportRejected(f"{reviewed.path}: {' '.join(exc.messages)}") from exc
    _check_reviewed_file(reviewed, datafile.path, datafile.sha256)


def _prepare_content(source_file: _SourceFile) -> _PreparedContent:
    """Decode and validate the content of one file; this needs no target and no query."""
    try:
        text = source_file.content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("The file is not valid UTF-8 text.") from exc
    if not text.strip():
        raise ValidationError("The file is empty.")
    if source_file.format == Format.CSS:
        css = normalize_grid_template_area(CSS_NEWLINES.sub("\n", text))
        # An empty yaml_layout keeps the YAML renderer from overriding this CSS.
        yaml_layout = ""
    else:
        try:
            yaml_layout, css = text, render_css(parse_yaml(text, strict=True))
        except LayoutParseError as exc:
            raise ValidationError(f"Invalid YAML layout: {exc}") from exc
    try:
        validate_css(css)
    except LayoutParseError as exc:
        raise ValidationError(f"Invalid CSS layout: {exc}") from exc
    # The model checks of the content fields run here, so full_clean under the lock skips them.
    other_fields = [
        field.name
        for field in DeviceView._meta.fields
        if field.name not in CONTENT_FIELDS
    ]
    DeviceView(yaml_layout=yaml_layout, grid_template_area=css).clean_fields(
        exclude=other_fields
    )
    return _PreparedContent(
        format=source_file.format, yaml_layout=yaml_layout, grid_template_area=css
    )


def _decide(
    user, content: _PreparedContent, device_type_id, *, replace: bool
) -> _Proposal:
    """Decide what the content does to its current target; raise ValidationError when it cannot."""
    device_type, device_view = _target(user, device_type_id)
    if device_view is not None:
        # A CSS import replaces the stored mode before full_clean, so check it here.
        DeviceView._meta.get_field("render_mode").clean(
            device_view.render_mode, device_view
        )
    render_mode = RenderMode.CSS.value
    # A YAML layout supports every render mode, so a replacement keeps it.
    if content.format == Format.YAML and device_view is not None:
        render_mode = device_view.render_mode
    fields = {
        "yaml_layout": content.yaml_layout,
        "grid_template_area": content.grid_template_area,
        "render_mode": render_mode,
    }
    if device_view is None:
        action = Action.CREATE
        candidate = _new_device_view(device_type, fields)
    else:
        current = _imported_fields(device_view)
        if all(
            _comparable_text(current[name]) == _comparable_text(value)
            for name, value in fields.items()
        ):
            action = Action.UNCHANGED
        else:
            action = Action.REPLACE if replace else Action.SKIP
        candidate = copy.copy(device_view)
        for name, value in fields.items():
            setattr(candidate, name, value)
    candidate.full_clean(exclude=CONTENT_FIELDS, validate_unique=False)
    if action == Action.CREATE and not _has_permission(user, action):
        raise ValidationError(_permission_denied(action))
    if action == Action.REPLACE and not _permits(user, "change", device_view):
        raise ValidationError(_permission_denied(action))
    return _Proposal(
        device_type=device_type,
        device_view=device_view,
        action=action,
        fields=fields,
        baseline=_baseline(device_type, device_view),
    )


def _new_device_view(device_type: DeviceType, fields: dict[str, str]) -> DeviceView:
    """Return an unsaved device view with NetBox's custom field defaults, as a new object gets."""
    return DeviceView(
        device_type=device_type,
        custom_field_data=CustomField.objects.get_defaults_for_model(DeviceView),
        **fields,
    )


def _baseline(device_type: DeviceType, device_view: DeviceView | None) -> str:
    """Return a digest of the target identity, its device view and its imported fields."""
    state = {
        "device_type": [
            device_type.pk,
            device_type.manufacturer.name,
            device_type.model,
        ],
        "device_view": device_view and [device_view.pk, _imported_fields(device_view)],
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def _has_permission(user, action) -> bool:
    return user.has_perm(f"netbox_device_view.{ACTION_PERMISSIONS[action]}_deviceview")


def _permits(user, permission: str, device_view: DeviceView) -> bool:
    """Return True when the user's object permission constraints match the stored record."""
    return (
        DeviceView.objects.restrict(user, permission).filter(pk=device_view.pk).exists()
    )


def _permission_denied(action) -> str:
    return (
        f"You do not have permission to {REPLAY_PERMISSIONS[action]} this device view."
    )


def _imported_fields(device_view: DeviceView) -> dict[str, str]:
    return {name: getattr(device_view, name) for name in IMPORTED_FIELDS}


def _comparable_text(value: str) -> str:
    """Return the text without the changes that a save through the edit form makes."""
    return _FORM_NEWLINES.sub("\n", value).strip()


def _diff_lines(current: str, proposed: str) -> list[tuple[str, str]] | None:
    """Return (kind, text) lines of a unified diff, or None for a field that is too large."""
    kinds = {"+": "add", "-": "remove", "@": "hunk"}
    # Split the compared text, so that differing fields always give a diff.
    current, proposed = _comparable_text(current), _comparable_text(proposed)
    if len(current) + len(proposed) > MAX_DIFF_CHARS:
        return None
    current_lines = current.split("\n")
    proposed_lines = proposed.split("\n")
    if len(current_lines) + len(proposed_lines) > MAX_DIFF_LINES:
        return None
    # The template escapes the text.
    lines = difflib.unified_diff(
        current_lines,
        proposed_lines,
        "current",
        "proposed",
        lineterm="",
    )
    # The first two lines name the two sides; they are not changes.
    return [
        ("hunk" if index < 2 else kinds.get(line[:1], "context"), line)
        for index, line in enumerate(lines)
    ]


def _file_format(path: str) -> Format | None:
    # Same rule as the catalog's path__endswith filter, so "Cisco/.yaml" is YAML too.
    for suffix, file_format in FORMAT_SUFFIXES.items():
        if path.endswith(suffix):
            return file_format
    return None


def _match_paths(user, paths) -> dict[str, DeviceType]:
    """Map each "<Manufacturer>/<stem>.<ext>" path to the visible device type it names exactly.

    The stem names a device type by its model, else by a part number of one device type only.
    """
    keys = {}
    for path in paths:
        parts = PurePosixPath(path).parts
        if len(parts) >= 2:
            keys[path] = (parts[-2], PurePosixPath(parts[-1]).stem)
    if not keys:
        return {}
    stems = {stem for _, stem in keys.values()}
    device_types = (
        permitted_device_types(user)
        .filter(
            Q(model__in=stems) | Q(part_number__in=stems),
            manufacturer__name__in={manufacturer for manufacturer, _ in keys.values()},
        )
        .select_related("manufacturer")
    )
    by_model = {}
    by_part_number = defaultdict(list)
    for device_type in device_types:
        manufacturer = device_type.manufacturer.name
        by_model[manufacturer, device_type.model] = device_type
        if device_type.part_number:
            by_part_number[manufacturer, device_type.part_number].append(device_type)
    matches = {}
    for path, key in keys.items():
        if key in by_model:
            matches[path] = by_model[key]
        elif len(by_part_number.get(key, ())) == 1:
            matches[path] = by_part_number[key][0]
    return matches


def _target(user, device_type_id) -> tuple[DeviceType, DeviceView | None]:
    """Return a permitted device type and its visible device view, if it has one."""
    device_type = (
        permitted_device_types(user)
        .select_related("manufacturer")
        .filter(pk=device_type_id)
        .first()
    )
    if device_type is None:
        raise ValidationError(TARGET_UNAVAILABLE)
    device_view = DeviceView.objects.filter(device_type=device_type).first()
    # A hidden device view reads the same as any other unavailable target.
    if device_view is not None and not _permits(user, "view", device_view):
        raise ValidationError(TARGET_UNAVAILABLE)
    return device_type, device_view
