"""score, train, and collect handlers (nightly-anomaly.md criteria 5-14, 25)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import features as f
import handler
import pytest
from botocore.exceptions import ClientError
from events import (
    DATE,
    on_success,
    put_history,
    put_summary,
    summary,
    training_event,
    transform_event,
)

ML = "silvercare-ml-test"
RUN = "0a1b2c3d"
VERSION = "anomaly-train-20261116-a1b2c3d4"
SCORE_JOB = f"anomaly-score-{DATE}-{RUN}"
META = f"input/score-meta/date={DATE}/{RUN}/rows.json"


def set_current(aws, cutoff: float = 2.0) -> None:
    aws.put(
        ML,
        "models/current.json",
        json.dumps(
            {"version": VERSION, "cutoff": cutoff, "mean": 1.0, "sd": 0.3, "rows": 300}
        ),
    )


def three_residents(aws) -> None:
    """res-0001 scoreable, res-0002 low wear tonight, res-0003 too little history."""
    put_history(aws, "res-0001", DATE, 37)
    put_summary(aws, summary("res-0001", hr_bpm_avg=85.0))
    put_history(aws, "res-0002", DATE, 37)
    put_summary(aws, summary("res-0002", vitals_readings=300))
    put_history(aws, "res-0003", DATE, 10)
    put_summary(aws, summary("res-0003"))


# ---- score ----


def test_score_starts_a_transform_for_scoreable_residents_only(aws):
    set_current(aws)
    three_residents(aws)
    out = handler.score_handler(on_success(), None)
    assert out == {"date": DATE, "run": RUN, "scored": 1}
    csv = aws.get(ML, f"input/score/date={DATE}/{RUN}/rows.csv").decode()
    assert len(csv.splitlines()) == 1 and len(csv.split(",")) == 9
    (job,) = aws.sagemaker.params("create_transform_job")
    assert job["TransformJobName"] == SCORE_JOB and job["ModelName"] == VERSION
    assert job["TransformInput"]["DataSource"]["S3DataSource"]["S3Uri"] == (
        f"s3://{ML}/input/score/date={DATE}/{RUN}/"
    )
    meta = json.loads(aws.get(ML, META))
    assert [(r["resident_id"], r["status"]) for r in meta["rows"]] == [
        ("res-0001", "scored"),
        ("res-0002", "low_wear"),
        ("res-0003", "not_enough_history"),
    ]
    assert meta["cutoff"] == 2.0 and meta["model_version"] == VERSION


def test_score_without_a_model_hands_statuses_to_collect(aws):
    three_residents(aws)
    out = handler.score_handler({"date": DATE}, None)
    assert out["scored"] == 0
    assert aws.sagemaker.calls == []
    (call,) = aws.lam.params("invoke")
    assert json.loads(call["Payload"]) == {"date": DATE, "run": RUN}
    statuses = [r["status"] for r in json.loads(aws.get(ML, META))["rows"]]
    assert statuses == ["no_model", "low_wear", "not_enough_history"]


def test_score_skips_when_results_loaded_nothing(aws):
    out = handler.score_handler(on_success(loaded=0), None)
    assert out["scored"] == 0
    assert aws.keys(ML) == [] and aws.sagemaker.calls == []


@pytest.mark.parametrize(
    "event",
    [
        {"date": "2026-02-30"},
        {"date": DATE, "extra": 1},
        {"responsePayload": {"date": DATE, "loaded": 1}},
        "not an object",
    ],
)
def test_score_rejects_bad_input_before_any_aws_call(monkeypatch, event):
    monkeypatch.setattr(handler, "s3_client", lambda: pytest.fail("AWS called"))
    with pytest.raises((ValueError, TypeError)):
        handler.score_handler(event, None)


def test_invalid_summary_object_is_skipped_and_logged_without_values(aws, logs):
    set_current(aws)
    three_residents(aws)
    aws.put(
        "silvercare-history-test", f"summaries/date={DATE}/bad.json", "not json 85.0"
    )
    handler.score_handler({"date": DATE}, None)
    assert any(r.get("skipped") == 1 for r in logs())
    assert "85" not in json.dumps(logs())


# ---- collect: scoring ----


def scored_run(aws, score: float) -> None:
    set_current(aws)
    three_residents(aws)
    handler.score_handler({"date": DATE}, None)
    aws.put(
        ML, f"output/score/date={DATE}/{RUN}/rows.csv.out", json.dumps({"score": score})
    )
    for r in ("res-0001", "res-0002", "res-0003"):
        aws.put_results_item(r, DATE)


def test_collect_writes_scores_and_results_items(aws):
    scored_run(aws, 2.5)
    handler.collect_handler(transform_event(SCORE_JOB), None)
    keys = aws.keys(ML, f"scores/date={DATE}/")
    assert len(keys) == 3
    tags = aws.s3.get_object_tagging(Bucket=ML, Key=keys[0])["TagSet"]
    assert tags[0]["Key"] == "resident_id"
    anomaly = aws.results_item("res-0001", DATE)["anomaly"]
    assert anomaly["status"] == "scored" and anomaly["unusual"] is True
    assert float(anomaly["score"]) == 2.5 and anomaly["model_version"] == VERSION
    assert anomaly["reasons"][0]["feature"] == "hr_bpm_avg"
    low = aws.results_item("res-0002", DATE)["anomaly"]
    assert (
        low["status"] == "low_wear" and low["score"] is None and low["unusual"] is None
    )


def test_score_at_the_cutoff_is_not_unusual(aws):
    scored_run(aws, 2.0)
    handler.collect_handler(transform_event(SCORE_JOB), None)
    assert aws.results_item("res-0001", DATE)["anomaly"]["unusual"] is False


def test_collect_rerun_replaces_the_dates_scores(aws):
    scored_run(aws, 2.5)
    handler.collect_handler(transform_event(SCORE_JOB), None)
    handler.collect_handler(transform_event(SCORE_JOB), None)
    assert len(aws.keys(ML, f"scores/date={DATE}/")) == 3


def test_collect_never_creates_a_results_item(aws):
    set_current(aws)
    three_residents(aws)
    handler.score_handler({"date": DATE}, None)
    aws.put(ML, f"output/score/date={DATE}/{RUN}/rows.csv.out", '{"score": 1.0}')
    handler.collect_handler(transform_event(SCORE_JOB), None)
    assert aws.results_item("res-0001", DATE) is None
    assert len(aws.keys(ML, "scores/")) == 3


def test_collect_direct_call_writes_statuses(aws):
    three_residents(aws)
    handler.score_handler({"date": DATE}, None)
    aws.put_results_item("res-0001", DATE)
    handler.collect_handler({"date": DATE, "run": RUN}, None)
    assert aws.results_item("res-0001", DATE)["anomaly"]["status"] == "no_model"


def test_output_count_mismatch_writes_nothing(aws):
    scored_run(aws, 2.5)
    aws.put(
        ML,
        f"output/score/date={DATE}/{RUN}/rows.csv.out",
        '{"score": 1}\n{"score": 2}\n',
    )
    with pytest.raises(handler.JobFailed):
        handler.collect_handler(transform_event(SCORE_JOB), None)
    assert aws.keys(ML, "scores/") == []


def test_failed_scoring_job_raises_and_writes_nothing(aws):
    scored_run(aws, 2.5)
    with pytest.raises(handler.JobFailed):
        handler.collect_handler(transform_event(SCORE_JOB, "Failed"), None)
    assert aws.keys(ML, "scores/") == []


def test_throttled_results_write_raises_for_retry(aws, monkeypatch):
    scored_run(aws, 2.5)
    error = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem"
    )

    class Throttled:
        def update_item(self, **kwargs):
            raise error

    monkeypatch.setattr(handler, "dynamodb_client", lambda: Throttled())
    with pytest.raises(ClientError):
        handler.collect_handler(transform_event(SCORE_JOB), None)


def test_collect_logs_no_scores_or_reasons(aws, logs):
    scored_run(aws, 7.3219)
    handler.collect_handler(transform_event(SCORE_JOB), None)
    text = json.dumps(logs())
    assert "7.32" not in text and "hr_bpm_avg" not in text


@pytest.mark.parametrize(
    "event",
    [
        transform_event("someone-elses-job"),
        transform_event(SCORE_JOB, "InProgress"),
        {"date": DATE, "run": "../../x"},
        {"detail-type": "Other", "detail": {}},
    ],
)
def test_collect_rejects_unexpected_events(aws, event):
    with pytest.raises(ValueError):
        handler.collect_handler(event, None)


# ---- collect: training and calibration ----


def test_training_done_creates_model_and_calibration(aws):
    handler.collect_handler(training_event(VERSION), None)
    (model,) = aws.sagemaker.params("create_model")
    assert model["ModelName"] == VERSION
    assert model["PrimaryContainer"]["ModelDataUrl"] == (
        f"s3://{ML}/models/{VERSION}/output/model.tar.gz"
    )
    (job,) = aws.sagemaker.params("create_transform_job")
    assert job["TransformJobName"] == f"anomaly-cal-{VERSION}"


def test_failed_training_leaves_the_current_model(aws):
    set_current(aws)
    with pytest.raises(handler.JobFailed):
        handler.collect_handler(training_event(VERSION, "Failed"), None)
    assert aws.sagemaker.calls == []
    assert json.loads(aws.get(ML, "models/current.json"))["version"] == VERSION


def test_calibration_writes_cutoff_then_current(aws):
    aws.put(
        ML,
        f"output/cal/{VERSION}/train.csv.out",
        '{"score": 1}\n{"score": 2}\n{"score": 3}\n',
    )
    handler.collect_handler(transform_event(f"anomaly-cal-{VERSION}"), None)
    cutoff = json.loads(aws.get(ML, f"models/{VERSION}/cutoff.json"))
    assert cutoff["cutoff"] == 4.4495
    assert json.loads(aws.get(ML, "models/current.json")) == {
        "version": VERSION,
        **cutoff,
    }


# ---- train ----


def test_train_starts_a_job_on_90_days_of_rows(aws):
    today = "2026-11-16"
    for r in ("res-0001", "res-0002", "res-0003"):
        put_history(aws, r, today, 127)
    out = handler.train_handler({"today": today}, None)
    assert out["rows"] == 270
    (job,) = aws.sagemaker.params("create_training_job")
    assert job["TrainingJobName"] == out["version"] == f"anomaly-train-20261116-{RUN}"
    assert job["HyperParameters"]["feature_dim"] == "9"
    assert job["ResourceConfig"]["InstanceType"] == "ml.m5.large"
    csv = aws.get(ML, f"input/train/{out['version']}/train.csv").decode()
    assert len(csv.splitlines()) == 270


def test_train_with_too_few_rows_raises_without_a_job(aws):
    put_history(aws, "res-0001", "2026-11-16", 127)
    with pytest.raises(handler.JobFailed):
        handler.train_handler({"today": "2026-11-16"}, None)
    assert aws.sagemaker.calls == []


def test_scheduled_train_uses_the_singapore_date(aws, monkeypatch):
    # 2026-11-15 17:00 UTC is already 16 Nov in Singapore.
    monkeypatch.setattr(
        handler, "now_utc", lambda: datetime(2026, 11, 15, 17, tzinfo=UTC)
    )
    monkeypatch.setattr(handler, "train", lambda today: {"today": today})
    assert handler.train_handler({"source": "aws.events"}, None) == {
        "today": "2026-11-16"
    }


def test_training_rows_skip_low_wear_nights():
    nights = {}
    for d in range(1, 128):
        date = f.shift("2026-11-16", -d)
        readings = 100 if d <= 10 else 1300
        nights[("res-0001", date)] = f.parse_summary(
            json.dumps(summary("res-0001", date, vitals_readings=readings)).encode(),
            date,
        )
    assert len(handler.training_rows("2026-11-16", nights)) == 80
