"""Athena results cache (docs/features/api-caching.md, criteria 5-11)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import handler
import pytest
import vitals_sql
from conftest import REGISTRY_TABLE
from events import NOW, WEARABLE, YESTERDAY, event, put_device, put_thresholds

SGT = timezone(timedelta(hours=8))
FAR = time.monotonic() + 3600
DAY_COLUMNS = ["date", *vitals_sql.SUMMARY_COLUMNS, *vitals_sql.THRESHOLD_COLUMNS]
HOURLY = {"interval": "hour", "from": YESTERDAY, "to": YESTERDAY}


def sgt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=SGT)


def call(ev, now=NOW, deadline=FAR):
    result = handler.handle(ev, now, deadline)
    return result["statusCode"], json.loads(result["body"])


def daily(start: str = "2026-11-17") -> dict:
    return event(params={"interval": "day", "from": start, "to": YESTERDAY})


@pytest.fixture
def athena(aws):
    _, fake = aws
    fake.columns = DAY_COLUMNS
    fake.rows = []
    return fake


def test_second_request_is_a_hit_with_the_same_body(athena, logs):
    first = call(daily())
    second = call(daily())
    assert len(athena.queries) == 1
    assert first == second
    oks = [r for r in logs() if r.get("outcome") == "ok"]
    assert [r["cache"] for r in oks] == ["miss", "hit"]


def test_a_different_range_is_a_different_key(athena):
    call(daily("2026-11-17"))
    call(daily("2026-11-16"))
    assert len(athena.queries) == 2


@pytest.mark.parametrize(
    ("stored", "expires"),
    [
        (sgt(19, 10), sgt(20, 9, 30)),
        (sgt(19, 9), sgt(19, 9, 30)),
        (sgt(19, 9, 30), sgt(20, 9, 30)),
        (sgt(19, 0, 5), sgt(19, 9, 30)),
    ],
)
def test_expiry_is_the_next_0930_sgt(stored, expires):
    assert handler.cache_expiry(stored.astimezone(handler.UTC)) == expires


def test_entry_is_served_until_0929_and_not_at_0930(athena):
    call(daily(), now=sgt(19, 10))
    call(daily(), now=sgt(20, 9, 29))
    assert len(athena.queries) == 1
    call(daily(), now=sgt(20, 9, 30))
    assert len(athena.queries) == 2


def test_failed_query_is_not_cached(athena):
    athena.state = "FAILED"
    with pytest.raises(handler.QueryFailedError):
        call(daily())
    athena.state = "SUCCEEDED"
    call(daily())
    call(daily())
    assert len(athena.queries) == 2


def test_timed_out_query_is_not_cached(athena):
    athena.running_polls = 10**6
    with pytest.raises(handler.QueryTimeoutError):
        call(daily(), deadline=0)
    athena.running_polls = 0
    call(daily())
    assert len(athena.queries) == 2


def test_oldest_entry_is_dropped_when_full(athena, monkeypatch):
    monkeypatch.setattr(handler, "CACHE_MAX_ENTRIES", 2)
    for start in ("2026-11-15", "2026-11-16", "2026-11-17"):
        call(daily(start))
    assert len(handler._results) == 2
    call(daily("2026-11-17"))  # newest: still cached
    assert len(athena.queries) == 3
    call(daily("2026-11-15"))  # oldest: dropped
    assert len(athena.queries) == 4


def test_hourly_reads_thresholds_every_time(aws):
    client, fake = aws
    put_device(client, REGISTRY_TABLE, WEARABLE)
    fake.columns = ["hour_utc", *vitals_sql.SUMMARY_COLUMNS]
    _, first = call(event(params=HOURLY))
    put_thresholds(client, REGISTRY_TABLE)  # a doctor changes the limits
    _, second = call(event(params=HOURLY))
    assert first["thresholds"] != second["thresholds"]
    # The limits are part of the query text, so the change is a new key.
    assert len(fake.queries) == 2
    call(event(params=HOURLY))
    assert len(fake.queries) == 2


def test_hourly_without_devices_caches_nothing(aws):
    call(event(params=HOURLY))
    assert handler._results == {}
