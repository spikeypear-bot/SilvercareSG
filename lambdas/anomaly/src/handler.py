"""Nightly anomaly score (docs/features/nightly-anomaly.md). Three functions share this
zip, each with its own role:

- `score_handler` (silvercare-anomaly-score): after `results`, builds last night's
  z-score rows and starts a SageMaker batch transform on the current model.
- `train_handler` (silvercare-anomaly-train): weekly, builds 90 days of rows and starts
  a Random Cut Forest training job.
- `collect_handler` (silvercare-anomaly-collect): on SageMaker job state changes (and
  direct calls from score), creates the model and its calibration transform, writes the
  cut-off, and writes nightly scores to S3 `scores/` and the Results table.

S3 layout in the ML bucket:
  input/score/date=<D>/<run>/rows.csv, rows.json    output/score/date=<D>/<run>/rows.csv.out
  input/train/<version>/train.csv                    output/cal/<version>/train.csv.out
  models/<version>/output/model.tar.gz, models/<version>/cutoff.json, models/current.json
  scores/date=<D>/<uuid4>.json (tag resident_id)

Logs: dates, job names, counts, model version. Never feature values, z-scores, scores,
or explanations (criterion 25).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from functools import cache
from typing import Any

import boto3
import features as f
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

logger = Logger()

SGT = timezone(timedelta(hours=8))
INSTANCE_TYPE = "ml.m5.large"
TRAIN_DAYS = 90
MIN_TRAIN_ROWS = 200
RCF_HYPERPARAMETERS = {
    "feature_dim": str(len(f.FEATURES)),
    "num_trees": "100",
    "num_samples_per_tree": "256",
}
CURRENT_KEY = "models/current.json"
MAX_OBJECT_BYTES = 64 * 1024
READ_WORKERS = 16

RUN_RE = re.compile(r"[0-9a-f]{8}")
VERSION_RE = re.compile(r"anomaly-train-[0-9]{8}-[0-9a-f]{8}")
SCORE_JOB_RE = re.compile(r"anomaly-score-([0-9]{4}-[0-9]{2}-[0-9]{2})-([0-9a-f]{8})")
CAL_JOB_RE = re.compile(r"anomaly-cal-(anomaly-train-[0-9]{8}-[0-9a-f]{8})")
DONE = "Completed"
ENDED = ("Completed", "Failed", "Stopped")

_serializer = TypeSerializer()


class JobFailed(Exception):
    """A SageMaker job ended without completing, or its output can't be used."""


# ---- clients ----


@cache
def s3_client() -> Any:
    return boto3.client("s3")


@cache
def sagemaker_client() -> Any:
    return boto3.client("sagemaker")


@cache
def lambda_client() -> Any:
    return boto3.client("lambda")


@cache
def dynamodb_client() -> Any:
    """Adaptive retries for the 1 WCU Results table (criterion 12)."""
    return boto3.client(
        "dynamodb",
        config=Config(retries={"mode": "adaptive"}),
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT"),
    )


def ml_bucket() -> str:
    return os.environ["ML_BUCKET"]


def now_utc() -> datetime:
    return datetime.now(UTC)


def new_run() -> str:
    return uuid.uuid4().hex[:8]


# ---- S3 helpers ----


def read_bytes(bucket: str, key: str, limit: int | None = MAX_OBJECT_BYTES) -> bytes:
    body = s3_client().get_object(Bucket=bucket, Key=key)["Body"]
    try:
        data = body.read() if limit is None else body.read(limit + 1)
    finally:
        body.close()
    if limit is not None and len(data) > limit:
        raise f.InvalidSummary("too big")
    return data


def put_json(key: str, value: Any, tagging: str | None = None) -> None:
    extra = {"Tagging": tagging} if tagging else {}
    s3_client().put_object(
        Bucket=ml_bucket(),
        Key=key,
        Body=json.dumps(value, separators=(",", ":")).encode(),
        ContentType="application/json",
        **extra,
    )


