"""Check that smoke-test messages were stored (docs/features/ingest-live.md and
docs/features/ingest-history.md).

Run on the host with your own AWS credentials, after
`make smoke-test DEVICE=<id> ENDPOINT=<host> INGEST=1`, using the command it prints:

    pip install -r scripts/requirements.txt
    python scripts/ingest-check.py --device <id> --message-ids <id,id,...> --invalid-ids <id>
    python scripts/ingest-check.py ... --history [--bucket <name>]

Reads the device's silvercare-latest items (one strongly consistent GetItem per message
type the device sends, region ap-southeast-1) for up to 30 s. PASS when every valid
message ID appears exactly once and no invalid ID appears; otherwise FAIL with the check
that failed.

With --history it then looks for the messages in the S3 history for up to 7 minutes
(the history Lambda writes in batches of up to 5 minutes): it lists the objects under
telemetry/message=<type>/date=<today and yesterday UTC>/ for each message type the
device sends, modified since the check started minus 15 minutes, downloads and
decompresses them, and reads the message_id of each line. History keeps invalid
messages and duplicates, so this check needs every valid **and** invalid ID at least
once. The bucket defaults to silvercare-history-<account_id> (account from
sts:GetCallerIdentity); override it with --bucket.

Prints only check results and message IDs, never file contents or field values.

Creates no AWS client when imported, so the tests can load it without credentials.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import zlib
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, TextIO

REGION = "ap-southeast-1"
DEFAULT_TABLE = "silvercare-latest"
TIMEOUT_S = 30.0
POLL_INTERVAL_S = 2.0
# Once the valid messages are there, wait this long and read again, so an invalid
# message that is still on its way would be caught.
SETTLE_S = 5.0

# --history (docs/features/ingest-history.md)
HISTORY_BUCKET_PREFIX = "silvercare-history-"
HISTORY_KEY_PREFIX = "telemetry"
HISTORY_TIMEOUT_S = 420.0  # 7 minutes: batches are written within 5 minutes
HISTORY_POLL_INTERVAL_S = 15.0
# Objects modified before the check started minus this are ignored.
HISTORY_LOOKBACK = timedelta(minutes=15)

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
ACCOUNT_ID_RE = re.compile(r"^[0-9]{12}\Z")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE
)

# Message types each device type sends (docs/metrics.md).
DEVICE_MESSAGES = {
    "wearable": ("vitals", "fall", "status"),
    "bedroom": ("motion", "bed", "status"),
}


# ---- pure helpers (unit tested, no network) ----


def device_type(device_id: str) -> str:
    """Return wearable or bedroom. Raises ValueError if the ID is malformed."""
    if not DEVICE_ID_RE.match(device_id):
        raise ValueError("device ID must match ^(wearable|bedroom)-[0-9]{4}$")
    return device_id.split("-", 1)[0]


def parse_ids(value: str) -> list[str]:
    """Split a comma-separated list of message IDs. Raises ValueError if one is malformed."""
    ids = [part.strip() for part in value.split(",") if part.strip()]
    for message_id in ids:
        if not UUID_RE.match(message_id):
            raise ValueError("message IDs must be UUIDs, separated by commas")
    return ids


def item_keys(device_id: str) -> list[dict[str, dict[str, str]]]:
    """GetItem keys (low-level format) of the device's silvercare-latest items."""
    return [
        {"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": f"MSG#{message}"}}
        for message in DEVICE_MESSAGES[device_type(device_id)]
    ]


def entry_ids(item: dict[str, Any] | None) -> list[str]:
    """message_id of every entry in one low-level item; ignores every other field."""
    if not item:
        return []
    ids = []
    for entry in item.get("entries", {}).get("L", []):
        message_id = entry.get("M", {}).get("message_id", {}).get("S")
        if message_id is not None:
            ids.append(message_id)
    return ids


def evaluate(
    stored_ids: Iterable[str], valid_ids: Iterable[str], invalid_ids: Iterable[str]
) -> list[str]:
    """Return the failed checks (empty list = pass). Mentions message IDs only."""
    counts = Counter(stored_ids)
    failures = []
    for message_id in valid_ids:
        if counts[message_id] == 0:
            failures.append(f"valid message {message_id} is not stored")
        elif counts[message_id] > 1:
            failures.append(
                f"valid message {message_id} is stored {counts[message_id]} times"
            )
    for message_id in invalid_ids:
        if counts[message_id]:
            failures.append(f"invalid message {message_id} is stored")
    return failures


