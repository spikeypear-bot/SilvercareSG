# DynamoDB tables from docs/data-model.md. Change the doc first, then this file.
#
# Capacity is fixed and must stay within the always-free 25 RCU / 25 WCU for all tables
# and indexes together (see "Capacity" in the doc). Current total: 19 RCU / 18 WCU.
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

# Alerts. Written by alert-processor from the alerts queue (built later). No stream.
module "alerts_table" {
  source = "./modules/dynamodb-table"

  name           = "silvercare-alerts"
  hash_key       = "pk"
  range_key      = "sk"
  read_capacity  = 3
  write_capacity = 2

  # Resolved alerts expire 30 days after resolved_at.
  ttl_attribute = "expires_at"

  global_secondary_indexes = [
    {
      # Pattern 9 (all open alerts, newest first). Sparse: open_pk is removed on resolve.
      name               = "open"
      hash_key           = "open_pk"
      range_key          = "sk"
      non_key_attributes = ["alert_type", "device_id", "status", "event_ts"]
      read_capacity      = 2
      write_capacity     = 1
    },
  ]
}