def list_keys(bucket: str, prefix: str) -> list[str]:
    pages = (
        s3_client()
        .get_paginator("list_objects_v2")
        .paginate(Bucket=bucket, Prefix=prefix)
    )
    return [o["Key"] for page in pages for o in page.get("Contents", [])]


def read_date(date: str) -> tuple[list[f.Night], int]:
    """Every valid summary for Singapore date D in the history bucket, and how many
    objects were skipped as invalid."""
    bucket = os.environ["HISTORY_BUCKET"]
    nights: list[f.Night] = []
    skipped = 0
    for key in list_keys(bucket, f"summaries/date={date}/"):
        try:
            nights.append(f.parse_summary(read_bytes(bucket, key), date))
        except f.InvalidSummary:
            skipped += 1
    return nights, skipped


def read_dates(dates: list[str]) -> dict[tuple[str, str], f.Night]:
    """(resident_id, date) -> night, for every date, read in parallel."""
    s3_client()  # create the client once, before the threads share it
    with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
        results = list(pool.map(read_date, dates))
    skipped = sum(s for _, s in results)
    if skipped:
        logger.error("summaries skipped", extra={"skipped": skipped})
    return {(n["resident_id"], n["date"]): n for nights, _ in results for n in nights}


def current_model() -> dict[str, Any] | None:
    """models/current.json, or None before the first calibrated model."""
    try:
        body = read_bytes(ml_bucket(), CURRENT_KEY)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    current = json.loads(body)
    if not isinstance(current, dict) or not VERSION_RE.fullmatch(
        str(current.get("version"))
    ):
        raise JobFailed("models/current.json is not valid")
    return current


# ---- input parsing ----


def parse_score_event(event: Any) -> tuple[str, int | None]:
    """(date, loaded count or None): `results`' on-success record or {"date": D}."""
    if not isinstance(event, dict):
        raise TypeError("event must be an object")
    if "responsePayload" in event:
        context = event.get("requestContext")
        if not isinstance(context, dict) or context.get("condition") != "Success":
            raise ValueError("invocation record must be an on-success record")
        payload = event["responsePayload"]
        if not isinstance(payload, dict) or "date" not in payload:
            raise ValueError("invocation record has no responsePayload.date")
        loaded = payload.get("loaded")
        if type(loaded) is not int or loaded < 0:
            raise ValueError("invocation record has no valid responsePayload.loaded")
        return f.parse_date(payload["date"]), loaded
    if set(event) == {"date"}:
        return f.parse_date(event["date"]), None
    raise ValueError('event must be {"date": "yyyy-mm-dd"} or an on-success record')


# ---- score ----


def build_sidecar(
    date: str,
    run: str,
    residents: list[str],
    nights: dict[tuple[str, str], f.Night],
    current: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[list[float]]]:
    """The sidecar (every resident with a summary for D, scored rows first, in CSV
    order) and the CSV rows. Pure."""
    scored: list[dict[str, Any]] = []
    rows: list[list[float]] = []
    others: list[dict[str, Any]] = []
    for resident_id in sorted(residents):
        a = f.assess(resident_id, date, nights)
        if a["status"] == "scoreable" and current is None:
            others.append({**_entry(a), "status": "no_model"})
        elif a["status"] == "scoreable":
            scored.append({**_entry(a), "status": "scored", "reasons": a["reasons"]})
            rows.append(a["z"])
        else:
            others.append(_entry(a))
    sidecar = {
        "date": date,
        "run": run,
        "model_version": current["version"] if current else None,
        "cutoff": current["cutoff"] if current else None,
        "rows": scored + others,
    }
    return sidecar, rows


def _entry(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "resident_id": a["resident_id"],
        "status": a["status"],
        "usable_nights": a["usable_nights"],
        "reasons": [],
    }


def score_prefix(date: str, run: str) -> str:
    return f"input/score/date={date}/{run}/"