def history_bucket(account_id: str) -> str:
    """Default history bucket name for an account. Raises ValueError if malformed."""
    if not ACCOUNT_ID_RE.match(account_id):
        raise ValueError("account ID must be 12 digits")
    return f"{HISTORY_BUCKET_PREFIX}{account_id}"


def history_prefixes(device_id: str, now: datetime) -> list[str]:
    """S3 prefixes to list: each message type the device sends, today and yesterday (UTC)."""
    today = now.astimezone(UTC).date()
    dates = [today, today - timedelta(days=1)]
    return [
        f"{HISTORY_KEY_PREFIX}/message={message}/date={day.isoformat()}/"
        for message in DEVICE_MESSAGES[device_type(device_id)]
        for day in dates
    ]


def ids_in_object(data: bytes) -> set[str]:
    """message_id of every line in one gzipped JSON Lines object; ignores other fields.

    Lines or objects that can't be read are skipped (they can't hold the IDs we look for).
    """
    try:
        text = gzip.decompress(data).decode("utf-8")
    except (OSError, EOFError, zlib.error, UnicodeDecodeError):
        return set()
    ids = set()
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("message_id"), str):
            ids.add(record["message_id"])
    return ids


def evaluate_history(
    found_ids: Iterable[str], valid_ids: Iterable[str], invalid_ids: Iterable[str]
) -> list[str]:
    """Failed history checks (empty = pass): every valid and invalid ID at least once."""
    found = set(found_ids)
    failures = [
        f"history: valid message {message_id} is not in S3"
        for message_id in valid_ids
        if message_id not in found
    ]
    failures.extend(
        f"history: invalid message {message_id} is not in S3"
        for message_id in invalid_ids
        if message_id not in found
    )
    return failures


# ---- AWS (run by the developer; tests use botocore Stubber) ----


def read_stored_ids(client: Any, table: str, device_id: str) -> list[str]:
    """Every message_id in the device's items (strongly consistent reads)."""
    ids: list[str] = []
    for key in item_keys(device_id):
        response = client.get_item(
            TableName=table,
            Key=key,
            ConsistentRead=True,
            ProjectionExpression="#entries",
            ExpressionAttributeNames={"#entries": "entries"},
        )
        ids.extend(entry_ids(response.get("Item")))
    return ids


def check(
    client: Any,
    table: str,
    device_id: str,
    valid_ids: list[str],
    invalid_ids: list[str],
    timeout_s: float = TIMEOUT_S,
    poll_interval_s: float = POLL_INTERVAL_S,
    settle_s: float = SETTLE_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[str]:
    """Poll until the checks pass (then confirm once after settle_s) or time runs out."""
    deadline = clock() + timeout_s
    while True:
        failures = evaluate(
            read_stored_ids(client, table, device_id), valid_ids, invalid_ids
        )
        if not failures:
            sleep(settle_s)
            return evaluate(
                read_stored_ids(client, table, device_id), valid_ids, invalid_ids
            )
        if clock() + poll_interval_s > deadline:
            return failures
        sleep(poll_interval_s)


def resolve_bucket(sts_client: Any, override: str | None) -> str:
    """--bucket if given, else silvercare-history-<account_id> from sts:GetCallerIdentity."""
    if override:
        return override
    return history_bucket(sts_client.get_caller_identity()["Account"])


def recent_keys(s3_client: Any, bucket: str, prefix: str, since: datetime) -> list[str]:
    """Keys under prefix last modified at or after since."""
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["LastModified"] >= since:
                keys.append(obj["Key"])
    return keys


def read_history_ids(
    s3_client: Any,
    bucket: str,
    device_id: str,
    since: datetime,
    now: datetime,
    seen: dict[str, set[str]] | None = None,
) -> set[str]:
    """Every message_id in the recent objects of the device's message types.

    seen caches the IDs per key between polls: objects are written once, never changed.
    """
    seen = {} if seen is None else seen
    for prefix in history_prefixes(device_id, now):
        for key in recent_keys(s3_client, bucket, prefix, since):
            if key not in seen:
                body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
                seen[key] = ids_in_object(body)
    found: set[str] = set()
    for ids in seen.values():
        found |= ids
    return found


def check_history(
    s3_client: Any,
    bucket: str,
    device_id: str,
    valid_ids: list[str],
    invalid_ids: list[str],
    started: datetime,
    timeout_s: float | None = None,
    poll_interval_s: float | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
    now: Callable[[], datetime] | None = None,
) -> list[str]:
    """Poll S3 until every valid and invalid ID is found or time runs out."""
    timeout_s = HISTORY_TIMEOUT_S if timeout_s is None else timeout_s
    poll_interval_s = (
        HISTORY_POLL_INTERVAL_S if poll_interval_s is None else poll_interval_s
    )
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    now = now or (lambda: datetime.now(UTC))
    since = started - HISTORY_LOOKBACK
    seen: dict[str, set[str]] = {}
    deadline = clock() + timeout_s
    while True:
        found = read_history_ids(s3_client, bucket, device_id, since, now(), seen)
        failures = evaluate_history(found, valid_ids, invalid_ids)
        if not failures or clock() + poll_interval_s > deadline:
            return failures
        sleep(poll_interval_s)


def _aws_failure(exc: Exception, what: str) -> str:
    from botocore.exceptions import ClientError

    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "unknown")
        return f"could not read {what} ({code})"
    return f"could not read {what} ({type(exc).__name__})"


