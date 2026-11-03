"""Thresholds: defaults, per-resident resolution, strict boundaries, and the cache
(docs/features/vitals-alerts.md, criteria 5-6 and 29). Pure, with a fake client."""

from __future__ import annotations

from decimal import Decimal

import pytest
import thresholds
from thresholds import DEFAULTS, Limits, direction, limits_for_resident

TABLE = "silvercare-registry"
RESIDENT = "res-0002"


class FakeRegistry:
    """Records get_item calls and returns one fixed (low-level) item or none."""

    def __init__(self, item: dict | None = None):
        self.item = item
        self.calls: list[dict] = []

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        return {"Item": self.item} if self.item is not None else {}


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _clear_cache():
    thresholds.clear_cache()
    yield
    thresholds.clear_cache()


def n(value) -> dict:
    return {"N": str(value)}


def item(**metrics) -> dict:
    """A THRESHOLDS item in low-level format; values are dicts of name -> number."""
    out = {"pk": {"S": f"THRESHOLDS#{RESIDENT}"}, "sk": {"S": "THRESHOLDS"}}
    for metric, limits in metrics.items():
        if isinstance(limits, dict):
            out[metric] = {"M": {k: n(v) for k, v in limits.items()}}
        else:
            out[metric] = limits
    return out


# ---- defaults (decisions table) ----


def test_defaults_are_the_decided_limits():
    assert DEFAULTS == {
        "hr_bpm": Limits(min=Decimal(50), max=Decimal(120)),
        "spo2_pct": Limits(min=Decimal(92), max=None),
        "skin_temp_c": Limits(min=Decimal("30.0"), max=Decimal("38.0")),
    }


# ---- strict boundaries (criterion 6) ----


@pytest.mark.parametrize(
    ("metric", "value", "expected"),
    [
        ("hr_bpm", 49, "below"),
        ("hr_bpm", 50, None),
        ("hr_bpm", 51, None),
        ("hr_bpm", 119, None),
        ("hr_bpm", 120, None),
        ("hr_bpm", 121, "above"),
        ("spo2_pct", 91, "below"),
        ("spo2_pct", 92, None),
        ("spo2_pct", 93, None),
        ("spo2_pct", 100, None),
        ("skin_temp_c", Decimal("29.9"), "below"),
        ("skin_temp_c", Decimal("30.0"), None),
        ("skin_temp_c", Decimal("30.1"), None),
        ("skin_temp_c", Decimal("37.9"), None),
        ("skin_temp_c", Decimal("38.0"), None),
        ("skin_temp_c", Decimal("38.1"), "above"),
        ("skin_temp_c", Decimal(30), None),
        ("skin_temp_c", Decimal(38), None),
    ],
)
def test_value_is_abnormal_only_strictly_past_a_limit(metric, value, expected):
    assert direction(value, DEFAULTS[metric]) == expected


@pytest.mark.parametrize("metric", ["hr_bpm", "spo2_pct", "skin_temp_c"])
def test_null_is_never_abnormal(metric):
    assert direction(None, DEFAULTS[metric]) is None


# ---- resolution from the item (criterion 5) ----


def test_no_item_gives_the_defaults():
    client = FakeRegistry(None)

    assert limits_for_resident(client, TABLE, RESIDENT) == DEFAULTS


def test_lookup_reads_the_thresholds_key_only():
    client = FakeRegistry(None)

    limits_for_resident(client, TABLE, RESIDENT)

    (call,) = client.calls
    assert call["TableName"] == TABLE
    assert call["Key"] == {
        "pk": {"S": f"THRESHOLDS#{RESIDENT}"},
        "sk": {"S": "THRESHOLDS"},
    }


def test_full_item_replaces_every_limit():
    client = FakeRegistry(
        item(
            hr_bpm={"min": 45, "max": 110},
            spo2_pct={"min": 88},
            skin_temp_c={"min": "31.5", "max": "37.5"},
        )
    )

    limits = limits_for_resident(client, TABLE, RESIDENT)

    assert limits == {
        "hr_bpm": Limits(min=Decimal(45), max=Decimal(110)),
        "spo2_pct": Limits(min=Decimal(88), max=None),
        "skin_temp_c": Limits(min=Decimal("31.5"), max=Decimal("37.5")),
    }


def test_partial_item_keeps_defaults_for_the_rest():
    # The seed script's res-0002: only a lower SpO2 min.
    client = FakeRegistry(item(spo2_pct={"min": 88}))

    limits = limits_for_resident(client, TABLE, RESIDENT)

    assert limits["spo2_pct"] == Limits(min=Decimal(88), max=None)
    assert limits["hr_bpm"] == DEFAULTS["hr_bpm"]
    assert limits["skin_temp_c"] == DEFAULTS["skin_temp_c"]


def test_partial_metric_keeps_the_default_for_its_other_limit():
    client = FakeRegistry(item(hr_bpm={"max": 110}))

    limits = limits_for_resident(client, TABLE, RESIDENT)

    assert limits["hr_bpm"] == Limits(min=Decimal(50), max=Decimal(110))


def test_personal_spo2_min_makes_89_normal():
    client = FakeRegistry(item(spo2_pct={"min": 88}))

    limits = limits_for_resident(client, TABLE, RESIDENT)

    assert direction(89, limits["spo2_pct"]) is None
    assert direction(87, limits["spo2_pct"]) == "below"


