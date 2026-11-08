"""GET /nightly/overview (docs/features/nightly-overview.md, criteria 1-13 and 27): the
group check before any read, the flag rule, tier order and every tie-break, counts that
add up, residents with no item, invalid registry `pk`s, `available` / `not_ready`
around 09:30 SGT, registry paging, BatchGetItem chunking and UnprocessedKeys, 500s, and
logs without resident IDs, values, or thresholds.

All IDs and values are synthetic and fixed (no UUIDs), so log checks can't match by
chance.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import handler
import pytest
from botocore.exceptions import ClientError
from events import (
    BEFORE_READY,
    CLEAR_COUNTS,
    EXPECTED_THRESHOLDS,
    NOW,
    REQUEST_ID,
    SUB,
    YESTERDAY,
    night_item,
    overview_event,
    put_counts,
    put_raw,
    put_resident,
    sgt,
)

FLAG_FIELDS = (
    "falls",
    "vitals_alert_episodes",
    "hr_bpm_above",
    "hr_bpm_below",
    "spo2_pct_below",
    "skin_temp_c_above",
    "skin_temp_c_below",
)
ENTRY_KEYS = {
    "resident_id",
    *FLAG_FIELDS,
    "out_of_limit",
    "thresholds",
}


def body(resp: dict) -> dict:
    return json.loads(resp["body"])


def call(event: dict | None = None, now: datetime = NOW) -> dict:
    return handler.handle_overview(event or overview_event(), now)


def rid(n: int) -> str:
    return f"res-{n:04d}"


def item(resident_id: str, **counts: Any) -> dict[str, Any]:
    """A deserialized Results item as the BatchGetItem projection returns it."""
    values = {k: (Decimal(v) if isinstance(v, int) else v) for k, v in counts.items()}
    full = night_item(resident_id, **{**CLEAR_COUNTS, **values})
    summary = {k: full["summary"][k] for k in (*FLAG_FIELDS, "thresholds")}
    return {"pk": full["pk"], "summary": summary}


def overview(residents: list[str], items: list[dict], now: datetime = NOW) -> dict:
    out, _, _ = handler.build_overview(residents, items, now)
    return out


def order(items: list[dict]) -> list[str]:
    residents = [i["pk"].removeprefix("RESIDENT#") for i in items]
    return [e["resident_id"] for e in overview(residents, items)["flagged"]]


def _throttled(operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "synthetic detail qx-5521",
            }
        },
        operation,
    )


def residents(aws, *ids: str) -> None:
    for i, resident_id in enumerate(ids):
        put_resident(aws.ddb, resident_id, f"Test Resident {chr(65 + i % 26)}")


# ---- criteria 1, 10: valid request ----


def test_doctor_gets_the_spec_example(aws):
    residents(aws, rid(3), rid(4), rid(5))
    put_counts(aws.ddb, rid(3), falls=1, vitals_alert_episodes=1, hr_bpm_above=12)
    put_counts(aws.ddb, rid(4))
    resp = call()
    assert resp["statusCode"] == 200
    assert body(resp) == {
        "expected_date": YESTERDAY,
        "available": True,
        "not_ready": False,
        "flagged": [
            {
                "resident_id": rid(3),
                "falls": 1,
                "vitals_alert_episodes": 1,
                "hr_bpm_above": 12,
                "hr_bpm_below": 0,
                "spo2_pct_below": 0,
                "skin_temp_c_above": 0,
                "skin_temp_c_below": 0,
                "out_of_limit": 12,
                "thresholds": EXPECTED_THRESHOLDS,
            }
        ],
        "clear_count": 1,
        "no_summary_count": 1,
    }


def test_headers_are_json_and_no_store(aws):
    resp = call()
    assert resp["headers"] == {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
    }


def test_flagged_entry_holds_only_the_spec_fields(aws):
    residents(aws, rid(3))
    put_counts(aws.ddb, rid(3), hr_bpm_below=2)
    (entry,) = body(call())["flagged"]
    assert set(entry) == ENTRY_KEYS
    text = json.dumps(body(call()))
    for name in ("hr_bpm_avg", "bed_minutes", "cancels", "loaded_at", "display_name"):
        assert name not in text, name


def test_thresholds_are_the_nights_own_with_json_types(aws):
    residents(aws, rid(3))
    put_counts(
        aws.ddb,
        rid(3),
        spo2_pct_below=1,
        thresholds={
            "hr_bpm": {"min": Decimal(45), "max": Decimal(125)},
            "spo2_pct": {"min": Decimal(90)},
            "skin_temp_c": {"min": Decimal("29.5"), "max": Decimal("37.5")},
        },
    )
    (entry,) = body(call())["flagged"]
    assert entry["thresholds"] == {
        "hr_bpm": {"min": 45, "max": 125},
        "spo2_pct": {"min": 90},
        "skin_temp_c": {"min": 29.5, "max": 37.5},
    }
    assert type(entry["thresholds"]["hr_bpm"]["max"]) is int
    assert type(entry["thresholds"]["skin_temp_c"]["min"]) is float
    assert type(entry["falls"]) is int


def test_doctor_and_nurse_is_allowed(aws):
    assert call(overview_event(groups=("nurse", "doctor")))["statusCode"] == 200


@pytest.mark.parametrize("groups", ["doctor", "[doctor]", "[nurse doctor]"])
def test_groups_as_the_authorizers_string_form(aws, groups):
    assert call(overview_event(groups=groups))["statusCode"] == 200


def test_query_string_is_ignored(aws):
    residents(aws, rid(3))
    put_counts(aws.ddb, rid(3), falls=1)
    plain = body(call())
    with_query = body(
        call(overview_event(query={"date": "2026-11-01", "resident_id": rid(9)}))
    )
    assert with_query == plain


# ---- criteria 2, 13: 403 before any read ----


@pytest.mark.parametrize(
    "groups", [(), ("nurse",), ("caregiver",), ("ops",), ("family",), ("Doctor",)]
)
def test_non_doctors_get_403_without_a_read(no_aws, groups):
    resp = call(overview_event(groups=groups))
    assert resp["statusCode"] == 403
    assert body(resp) == {
        "error": {
            "code": "forbidden",
            "message": "Only doctors can see the nightly overview.",
        }
    }
    assert resp["headers"]["Cache-Control"] == "no-store"


def test_missing_authorizer_gets_403_without_a_read(no_aws):
    event = overview_event()
    del event["requestContext"]["authorizer"]
    assert call(event)["statusCode"] == 403


def test_groups_claim_of_the_wrong_type_gets_403(no_aws):
    assert call(overview_event(groups={"doctor": True}))["statusCode"] == 403


def test_403_makes_no_dynamodb_call(aws):
    call(overview_event(groups=("nurse",)))
    assert aws.calls == []


# ---- criterion 7: flag rule ----


@pytest.mark.parametrize("field", FLAG_FIELDS)
def test_each_count_alone_flags_the_night(field):
    entry = handler.overview_entry(item(rid(3), **{field: 1}))[1]
    assert handler.is_flagged(entry) is True


def test_all_counts_zero_is_not_flagged():
    entry = handler.overview_entry(item(rid(3)))[1]
    assert handler.is_flagged(entry) is False


def test_null_counts_count_as_zero():
    entry = handler.overview_entry(item(rid(3), **dict.fromkeys(FLAG_FIELDS)))[1]
    assert handler.is_flagged(entry) is False
    assert all(entry[f] == 0 for f in FLAG_FIELDS)


def test_missing_counts_count_as_zero():
    raw = item(rid(3))
    raw["summary"] = {"thresholds": raw["summary"]["thresholds"]}
    entry = handler.overview_entry(raw)[1]
    assert handler.is_flagged(entry) is False
    assert entry["out_of_limit"] == 0


def test_null_count_beside_a_positive_one_still_flags():
    entry = handler.overview_entry(item(rid(3), falls=None, spo2_pct_below=2))[1]
    assert handler.is_flagged(entry) is True
    assert entry["falls"] == 0


def test_out_of_limit_is_the_sum_of_the_five_counts():
    entry = handler.overview_entry(
        item(
            rid(3),
            falls=7,
            vitals_alert_episodes=5,
            hr_bpm_above=1,
            hr_bpm_below=2,
            spo2_pct_below=3,
            skin_temp_c_above=4,
            skin_temp_c_below=6,
        )
    )[1]
    assert entry["out_of_limit"] == 16


def test_null_counts_in_the_stored_item_are_zero_end_to_end(aws):
    residents(aws, rid(3), rid(4))
    put_counts(aws.ddb, rid(3), falls=None, hr_bpm_above=None)
    put_counts(aws.ddb, rid(4), falls=None, skin_temp_c_below=3)
    payload = body(call())
    assert [e["resident_id"] for e in payload["flagged"]] == [rid(4)]
    assert payload["flagged"][0]["falls"] == 0
    assert payload["clear_count"] == 1


# ---- criterion 9: order ----


def test_falls_tier_comes_before_alert_episodes_and_out_of_limit():
    items = [
        item(rid(1), hr_bpm_above=99),
        item(rid(2), vitals_alert_episodes=9, hr_bpm_above=50),
        item(rid(3), falls=1),
    ]
    assert order(items) == [rid(3), rid(2), rid(1)]


def test_alert_episode_tier_comes_before_out_of_limit_only():
    items = [item(rid(1), spo2_pct_below=80), item(rid(2), vitals_alert_episodes=1)]
    assert order(items) == [rid(2), rid(1)]


def test_falls_tier_sorts_by_falls_descending():
    items = [item(rid(1), falls=1, vitals_alert_episodes=5), item(rid(2), falls=2)]
    assert order(items) == [rid(2), rid(1)]


def test_falls_tier_ties_on_falls_break_on_alert_episodes():
    items = [
        item(rid(1), falls=1, vitals_alert_episodes=1, hr_bpm_above=30),
        item(rid(2), falls=1, vitals_alert_episodes=2),
    ]
    assert order(items) == [rid(2), rid(1)]


def test_falls_tier_ties_on_episodes_break_on_out_of_limit():
    items = [
        item(rid(1), falls=1, vitals_alert_episodes=1, hr_bpm_above=2),
        item(rid(2), falls=1, vitals_alert_episodes=1, hr_bpm_above=1, hr_bpm_below=2),
    ]
    assert order(items) == [rid(2), rid(1)]


def test_falls_tier_full_tie_breaks_on_resident_id_ascending():
    items = [
        item(rid(9), falls=1, hr_bpm_above=2),
        item(rid(2), falls=1, spo2_pct_below=2),
    ]
    assert order(items) == [rid(2), rid(9)]


def test_alert_episode_tier_sorts_by_episodes_descending():
    items = [
        item(rid(1), vitals_alert_episodes=1, hr_bpm_above=40),
        item(rid(2), vitals_alert_episodes=3),
    ]
    assert order(items) == [rid(2), rid(1)]


def test_alert_episode_tier_ties_break_on_out_of_limit():
    items = [
        item(rid(1), vitals_alert_episodes=2),
        item(rid(2), vitals_alert_episodes=2, skin_temp_c_above=1),
    ]
    assert order(items) == [rid(2), rid(1)]


def test_alert_episode_tier_full_tie_breaks_on_resident_id_ascending():
    items = [
        item(rid(7), vitals_alert_episodes=2),
        item(rid(4), vitals_alert_episodes=2),
    ]
    assert order(items) == [rid(4), rid(7)]


def test_out_of_limit_tier_sorts_by_the_sum_descending():
    items = [
        item(rid(1), hr_bpm_above=5),
        item(rid(2), hr_bpm_below=2, spo2_pct_below=2, skin_temp_c_below=2),
    ]
    assert order(items) == [rid(2), rid(1)]


def test_out_of_limit_tier_full_tie_breaks_on_resident_id_ascending():
    items = [item(rid(12), hr_bpm_above=3), item(rid(11), skin_temp_c_below=3)]
    assert order(items) == [rid(11), rid(12)]


def test_response_keeps_the_order_end_to_end(aws):
    residents(aws, rid(1), rid(2), rid(3), rid(4))
    put_counts(aws.ddb, rid(1), hr_bpm_below=2)
    put_counts(aws.ddb, rid(2), vitals_alert_episodes=2, spo2_pct_below=3)
    put_counts(aws.ddb, rid(3), falls=1, vitals_alert_episodes=1, hr_bpm_above=12)
    put_counts(aws.ddb, rid(4), hr_bpm_above=40, skin_temp_c_below=5)
    flagged = body(call())["flagged"]
    assert [e["resident_id"] for e in flagged] == [rid(3), rid(2), rid(4), rid(1)]


# ---- criterion 8: counts ----


def test_counts_add_up_to_the_number_of_residents(aws):
    ids = [rid(n) for n in range(1, 21)]
    residents(aws, *ids)
    for n in range(1, 5):
        put_counts(aws.ddb, rid(n), hr_bpm_above=n)
    for n in range(5, 19):
        put_counts(aws.ddb, rid(n))
    payload = body(call())
    assert len(payload["flagged"]) == 4
    assert payload["clear_count"] == 14
    assert payload["no_summary_count"] == 2
    assert len(payload["flagged"]) + payload["clear_count"] + payload[
        "no_summary_count"
    ] == len(ids)


def test_resident_with_no_item_counts_as_no_summary(aws):
    residents(aws, rid(3), rid(4))
    put_counts(aws.ddb, rid(3))
    payload = body(call())
    assert payload["clear_count"] == 1
    assert payload["no_summary_count"] == 1


def test_only_last_nights_item_counts(aws):
    residents(aws, rid(3), rid(4))
    put_counts(aws.ddb, rid(3), falls=1)
    put_counts(aws.ddb, rid(4), date="2026-11-17", falls=2)
    payload = body(call())
    assert [e["resident_id"] for e in payload["flagged"]] == [rid(3)]
    assert payload["no_summary_count"] == 1


def test_item_for_a_resident_not_in_the_registry_is_ignored(aws):
    residents(aws, rid(3))
    put_counts(aws.ddb, rid(3))
    put_counts(aws.ddb, rid(8), falls=1)
    payload = body(call())
    assert payload["flagged"] == []
    assert payload["clear_count"] == 1
    assert payload["no_summary_count"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"falls": "synthetic"},
        {"falls": Decimal(-1)},
        {"hr_bpm_above": Decimal("1.5")},
        {"vitals_alert_episodes": ["synthetic"]},
    ],
)
def test_unreadable_item_counts_as_no_summary_and_is_logged_as_a_count(aws, logs, bad):
    residents(aws, rid(3), rid(4))
    put_counts(aws.ddb, rid(3), **bad)
    put_counts(aws.ddb, rid(4), hr_bpm_above=2)
    resp = call()
    assert resp["statusCode"] == 200
    payload = body(resp)
    assert [e["resident_id"] for e in payload["flagged"]] == [rid(4)]
    assert payload["no_summary_count"] == 1
    (record,) = [r for r in logs() if r.get("outcome") == "invalid_items"]
    assert record["items"] == 1
    assert "res-" not in json.dumps(record)


@pytest.mark.parametrize(
    "bad_thresholds",
    [
        None,
        "synthetic",
        {"hr_bpm": "synthetic", "spo2_pct": {"min": Decimal(92)}, "skin_temp_c": {}},
        {"hr_bpm": {"min": Decimal(50), "max": Decimal(120), "extra": Decimal(1)}},
    ],
)
def test_unreadable_thresholds_still_flag_the_night_with_null_limits(
    aws, logs, bad_thresholds
):
    # User's decision 2026-11-08 (option b): a fall must not drop off the list because
    # the night's stored limits can't be read.
    residents(aws, rid(3), rid(4))
    put_counts(aws.ddb, rid(3), falls=1, hr_bpm_above=12, thresholds=bad_thresholds)
    put_counts(aws.ddb, rid(4), thresholds=bad_thresholds)
    resp = call()
    assert resp["statusCode"] == 200
    payload = body(resp)
    (entry,) = payload["flagged"]
    assert entry["resident_id"] == rid(3)
    assert entry["falls"] == 1
    assert entry["hr_bpm_above"] == 12
    assert entry["thresholds"] is None
    assert payload["clear_count"] == 1
    assert payload["no_summary_count"] == 0
    assert not [r for r in logs() if r.get("outcome") == "invalid_items"]
    (record,) = [r for r in logs() if r.get("outcome") == "unreadable_thresholds"]
    assert record["items"] == 2
    assert "res-" not in json.dumps(record)


def test_readable_thresholds_are_never_null():
    entry = handler.overview_entry(item(rid(3), falls=1))[1]
    assert entry["thresholds"] is not None


# ---- criterion 4: residents from the registry ----


def test_invalid_registry_pk_is_skipped_and_logged_as_a_count(aws, logs):
    residents(aws, rid(3))
    for pk in ("RESIDENT#res-12345", "RESIDENT#synthetic-note", "RESIDENT#"):
        put_raw(
            aws.ddb,
            {"pk": pk, "sk": "PROFILE", "entity_type": "RESIDENT"},
            handler.os.environ["REGISTRY_TABLE"],
        )
    put_counts(aws.ddb, rid(3))
    payload = body(call())
    assert payload["clear_count"] == 1
    assert payload["no_summary_count"] == 0
    (record,) = [r for r in logs() if r.get("outcome") == "invalid_resident_pks"]
    assert record["skipped"] == 3
    text = json.dumps(logs())
    assert "synthetic-note" not in text
    assert "12345" not in text


def test_residents_from_pks_skips_non_strings_and_dedupes():
    ids, skipped = handler.residents_from_pks(
        ["RESIDENT#res-0004", "RESIDENT#res-0003", "RESIDENT#res-0003", None, 7]
    )
    assert ids == [rid(3), rid(4)]
    assert skipped == 2


def test_devices_and_other_entity_types_are_not_residents(aws):
    residents(aws, rid(3))
    put_raw(
        aws.ddb,
        {"pk": "DEVICE#device-test-01", "sk": "ASSIGNMENT", "entity_type": "DEVICE"},
        handler.os.environ["REGISTRY_TABLE"],
    )
    put_counts(aws.ddb, rid(3))
    payload = body(call())
    (batch,) = aws.batch_gets()
    (table,) = batch["RequestItems"].values()
    assert [k["pk"]["S"] for k in table["Keys"]] == ["RESIDENT#res-0003"]
    assert payload["clear_count"] == 1
    assert payload["no_summary_count"] == 0


def test_registry_query_shape(aws):
    residents(aws, rid(3))
    call()
    (query,) = aws.queries()
    assert query["TableName"] == handler.os.environ["REGISTRY_TABLE"]
    assert query["IndexName"] == "by-type"
    assert query["Select"] == "SPECIFIC_ATTRIBUTES"
    assert query["ConsistentRead"] is False
    names = query["ExpressionAttributeNames"]
    projected = [names[p.strip()] for p in query["ProjectionExpression"].split(",")]
    assert projected == ["pk"]
    assert set(names.values()) <= {"pk", "entity_type"}
    assert query["ExpressionAttributeValues"] == {":t": {"S": "RESIDENT"}}
    key = query["KeyConditionExpression"]
    for placeholder, name in names.items():
        key = key.replace(placeholder, name)
    assert key == "entity_type = :t"


def test_registry_query_follows_last_evaluated_key(aws):
    ids = [rid(n) for n in range(1, 8)]
    residents(aws, *ids)
    for resident_id in ids:
        put_counts(aws.ddb, resident_id)
    aws.client.page_limit = 2
    payload = body(call())
    assert len(aws.queries()) >= 4
    assert payload["clear_count"] == len(ids)
    assert payload["no_summary_count"] == 0


def test_no_residents_makes_no_batch_get(aws):
    payload = body(call())
    assert aws.ops() == ["query"]
    assert payload["available"] is False
    assert payload["no_summary_count"] == 0


# ---- criterion 5: BatchGetItem ----


def test_batch_get_shape(aws):
    residents(aws, rid(3), rid(4))
    call()
    (batch,) = aws.batch_gets()
    request = batch["RequestItems"]
    assert list(request) == [handler.os.environ["RESULTS_TABLE"]]
    (table,) = request.values()
    assert table["Keys"] == [
        {"pk": {"S": f"RESIDENT#{r}"}, "sk": {"S": f"NIGHT#{YESTERDAY}"}}
        for r in (rid(3), rid(4))
    ]
    assert table["ConsistentRead"] is False
    names = table["ExpressionAttributeNames"]
    paths = []
    for part in table["ProjectionExpression"].split(","):
        paths.append(".".join(names[p] for p in part.strip().split(".")))
    assert sorted(paths) == sorted(
        ["pk", *(f"summary.{f}" for f in FLAG_FIELDS), "summary.thresholds"]
    )


def test_batch_get_sends_at_most_100_keys_per_call(aws):
    ids = [rid(n) for n in range(1, 102)]
    residents(aws, *ids)
    put_counts(aws.ddb, rid(101), falls=1)
    put_counts(aws.ddb, rid(1))
    payload = body(call())
    sizes = [
        len(next(iter(b["RequestItems"].values()))["Keys"]) for b in aws.batch_gets()
    ]
    assert sizes == [100, 1]
    assert [e["resident_id"] for e in payload["flagged"]] == [rid(101)]
    assert payload["clear_count"] == 1
    assert payload["no_summary_count"] == 99


class Unprocessed:
    """Wraps the recorder: the first `times` BatchGetItem calls process nothing and
    return every key as UnprocessedKeys."""

    def __init__(self, inner, times: int) -> None:
        self._inner = inner
        self.times = times
        self.batch_calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def batch_get_item(self, **kwargs):
        self.batch_calls += 1
        if self.batch_calls <= self.times:
            self._inner.calls.append(("batch_get_item", kwargs))
            return {
                "Responses": {handler.os.environ["RESULTS_TABLE"]: []},
                "UnprocessedKeys": kwargs["RequestItems"],
            }
        return self._inner.batch_get_item(**kwargs)


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(handler, "sleep", waited.append)
    return waited


def _unprocessed(aws, monkeypatch, times: int) -> Unprocessed:
    client = Unprocessed(aws.client, times)
    monkeypatch.setattr(handler, "dynamodb_client", lambda: client)
    return client


def test_unprocessed_keys_are_retried_with_exponential_back_off(
    aws, monkeypatch, sleeps
):
    residents(aws, rid(3), rid(4))
    put_counts(aws.ddb, rid(3), falls=1)
    put_counts(aws.ddb, rid(4))
    client = _unprocessed(aws, monkeypatch, 3)
    payload = body(call())
    assert client.batch_calls == 4
    assert sleeps == [0.05, 0.1, 0.2]
    assert [e["resident_id"] for e in payload["flagged"]] == [rid(3)]
    assert payload["clear_count"] == 1


def test_retry_sends_only_the_unprocessed_keys(aws, monkeypatch, sleeps):
    residents(aws, rid(3), rid(4))
    put_counts(aws.ddb, rid(3))
    put_counts(aws.ddb, rid(4))
    table = handler.os.environ["RESULTS_TABLE"]

    class Partial:
        def __init__(self, inner):
            self._inner = inner
            self.requests: list = []

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def batch_get_item(self, **kwargs):
            self.requests.append(kwargs)
            if len(self.requests) == 1:
                keys = kwargs["RequestItems"][table]["Keys"]
                first = {**kwargs["RequestItems"][table], "Keys": keys[:1]}
                rest = {**kwargs["RequestItems"][table], "Keys": keys[1:]}
                result = self._inner.batch_get_item(RequestItems={table: first})
                result["UnprocessedKeys"] = {table: rest}
                return result
            return self._inner.batch_get_item(**kwargs)

    client = Partial(aws.client)
    monkeypatch.setattr(handler, "dynamodb_client", lambda: client)
    payload = body(call())
    assert len(client.requests) == 2
    assert [
        k["pk"]["S"] for k in client.requests[1]["RequestItems"][table]["Keys"]
    ] == ["RESIDENT#res-0004"]
    assert payload["clear_count"] == 2
    assert payload["no_summary_count"] == 0


def test_keys_still_unprocessed_after_5_attempts_return_500(
    aws, monkeypatch, sleeps, logs
):
    residents(aws, rid(3))
    put_counts(aws.ddb, rid(3), falls=1)
    client = _unprocessed(aws, monkeypatch, 5)
    monkeypatch.setattr(handler, "datetime", _fixed(NOW))
    resp = handler.overview_handler(overview_event(), None)
    assert client.batch_calls == 5
    assert len(sleeps) == 4
    assert resp["statusCode"] == 500
    assert body(resp) == {
        "error": {
            "code": "internal_error",
            "message": "Could not load the nightly overview.",
        }
    }
    assert "flagged" not in resp["body"]
    (record,) = [r for r in logs() if r.get("outcome") == "error"]
    assert record["error"] == "RuntimeError"


def test_keys_processed_on_the_fifth_attempt_succeed(aws, monkeypatch, sleeps):
    residents(aws, rid(3))
    put_counts(aws.ddb, rid(3), falls=1)
    client = _unprocessed(aws, monkeypatch, 4)
    resp = call()
    assert resp["statusCode"] == 200
    assert client.batch_calls == 5
    assert len(body(resp)["flagged"]) == 1


# ---- criterion 13: 500 ----


def _fixed(moment: datetime):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz)

    return Fixed


@pytest.mark.parametrize(
    ("operation", "api_name"), [("query", "Query"), ("batch_get_item", "BatchGetItem")]
)
def test_dynamodb_failure_returns_500_without_detail(
    aws, logs, monkeypatch, operation, api_name
):
    residents(aws, rid(3))
    aws.client.fail[operation] = _throttled(api_name)
    monkeypatch.setattr(handler, "datetime", _fixed(NOW))
    resp = handler.overview_handler(overview_event(), None)
    assert resp["statusCode"] == 500
    assert body(resp) == {
        "error": {
            "code": "internal_error",
            "message": "Could not load the nightly overview.",
        }
    }
    assert resp["headers"]["Cache-Control"] == "no-store"
    assert "qx-5521" not in resp["body"]
    (record,) = [r for r in logs() if r.get("outcome") == "error"]
    assert record["error"] == "ProvisionedThroughputExceededException"
    assert "qx-5521" not in json.dumps(logs())


def test_overview_handler_uses_the_current_singapore_time(aws, monkeypatch):
    monkeypatch.setattr(handler, "datetime", _fixed(BEFORE_READY))
    payload = body(handler.overview_handler(overview_event(), None))
    assert payload["expected_date"] == YESTERDAY
    assert payload["not_ready"] is False


# ---- criteria 3, 6: expected_date, available, not_ready ----


def test_expected_date_is_yesterday_in_singapore(aws):
    # 2026-11-18 16:00 UTC is already 2026-11-19 00:00 SGT.
    assert body(call(now=sgt(2026, 11, 19, 0, 0, 0)))["expected_date"] == YESTERDAY


def test_expected_date_just_before_singapore_midnight(aws):
    assert body(call(now=sgt(2026, 11, 19, 23, 59, 59)))["expected_date"] == YESTERDAY


@pytest.mark.parametrize(
    ("now", "not_ready"), [(BEFORE_READY, False), (NOW, True)], ids=["0929", "0930"]
)
def test_without_items_not_ready_turns_true_at_0930(aws, now, not_ready):
    residents(aws, rid(3), rid(4))
    payload = body(call(now=now))
    assert payload == {
        "expected_date": YESTERDAY,
        "available": False,
        "not_ready": not_ready,
        "flagged": [],
        "clear_count": 0,
        "no_summary_count": 0,
    }


@pytest.mark.parametrize("now", [BEFORE_READY, NOW], ids=["0929", "0930"])
def test_with_items_available_and_never_not_ready(aws, now):
    residents(aws, rid(3), rid(4))
    put_counts(aws.ddb, rid(3), falls=1)
    payload = body(call(now=now))
    assert payload["available"] is True
    assert payload["not_ready"] is False
    assert payload["no_summary_count"] == 1


def test_only_older_nights_is_not_available(aws):
    residents(aws, rid(3))
    put_counts(aws.ddb, rid(3), date="2026-11-17", falls=1)
    payload = body(call())
    assert payload["available"] is False
    assert payload["not_ready"] is True
    assert payload["flagged"] == []


def test_nothing_flagged_is_available_with_an_empty_list(aws):
    residents(aws, rid(3), rid(4))
    put_counts(aws.ddb, rid(3))
    put_counts(aws.ddb, rid(4))
    payload = body(call())
    assert payload["available"] is True
    assert payload["flagged"] == []
    assert payload["clear_count"] == 2


def test_not_ready_late_in_the_day(aws):
    residents(aws, rid(3))
    assert body(call(now=sgt(2026, 11, 19, 23, 0, 0)))["not_ready"] is True


# ---- criterion 12: logs ----


def test_ok_log_has_outcome_route_ids_date_and_counts(aws, logs):
    residents(aws, rid(1), rid(2), rid(3), rid(4))
    put_counts(aws.ddb, rid(1), falls=1)
    put_counts(aws.ddb, rid(2))
    put_counts(aws.ddb, rid(3))
    call()
    (record,) = [r for r in logs() if r.get("outcome") == "ok"]
    assert record["route"] == "GET /nightly/overview"
    assert record["request_id"] == REQUEST_ID
    assert record["sub"] == SUB
    assert record["expected_date"] == YESTERDAY
    assert record["residents"] == 4
    assert record["flagged"] == 1
    assert record["clear"] == 2
    assert record["no_summary"] == 1


def test_forbidden_log_has_the_groups_and_no_reads(no_aws, logs):
    call(overview_event(groups=("nurse", "caregiver")))
    (record,) = logs()
    assert record["outcome"] == "forbidden"
    assert record["groups"] == ["caregiver", "nurse"]
    assert record["sub"] == SUB
    assert record["route"] == "GET /nightly/overview"


def test_free_text_sub_and_request_id_are_logged_as_invalid(no_aws, logs):
    event = overview_event(groups=("nurse",), claims={"sub": "Test Doctor Zq"})
    event["requestContext"]["requestId"] = "synthetic request with spaces"
    call(event)
    (record,) = logs()
    assert record["sub"] == "invalid"
    assert record["request_id"] == "invalid"


def test_warm_container_drops_the_previous_expected_date(aws, logs):
    call()
    call(overview_event(groups=("nurse",)))
    (forbidden,) = [r for r in logs() if r.get("outcome") == "forbidden"]
    assert "expected_date" not in forbidden


def test_logs_never_hold_resident_ids_values_or_thresholds(aws, logs):
    residents(aws, rid(3), rid(4), rid(5))
    put_counts(
        aws.ddb,
        rid(3),
        falls=731,
        vitals_alert_episodes=842,
        hr_bpm_above=953,
        thresholds={
            "hr_bpm": {"min": Decimal(47), "max": Decimal(137)},
            "spo2_pct": {"min": Decimal(91)},
            "skin_temp_c": {"min": Decimal("29.7"), "max": Decimal("38.3")},
        },
    )
    put_counts(aws.ddb, rid(4), spo2_pct_below=864)
    put_counts(aws.ddb, rid(5), skin_temp_c_below="synthetic")
    call()
    records = logs()
    assert records
    text = json.dumps(records)
    assert "res-" not in text
    assert "RESIDENT#" not in text
    for value in ("731", "842", "953", "864", "1684", "137", "29.7", "38.3"):
        assert value not in text, value
    for name in ("thresholds", "hr_bpm", "spo2", "skin_temp", "falls"):
        assert name not in text, name
    assert "Test Resident" not in text