def report(failures: list[str], out: TextIO | None = None) -> int:
    out = out or sys.stdout
    if not failures:
        print("PASS", file=out)
        return 0
    for failure in failures:
        print(f"FAIL  {failure}", file=out)
    return 1


def main(
    argv: list[str] | None = None,
    client: Any = None,
    s3_client: Any = None,
    sts_client: Any = None,
) -> int:
    started = datetime.now(UTC)
    parser = argparse.ArgumentParser(
        description="Check that smoke-test messages were stored in silvercare-latest"
        " (and, with --history, in the S3 history)."
    )
    parser.add_argument("--device", required=True, help="device ID, e.g. wearable-0001")
    parser.add_argument(
        "--message-ids",
        required=True,
        help="comma-separated IDs of valid messages: each must be stored exactly once",
    )
    parser.add_argument(
        "--invalid-ids",
        default="",
        help="comma-separated IDs of invalid messages: none may be stored in"
        " silvercare-latest, each must be in the history (--history)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="also check that every message reached the S3 history (up to 7 minutes)",
    )
    parser.add_argument(
        "--bucket",
        help="history bucket (default silvercare-history-<account_id>); needs --history",
    )
    args = parser.parse_args(argv)

    try:
        device_type(args.device)
        valid_ids = parse_ids(args.message_ids)
        invalid_ids = parse_ids(args.invalid_ids)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not valid_ids:
        print("error: --message-ids needs at least one ID", file=sys.stderr)
        return 2
    if args.bucket is not None and not args.history:
        print("error: --bucket needs --history", file=sys.stderr)
        return 2
    if args.bucket is not None and not BUCKET_RE.match(args.bucket):
        print("error: --bucket must be an S3 bucket name", file=sys.stderr)
        return 2

    if client is None:
        import boto3  # only when run for real, never at import

        client = boto3.client("dynamodb", region_name=REGION)

    from botocore.exceptions import BotoCoreError, ClientError

    try:
        failures = check(client, DEFAULT_TABLE, args.device, valid_ids, invalid_ids)
    except (ClientError, BotoCoreError) as exc:
        failures = [_aws_failure(exc, DEFAULT_TABLE)]

    if args.history:
        failures.extend(
            history_failures(
                args, valid_ids, invalid_ids, started, s3_client, sts_client
            )
        )
    return report(failures)


def history_failures(
    args: argparse.Namespace,
    valid_ids: list[str],
    invalid_ids: list[str],
    started: datetime,
    s3_client: Any = None,
    sts_client: Any = None,
) -> list[str]:
    """Run the --history check; AWS errors become failures (error code only)."""
    from botocore.exceptions import BotoCoreError, ClientError

    if args.bucket is None and sts_client is None:
        import boto3  # only when run for real, never at import

        sts_client = boto3.client("sts", region_name=REGION)
    try:
        bucket = resolve_bucket(sts_client, args.bucket)
    except (ClientError, BotoCoreError, KeyError, ValueError) as exc:
        return [_aws_failure(exc, "the account ID (sts:GetCallerIdentity)")]
    if s3_client is None:
        import boto3  # only when run for real, never at import

        s3_client = boto3.client("s3", region_name=REGION)
    try:
        return check_history(
            s3_client, bucket, args.device, valid_ids, invalid_ids, started
        )
    except (ClientError, BotoCoreError) as exc:
        return [_aws_failure(exc, bucket)]


if __name__ == "__main__":
    sys.exit(main())