@pytest.mark.parametrize(
    ("field", "attribute"),
    [
        ("hr_bpm.max", n(300)),
        ("hr_bpm.min", n(19)),
        ("hr_bpm.max", n("110.5")),
        ("hr_bpm.max", {"S": "high"}),
        ("hr_bpm.max", {"BOOL": True}),
        ("hr_bpm.max", {"NULL": True}),
        ("spo2_pct.min", n(49)),
        ("spo2_pct.min", n(101)),
        ("skin_temp_c.max", n("45.1")),
        ("skin_temp_c.min", n("19.9")),
    ],
)
def test_invalid_limit_keeps_its_default_and_is_reported_by_field_name(
    field, attribute
):
    metric, name = field.split(".")
    built = item()
    built[metric] = {"M": {name: attribute}}
    reported: list = []
    client = FakeRegistry(built)

    limits = limits_for_resident(
        client, TABLE, RESIDENT, on_invalid=lambda r, f: reported.append((r, f))
    )

    assert limits[metric] == DEFAULTS[metric]
    assert reported == [(RESIDENT, [field])]


def test_metric_that_is_not_a_map_is_invalid():
    built = item()
    built["hr_bpm"] = {"N": "110"}
    reported: list = []

    limits = limits_for_resident(
        FakeRegistry(built),
        TABLE,
        RESIDENT,
        on_invalid=lambda r, f: reported.append(f),
    )

    assert limits["hr_bpm"] == DEFAULTS["hr_bpm"]
    assert reported == [["hr_bpm"]]


def test_spo2_max_is_ignored():
    # SpO2 has no upper limit; an extra max is not a limit and not reported.
    reported: list = []

    limits = limits_for_resident(
        FakeRegistry(item(spo2_pct={"min": 90, "max": 99})),
        TABLE,
        RESIDENT,
        on_invalid=lambda r, f: reported.append(f),
    )

    assert limits["spo2_pct"] == Limits(min=Decimal(90), max=None)
    assert reported == []


@pytest.mark.parametrize(
    "hr", [{"min": 110, "max": 110}, {"min": 115, "max": 100}, {"min": 125}]
)
def test_min_not_below_max_falls_back_to_both_defaults(hr):
    # {"min": 125} alone: 125 is not below the default max 120.
    reported: list = []

    limits = limits_for_resident(
        FakeRegistry(item(hr_bpm=hr)),
        TABLE,
        RESIDENT,
        on_invalid=lambda r, f: reported.append(f),
    )

    assert limits["hr_bpm"] == DEFAULTS["hr_bpm"]
    assert reported == [["hr_bpm"]]


def test_invalid_report_has_field_names_not_values():
    reported: list = []

    limits_for_resident(
        FakeRegistry(item(hr_bpm={"max": 247.5}, spo2_pct={"min": 7})),
        TABLE,
        RESIDENT,
        on_invalid=lambda r, f: reported.append((r, f)),
    )

    ((resident, fields),) = reported
    assert resident == RESIDENT
    assert fields == ["hr_bpm.max", "spo2_pct.min"]
    assert "247" not in str(reported)
    assert "7" not in "".join(fields)


def test_valid_item_reports_nothing():
    reported: list = []

    limits_for_resident(
        FakeRegistry(item(spo2_pct={"min": 88})),
        TABLE,
        RESIDENT,
        on_invalid=lambda r, f: reported.append(f),
    )

    assert reported == []


def test_unassigned_gets_defaults_without_a_lookup():
    client = FakeRegistry(item(hr_bpm={"max": 100}))

    assert limits_for_resident(client, TABLE, "unassigned") == DEFAULTS
    assert client.calls == []


# ---- cache (criterion 5) ----


def test_result_is_cached_for_five_minutes():
    client = FakeRegistry(item(spo2_pct={"min": 88}))
    clock = Clock()

    limits_for_resident(client, TABLE, RESIDENT, clock=clock)
    clock.now += 299.9
    limits_for_resident(client, TABLE, RESIDENT, clock=clock)

    assert len(client.calls) == 1


def test_cache_expires_after_five_minutes():
    client = FakeRegistry(item(spo2_pct={"min": 88}))
    clock = Clock()

    limits_for_resident(client, TABLE, RESIDENT, clock=clock)
    clock.now += 300.0
    limits_for_resident(client, TABLE, RESIDENT, clock=clock)

    assert len(client.calls) == 2


def test_no_item_is_cached_too():
    client = FakeRegistry(None)
    clock = Clock()

    limits_for_resident(client, TABLE, RESIDENT, clock=clock)
    limits_for_resident(client, TABLE, RESIDENT, clock=clock)

    assert len(client.calls) == 1


def test_cache_is_per_resident():
    client = FakeRegistry(None)

    limits_for_resident(client, TABLE, "res-0001")
    limits_for_resident(client, TABLE, "res-0002")

    assert len(client.calls) == 2


def test_invalid_limits_are_reported_on_lookup_not_on_cache_hits():
    reported: list = []
    client = FakeRegistry(item(hr_bpm={"max": 300}))

    for _ in range(3):
        limits_for_resident(
            client, TABLE, RESIDENT, on_invalid=lambda r, f: reported.append(f)
        )

    assert len(reported) == 1


def test_cached_result_cannot_be_changed_by_the_caller():
    client = FakeRegistry(None)
    first = limits_for_resident(client, TABLE, RESIDENT)
    first["hr_bpm"] = Limits(min=None, max=None)

    assert limits_for_resident(client, TABLE, RESIDENT) == DEFAULTS


def test_aws_error_is_raised_and_not_cached():
    from botocore.exceptions import ClientError

    class Failing(FakeRegistry):
        def get_item(self, **kwargs):
            self.calls.append(kwargs)
            raise ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "x"}}, "GetItem"
            )

    client = Failing()
    with pytest.raises(ClientError):
        limits_for_resident(client, TABLE, RESIDENT)
    with pytest.raises(ClientError):
        limits_for_resident(client, TABLE, RESIDENT)

    assert len(client.calls) == 2