def start_transform(
    name: str, model: str, input_prefix: str, output_prefix: str
) -> None:
    bucket = ml_bucket()
    sagemaker_client().create_transform_job(
        TransformJobName=name,
        ModelName=model,
        BatchStrategy="MultiRecord",
        MaxPayloadInMB=6,
        TransformInput={
            "DataSource": {
                "S3DataSource": {
                    "S3DataType": "S3Prefix",
                    "S3Uri": f"s3://{bucket}/{input_prefix}",
                }
            },
            "ContentType": "text/csv",
            "SplitType": "Line",
        },
        TransformOutput={
            "S3OutputPath": f"s3://{bucket}/{output_prefix}",
            "Accept": "application/jsonlines",
            "AssembleWith": "Line",
        },
        TransformResources={"InstanceType": INSTANCE_TYPE, "InstanceCount": 1},
    )


def score(date: str) -> dict[str, Any]:
    current = current_model()
    if current is None:
        logger.warning("no anomaly model yet", extra={"date": date})
    dates = [date, *f.baseline_dates(date)]
    nights = read_dates(dates)
    residents = [r for (r, d) in nights if d == date]
    run = new_run()
    sidecar, rows = build_sidecar(date, run, residents, nights, current)
    prefix = score_prefix(date, run)
    # rows.json sits beside the CSV prefix-wise but in its own folder name, so the
    # transform (which reads every object under its prefix) only reads rows.csv.
    put_json(f"input/score-meta/date={date}/{run}/rows.json", sidecar)
    counts = _status_counts(sidecar)
    if rows:
        s3_client().put_object(
            Bucket=ml_bucket(),
            Key=f"{prefix}rows.csv",
            Body=f.csv_rows(rows),
            ContentType="text/csv",
        )
        job = f"anomaly-score-{date}-{run}"
        start_transform(
            job, current["version"], prefix, f"output/score/date={date}/{run}/"
        )
        logger.info("scoring started", extra={"date": date, "job": job, **counts})
    else:
        lambda_client().invoke(
            FunctionName=os.environ["COLLECT_FUNCTION"],
            InvocationType="Event",
            Payload=json.dumps({"date": date, "run": run}).encode(),
        )
        logger.info("nothing to score", extra={"date": date, **counts})
    return {"date": date, "run": run, "scored": len(rows)}


def _status_counts(sidecar: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in sidecar["rows"]:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def score_handler(event: Any, context: Any) -> dict[str, Any]:
    date, loaded = parse_score_event(event)
    if loaded == 0:
        logger.info("results loaded nothing: nothing to score", extra={"date": date})
        return {"date": date, "run": None, "scored": 0}
    return score(date)


# ---- train ----


def training_rows(
    today: str, nights: dict[tuple[str, str], f.Night]
) -> list[list[float]]:
    """Every scoreable resident-night in the last 90 days (criterion 9). Pure."""
    residents = sorted({r for r, _ in nights})
    rows = []
    for day in range(TRAIN_DAYS, 0, -1):
        date = f.shift(today, -day)
        for resident_id in residents:
            a = f.assess(resident_id, date, nights)
            if a["status"] == "scoreable":
                rows.append(a["z"])
    return rows


def train(today: str) -> dict[str, Any]:
    first = f.shift(today, -(TRAIN_DAYS + f.BASELINE_FIRST))
    dates = [f.shift(first, i) for i in range(TRAIN_DAYS + f.BASELINE_FIRST)]
    rows = training_rows(today, read_dates(dates))
    if len(rows) < MIN_TRAIN_ROWS:
        logger.error("too few training rows", extra={"rows": len(rows)})
        raise JobFailed(f"{len(rows)} training rows, need {MIN_TRAIN_ROWS}")
    version = f"anomaly-train-{today.replace('-', '')}-{new_run()}"
    bucket = ml_bucket()
    s3_client().put_object(
        Bucket=bucket,
        Key=f"input/train/{version}/train.csv",
        Body=f.csv_rows(rows),
        ContentType="text/csv",
    )
    sagemaker_client().create_training_job(
        TrainingJobName=version,
        AlgorithmSpecification={
            "TrainingImage": os.environ["RCF_IMAGE"],
            "TrainingInputMode": "File",
        },
        RoleArn=os.environ["SAGEMAKER_ROLE_ARN"],
        HyperParameters=RCF_HYPERPARAMETERS,
        InputDataConfig=[
            {
                "ChannelName": "train",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": f"s3://{bucket}/input/train/{version}/",
                        "S3DataDistributionType": "ShardedByS3Key",
                    }
                },
                "ContentType": "text/csv;label_size=0",
            }
        ],
        OutputDataConfig={"S3OutputPath": f"s3://{bucket}/models/"},
        ResourceConfig={
            "InstanceType": INSTANCE_TYPE,
            "InstanceCount": 1,
            "VolumeSizeInGB": 1,
        },
        StoppingCondition={"MaxRuntimeInSeconds": 1800},
    )
    logger.info("training started", extra={"job": version, "rows": len(rows)})
    return {"version": version, "rows": len(rows)}


