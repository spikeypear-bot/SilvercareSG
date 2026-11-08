# DynamoDB tables from docs/data-model.md. Change the doc first, then this file.
#
# Capacity is fixed and must stay within the always-free 25 RCU / 25 WCU for all tables
# and indexes together (see "Capacity" in the doc). Current total: 20 RCU / 19 WCU.
#
# Changing a table's name, hash key, or range key destroys the table.

# Residents, thresholds, device assignments, and user <-> resident links.
module "registry_table" {
  source = "./modules/dynamodb-table"

  name           = "silvercare-registry"
  hash_key       = "pk"
  range_key      = "sk"
  read_capacity  = 3
  write_capacity = 1

  global_secondary_indexes = [
    {
      # Patterns 4 and 8 (every device / every resident), photo cleanup.
      name               = "by-type"
      hash_key           = "entity_type"
      range_key          = "pk"
      non_key_attributes = ["display_name", "home_id", "photo_key", "device_type", "resident_id"]
      read_capacity      = 2
      write_capacity     = 1
    },
    {
      # Patterns 10 and 15 (a resident's devices / linked users).
      name               = "by-resident"
      hash_key           = "gsi1_pk"
      range_key          = "gsi1_sk"
      non_key_attributes = ["device_type", "relationship"]
      read_capacity      = 2
      write_capacity     = 1
    },
  ]
}

# Latest 5 messages per device and message type.
module "latest_table" {
  source = "./modules/dynamodb-table"

  name           = "silvercare-latest"
  hash_key       = "pk"
  range_key      = "sk"
  read_capacity  = 4
  write_capacity = 8

  global_secondary_indexes = [
    {
      # Pattern 5 (newest vitals/status/bed/motion of every device). Sparse: fall items
      # have no watch_type. Needs enough WCU for every vitals write, or base-table
      # writes are throttled too.
      name               = "by-message"
      hash_key           = "watch_type"
      range_key          = "pk"
      non_key_attributes = ["device_type", "latest", "latest_ts"]
      read_capacity      = 3
      write_capacity     = 4
    },
  ]
}

# Alerts. Written by alert-processor from the alerts queue (alerts.tf). No stream.
module "alerts_table" {
  source = "./modules/dynamodb-table"

  name           = "silvercare-alerts"
  hash_key       = "pk"
  range_key      = "sk"
  read_capacity  = 3
  write_capacity = 2

  # Resolved and cancelled alerts expire 30 days after resolved_at / cancelled_at.
  ttl_attribute = "expires_at"

  global_secondary_indexes = [
    {
      # Pattern 9 (all open alerts, newest first). Sparse: open_pk is removed on resolve
      # or cancel. Projects every field of the dashboards' alert rows (docs/data-model.md,
      # alerts Index), as an explicit list: never ALL, so details stay out of the index.
      # The watchdog role may read only pk, sk, open_pk, alert_type, and device_id
      # (watchdog.tf), so not ack_note or who acknowledged. Changing this list deletes
      # and recreates the index during the apply: pattern 9 fails until it is active
      # again, so merge such a change in Singapore daytime (docs/data-model.md).
      name      = "open"
      hash_key  = "open_pk"
      range_key = "sk"
      non_key_attributes = [
        "alert_type",
        "device_id",
        "status",
        "event_ts",
        "severity",
        "acknowledged_at",
        "ack_note",
        "acknowledged_by_role",
        "acknowledged_by",
        "escalated_at",
        "notified_at",
      ]
      read_capacity  = 2
      write_capacity = 1
    },
  ]
}

# Results: about 30 nights of each resident's daily summary, copied from S3 summaries/
# by the results Lambda for the doctors' nightly section (docs/features/nightly-results.md;
# patterns 30 and 31). The one exception to "live state only": items expire 30 nights
# after their own (expires_at = 00:00 SGT on the night's date + 31 days). No indexes.
module "results_table" {
  source = "./modules/dynamodb-table"

  name           = "silvercare-results"
  hash_key       = "pk"
  range_key      = "sk"
  read_capacity  = 1
  write_capacity = 1

  ttl_attribute = "expires_at"
}
