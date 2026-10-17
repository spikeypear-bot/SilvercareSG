---
name: iot-metric
description: Add or change an IoT sensor metric end to end - payload contract, MQTT topic, IoT topic rule, ingest Lambda, storage, and device policy. Use when adding a sensor, metric, or MQTT topic, or changing a payload.
argument-hint: [metric-name]
---

# Adding an IoT metric

Metric: $ARGUMENTS

`docs/metrics.md` is the contract between devices and the backend. Change it first and
build everything else from it.

## Steps

1. **Contract.** In `docs/metrics.md`, add or update the metric: topic suffix, fields,
   types, units, send rate, whether it is a regular reading or a one-off event, and an
   example payload. If the change breaks existing devices (a field is removed, renamed,
   or retyped), bump `schema_version` and keep accepting the old version until devices
   are updated.
2. **Storage.** If the metric is stored, follow the `data-model` skill: add the access
   pattern and item shape to `docs/data-model.md`.
3. **Topic rule** (Terraform, `aws_iot_topic_rule`):
   - The SQL selects from the topic pattern in `docs/metrics.md`. Pull IDs from the topic
     with `topic(n)` instead of trusting IDs in the payload.
   - Use a Lambda action when the message needs validation, transformation, or alert
     checks; use a direct DynamoDB action only for pure storage.
   - Always set an `error_action` (e.g. to an S3 bucket or CloudWatch Logs) so dropped
     messages can be inspected.
   - Add `aws_lambda_permission` for principal `iot.amazonaws.com`, with `source_arn` set
     to the rule ARN.
4. **Handler.** Follow `lambda-python`. Validate the payload against the contract, reject
   unknown `schema_version`s, write idempotently, and run any alert checks.
5. **Device policy.** Make sure the IoT policy lets devices publish to the new topic, and
   only their own (see `iam-policy`).
6. **Tests.** Follow `lambda-test`, with a fake event built from the contract's example
   payload.
7. Run `make fmt`, `make tf-check`, and `make test FN=<lambda>`.
8. Summarise the topic, rule, handler, storage, and policy changes.

## Rules

- Never log the payload body. Log the device ID, metric name, and outcome.
- Devices may resend messages and clocks may be wrong. Store both the device timestamp
  and the time the message was received.