def train_handler(event: Any, context: Any) -> dict[str, Any]:
    """The weekly schedule's event, or {} / {"today": D} manually."""
    if isinstance(event, dict) and "today" in event:
        today = f.parse_date(event["today"])
    else:
        today = now_utc().astimezone(SGT).date().isoformat()
    return train(today)


# ---- collect ----


def on_training_done(version: str) -> None:
    """Criterion 11, first half: model + calibration transform over the training rows."""
    bucket = ml_bucket()
    sagemaker_client().create_model(
        ModelName=version,
        PrimaryContainer={
            "Image": os.environ["RCF_IMAGE"],
            "ModelDataUrl": f"s3://{bucket}/models/{version}/output/model.tar.gz",
        },
        ExecutionRoleArn=os.environ["SAGEMAKER_ROLE_ARN"],
    )
    job = f"anomaly-cal-{version}"
    start_transform(job, version, f"input/train/{version}/", f"output/cal/{version}/")
    logger.info("calibration started", extra={"job": job})


def on_calibration_done(version: str) -> None:
    """Criterion 11, second half: cutoff.json, then current.json."""
    body = read_bytes(ml_bucket(), f"output/cal/{version}/train.csv.out", limit=None)
    stats = f.cutoff(f.parse_scores(body))
    put_json(f"models/{version}/cutoff.json", stats)
    put_json(CURRENT_KEY, {"version": version, **stats})
    logger.info("model is current", extra={"version": version, "rows": stats["rows"]})


def score_record(
    row: dict[str, Any], score_value: float | None, sidecar: dict[str, Any], at: str
) -> dict[str, Any]:
    """The kept `scores/` object for one resident-night (criterion 12). Pure."""
    scored = row["status"] == "scored"
    cut = sidecar["cutoff"] if scored else None
    return {
        "resident_id": row["resident_id"],
        "date": sidecar["date"],
        "status": row["status"],
        "score": round(score_value, 4) if scored else None,
        "cutoff": cut,
        "unusual": (score_value > cut) if scored else None,
        "reasons": row["reasons"],
        "usable_nights": row["usable_nights"],
        "model_version": sidecar["model_version"] if scored else None,
        "generated_at": at,
        "schema_version": f.SCHEMA_VERSION,
    }


def combine(
    sidecar: dict[str, Any], scores: list[float], at: str
) -> list[dict[str, Any]]:
    """Pair scored rows (first in the sidecar, CSV order) with the transform output.
    Raises JobFailed if the counts differ (criterion 14). Pure."""
    scored = [r for r in sidecar["rows"] if r["status"] == "scored"]
    if len(scores) != len(scored):
        raise JobFailed("transform output doesn't match its input")
    values = iter(scores)
    return [
        score_record(r, next(values) if r["status"] == "scored" else None, sidecar, at)
        for r in sidecar["rows"]
    ]


def anomaly_attribute(record: dict[str, Any]) -> dict[str, Any]:
    """The Results item's `anomaly` map, as a low-level DynamoDB value."""
    value = {
        k: v
        for k, v in record.items()
        if k not in ("resident_id", "date", "generated_at")
    }
    return _serializer.serialize(json.loads(json.dumps(value), parse_float=Decimal))


def write_scores(date: str, records: list[dict[str, Any]]) -> dict[str, int]:
    bucket = ml_bucket()
    for key in list_keys(bucket, f"scores/date={date}/"):
        s3_client().delete_object(Bucket=bucket, Key=key)
    missing = 0
    for record in records:
        put_json(
            f"scores/date={date}/{uuid.uuid4()}.json",
            record,
            tagging=f"resident_id={record['resident_id']}",
        )
        try:
            dynamodb_client().update_item(
                TableName=os.environ["RESULTS_TABLE"],
                Key={
                    "pk": {"S": f"RESIDENT#{record['resident_id']}"},
                    "sk": {"S": f"NIGHT#{date}"},
                },
                UpdateExpression="SET anomaly = :a",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={":a": anomaly_attribute(record)},
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code != "ConditionalCheckFailedException":
                raise
            missing += 1
            logger.warning(
                "no results item to update",
                extra={"date": date, "resident_id": record["resident_id"]},
            )
    return {"written": len(records), "no_results_item": missing}


def on_scoring_done(date: str, run: str, with_output: bool) -> None:
    bucket = ml_bucket()
    sidecar = json.loads(
        read_bytes(bucket, f"input/score-meta/date={date}/{run}/rows.json", limit=None)
    )
    scores = []
    if with_output:
        body = read_bytes(bucket, f"output/score/date={date}/{run}/rows.csv.out", None)
        scores = f.parse_scores(body)
    records = combine(sidecar, scores, iso_ms(now_utc()))
    counts = write_scores(date, records)
    unusual = sum(1 for r in records if r["unusual"])
    logger.info(
        "scores written",
        extra={"date": date, "run": run, "unusual": unusual, **counts},
    )


def iso_ms(moment: datetime) -> str:
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def collect_handler(event: Any, context: Any) -> dict[str, Any]:
    """EventBridge SageMaker job state changes, or {"date", "run"} from score."""
    if not isinstance(event, dict):
        raise TypeError("event must be an object")
    if set(event) == {"date", "run"}:
        date = f.parse_date(event["date"])
        if not isinstance(event["run"], str) or not RUN_RE.fullmatch(event["run"]):
            raise ValueError("run must be 8 hex characters")
        on_scoring_done(date, event["run"], with_output=False)
        return {"handled": "direct", "date": date}
    detail = event.get("detail")
    if not isinstance(detail, dict):
        raise TypeError("event must be a SageMaker job state change")
    kind = event.get("detail-type")
    if kind == "SageMaker Training Job State Change":
        name, status = detail.get("TrainingJobName"), detail.get("TrainingJobStatus")
        if not isinstance(name, str) or not VERSION_RE.fullmatch(name):
            raise ValueError("unexpected training job name")
        _check_status(name, status)
        on_training_done(name)
        return {"handled": "training", "job": name}
    if kind == "SageMaker Transform Job State Change":
        name, status = detail.get("TransformJobName"), detail.get("TransformJobStatus")
        name = name if isinstance(name, str) else ""
        if cal := CAL_JOB_RE.fullmatch(name):
            _check_status(name, status)
            on_calibration_done(cal.group(1))
            return {"handled": "calibration", "job": name}
        if job := SCORE_JOB_RE.fullmatch(name):
            _check_status(name, status)
            on_scoring_done(f.parse_date(job.group(1)), job.group(2), with_output=True)
            return {"handled": "scoring", "job": name}
        raise ValueError("unexpected transform job name")
    raise ValueError("event must be a SageMaker job state change")


def _check_status(name: str, status: Any) -> None:
    if status not in ENDED:
        raise ValueError("unexpected job status")
    if status != DONE:
        logger.error("job did not complete", extra={"job": name, "status": status})
        raise JobFailed(f"{name} ended {status}")
